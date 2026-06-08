"""OCR stage: scanned page image → markdown via OpenRouter vision model.

Parallel ThreadPoolExecutor, resumable (skip pages có .md non-empty), retry trên
transient HTTP error. Default model `qwen/qwen3.7-plus` — winner benchmark
2026-06-08 trên CẢ sách hiện đại lẫn văn bản cổ (Nam Phong 1917): chất lượng chữ
sòng phẳng Gemini, 0 fail (Gemini blank/cắt vài trang dày), rẻ hơn ~14-15×.

Prompt được verify trên Nam Phong 1917. KHÔNG sửa prompt mà không re-test full
batch — đổi 1 dòng có thể regress chính tả cổ ("văn-chương" → "văn chương").
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from urllib import error as urlerr, request as urlreq

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "qwen/qwen3.7-plus"

# Giá OpenRouter ($/M token in, out) — verify live 2026-06-08. Dùng để ước tính
# cost; nếu model không có trong bảng, fallback giá DEFAULT_MODEL. Provider đổi
# giá thì cập nhật ở đây (1 chỗ duy nhất, cả ocr lẫn context_prepass dùng chung).
MODEL_PRICES: dict[str, tuple[float, float]] = {
    "qwen/qwen3.7-plus": (0.40, 1.60),
    "google/gemini-3.1-pro-preview": (2.5, 10.0),
}


def estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    """Ước tính cost USD theo bảng giá; fallback giá DEFAULT_MODEL nếu model lạ."""
    price_in, price_out = MODEL_PRICES.get(model, MODEL_PRICES[DEFAULT_MODEL])
    return tokens_in / 1e6 * price_in + tokens_out / 1e6 * price_out

# Placeholder ghi cho trang trống thật (giấy trắng/divider).
BLANK_PLACEHOLDER = "<!-- blank page -->"
# Marker error nhận diện trang trống thật: model trả rỗng VÀ finish_reason=stop
# (tự kết thúc, không phải lỗi/cắt). Không retry — retry trang trắng vô ích.
_BLANK_MARKER = "blank page (empty + finish_reason=stop)"

_NUM_RE = re.compile(r"\d+")


def natural_sort_key(path: Path) -> tuple:
    """Sort key tách số trong filename để `page_9` < `page_10` (không lexical).

    Filename không zero-pad (page_5..page_80) → `sorted()` string xếp sai
    (page_10 trước page_5). Tách các cụm số thành int để sort đúng số học.
    Tie-break bằng stem để ổn định khi không có số.
    """
    stem = path.stem
    nums = tuple(int(n) for n in _NUM_RE.findall(stem))
    return (nums, stem)

PROMPT = """Bạn là OCR engine cho sách/tạp chí tiếng Việt.

NHIỆM VỤ: Trích xuất TOÀN BỘ văn bản tiếng Việt trong ảnh này thành Markdown thuần.

QUY TẮC BẮT BUỘC:
1. Giữ NGUYÊN dấu tiếng Việt (ả, ấ, ầ, ẩ, ẫ, ậ, đ, ...). KHÔNG bỏ dấu, KHÔNG đoán sai dấu.
2. Trung thành VỚI BẢN GỐC: chép đúng chính tả hiện trên trang, KHÔNG hiện-đại-hoá, KHÔNG sửa "lỗi". NẾU là văn bản cổ, giữ nguyên chính tả/từ cổ (vd "nhân-loại", "văn-chương", "chánh"); NẾU hiện đại, giữ đúng chính tả hiện hành. Tên riêng/từ nước ngoài giữ y như in.
3. Layout nhiều cột: đọc cột TRÁI trước, cột PHẢI sau (theo thứ tự đọc). Nối liền văn bản, KHÔNG giữ cấu trúc cột.
4. Heading/title: dùng `## ` hoặc `### `.
5. Bullet/numbered list: dùng `- ` hoặc `1. `.
6. Footnote (số nhỏ trên cao): viết `[^N]` inline, footnote body cuối page dạng `[^N]: nội dung`.
7. Bỏ qua header/footer trang chạy (tên sách/chương lặp ở mép trang) và số trang.
8. Hyphen cuối dòng (vd "văn-\\nchương"): nối lại thành "văn-chương".
9. Đoạn văn cách bằng dòng trống.

CHỈ output Markdown. KHÔNG giải thích, KHÔNG ```markdown wrapper, KHÔNG comment thêm.
"""


@dataclass
class PageResult:
    page_path: Path
    markdown: str | None
    latency_s: float
    prompt_tokens: int
    completion_tokens: int
    error: str | None
    is_blank: bool = False  # trang trống thật → ghi placeholder, không tính fail


def _encode_image(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def _atomic_write(dst: Path, text: str) -> None:
    """Ghi qua file tạm rồi os.replace — tránh file nửa-ghi nếu bị kill giữa chừng.

    Resume check dùng size>0; file nửa-ghi non-empty sẽ bị skip → bake corrupt.
    Atomic rename loại bỏ edge case này."""
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, dst)


def _detect_mime(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in (".jpg", ".jpeg"):
        return "image/jpeg"
    if ext == ".webp":
        return "image/webp"
    return "image/png"


def _post_once(
    api_key: str,
    model: str,
    image_b64: str,
    mime: str,
    max_tokens: int,
    prompt_context: str = "",
) -> tuple[str, dict]:
    """1 lần POST, không retry. Raises trên HTTP/parse error với body context.

    `prompt_context` (block bối cảnh sách từ context pre-pass) được append vào base
    PROMPT khi non-empty. Base PROMPT giữ nguyên byte-for-byte."""
    text = PROMPT + ("\n\n" + prompt_context if prompt_context else "")
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": text},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{image_b64}"},
                    },
                ],
            }
        ],
        "temperature": 0.0,
        "max_tokens": max_tokens,
    }
    req = urlreq.Request(
        OPENROUTER_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/phucnt/scan-to-ebook",
            "X-Title": "scan-to-ebook",
        },
        method="POST",
    )
    t0 = time.time()
    try:
        with urlreq.urlopen(req, timeout=300) as resp:
            raw = resp.read().decode("utf-8")
    except urlerr.HTTPError as exc:
        try:
            err_body = exc.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            err_body = "<unreadable>"
        raise RuntimeError(f"HTTP {exc.code} {exc.reason}: {err_body}") from exc
    # Response body đôi khi bị cắt/malformed (provider stream lỗi) → JSONDecodeError.
    # Đây là transient (trang text dày, response lớn dễ đứt), không phải config error.
    # Gắn marker "malformed response" để ocr_page retry thay vì raise luôn.
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"malformed response (JSON parse): {exc} | body[:200]={raw[:200]!r}") from exc
    latency = time.time() - t0

    if "choices" not in body or not body["choices"]:
        err = body.get("error", body)
        raise RuntimeError(f"no choices in response: {json.dumps(err)[:300]}")

    msg = body["choices"][0].get("message", {})
    text = msg.get("content")
    if text is None or not text.strip():
        finish = body["choices"][0].get("finish_reason", "unknown")
        # finish_reason=stop + rỗng = trang trống thật (model xem xong, không có gì).
        # Phân biệt với rỗng do lỗi/cắt (finish khác) → cái sau vẫn transient retry.
        if finish == "stop":
            raise RuntimeError(_BLANK_MARKER)
        raise RuntimeError(f"empty content (finish_reason={finish})")

    usage = body.get("usage", {})
    return text, {"latency_s": round(latency, 2), "usage": usage}


def _is_transient(msg: str) -> bool:
    """Lỗi tạm → đáng retry: 429/5xx/timeout/empty/malformed JSON.

    Blank page (empty + finish_reason=stop) KHÔNG transient — trang trống thật,
    run_batch ghi placeholder. 4xx config/auth cũng không retry.
    """
    return (
        "HTTP 429" in msg
        or "HTTP 5" in msg
        or "timed out" in msg.lower()
        or "empty content" in msg
        or "malformed response" in msg
    ) and _BLANK_MARKER not in msg


def ocr_page(
    api_key: str,
    model: str,
    image_path: Path,
    retries: int = 2,
    max_tokens: int = 12000,
    prompt_context: str = "",
) -> tuple[str, dict]:
    """Single page OCR với retry exponential backoff cho transient error.

    Retry trên 429/5xx/timeout/empty content/malformed JSON. Không retry trên
    4xx khác, cũng không retry blank page (empty+finish_reason=stop) — trang
    trống thật, run_batch sẽ ghi placeholder. `prompt_context` từ context pre-pass
    được thread xuống _post_once."""
    image_b64 = _encode_image(image_path)
    mime = _detect_mime(image_path)
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return _post_once(api_key, model, image_b64, mime, max_tokens, prompt_context)
        except RuntimeError as exc:
            last_exc = exc
            if not _is_transient(str(exc)) or attempt == retries:
                raise
            wait = 2 ** attempt + (attempt * 0.5)  # 1, 2.5, 5s
            time.sleep(wait)
    assert last_exc is not None
    raise last_exc


def _glob_patterns(input_dir: Path, pattern: str) -> list[Path]:
    """Glob 1 hoặc nhiều pattern (phân tách bằng dấu phẩy), dedupe theo path.

    `pattern="*.png,*.jpg,*.jpeg"` → gộp kết quả cả 3 ext, bỏ trùng (file khớp
    nhiều glob), trả list chưa sort. Cho phép `all` quét cả PNG lẫn JPG.
    """
    seen: dict[Path, None] = {}
    for pat in (p.strip() for p in pattern.split(",") if p.strip()):
        for path in input_dir.glob(pat):
            seen[path] = None
    return list(seen)


def collect_pending_pages(
    input_dir: Path, pattern: str, output_dir: Path, limit: int | None
) -> tuple[list[Path], int]:
    """Glob input, sort, filter pages đã có output non-empty. Returns (todo, total).

    `pattern` chấp nhận nhiều glob phân tách dấu phẩy (vd "*.png,*.jpg")."""
    pages = sorted(_glob_patterns(input_dir, pattern), key=natural_sort_key)
    todo = []
    for p in pages:
        md_path = output_dir / f"{p.stem}.md"
        if md_path.exists() and md_path.stat().st_size > 0:
            continue
        todo.append(p)
    if limit is not None:
        todo = todo[:limit]
    return todo, len(pages)


def run_batch(
    *,
    api_key: str,
    input_dir: Path,
    output_dir: Path,
    model: str = DEFAULT_MODEL,
    workers: int = 4,
    pattern: str = "*.png",
    limit: int | None = None,
    max_tokens: int = 12000,
    on_event=None,
    prompt_context: str = "",
) -> dict:
    """Run OCR batch. Returns summary dict.

    `on_event(kind, payload)` — optional callback cho progress logging
    (kind: 'start', 'page_ok', 'page_fail', 'done').
    `prompt_context` — block bối cảnh sách (context pre-pass) append vào PROMPT mỗi trang."""
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    todo, total = collect_pending_pages(input_dir, pattern, output_dir, limit)
    skipped = total - len(todo) if limit is None else 0

    if on_event:
        on_event("start", {"total": total, "skipped": skipped, "todo": len(todo)})

    if not todo:
        return {"ok": 0, "fail": 0, "blank": 0, "skipped": skipped, "total": total, "cost_usd": 0.0}

    total_in = total_out = 0
    ok_count = fail_count = blank_count = 0
    failures: list[tuple[str, str]] = []

    def work(page_path: Path) -> PageResult:
        try:
            md, meta = ocr_page(
                api_key, model, page_path, max_tokens=max_tokens, prompt_context=prompt_context
            )
            usage = meta.get("usage", {})
            return PageResult(
                page_path=page_path,
                markdown=md,
                latency_s=meta["latency_s"],
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                error=None,
            )
        except Exception as exc:
            msg = str(exc)
            if _BLANK_MARKER in msg:
                # Trang trống thật: ghi placeholder, đánh dấu blank (không phải fail).
                return PageResult(
                    page_path=page_path,
                    markdown=BLANK_PLACEHOLDER,
                    latency_s=0,
                    prompt_tokens=0,
                    completion_tokens=0,
                    error=None,
                    is_blank=True,
                )
            return PageResult(
                page_path=page_path,
                markdown=None,
                latency_s=0,
                prompt_tokens=0,
                completion_tokens=0,
                error=msg,
            )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(work, p) for p in todo]
        for fut in as_completed(futures):
            r = fut.result()
            if r.error:
                fail_count += 1
                failures.append((r.page_path.name, r.error))
                if on_event:
                    on_event("page_fail", {"page": r.page_path.name, "error": r.error})
                continue
            dst = output_dir / f"{r.page_path.stem}.md"
            _atomic_write(dst, r.markdown)
            if r.is_blank:
                blank_count += 1
                if on_event:
                    on_event("page_blank", {"page": r.page_path.name, "dst": dst.name})
                continue
            total_in += r.prompt_tokens
            total_out += r.completion_tokens
            ok_count += 1
            if on_event:
                on_event(
                    "page_ok",
                    {
                        "page": r.page_path.name,
                        "latency_s": r.latency_s,
                        "in": r.prompt_tokens,
                        "out": r.completion_tokens,
                        "dst": dst.name,
                    },
                )

    # Cost estimate theo bảng giá MODEL_PRICES (qwen3.7-plus mặc định ~$0.004/page).
    est_cost = estimate_cost(model, total_in, total_out)
    summary = {
        "ok": ok_count,
        "fail": fail_count,
        "blank": blank_count,
        "skipped": skipped,
        "total": total,
        "tokens_in": total_in,
        "tokens_out": total_out,
        "cost_usd": round(est_cost, 4),
        "failures": failures,
    }
    if on_event:
        on_event("done", summary)
    return summary


def require_api_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise SystemExit("OPENROUTER_API_KEY missing in environment")
    return key
