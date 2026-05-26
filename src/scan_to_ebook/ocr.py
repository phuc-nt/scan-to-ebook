"""OCR stage: scanned page image → markdown via OpenRouter vision model.

Parallel ThreadPoolExecutor, resumable (skip pages có .md non-empty), retry trên
transient HTTP error. Default model `google/gemini-3.1-pro-preview` — winner
Phase 0 spike trên corpus Việt cổ (Nam Phong 1917, 0 lỗi, ~$0.05/page).

Prompt được verify trên Nam Phong 1917. KHÔNG sửa prompt mà không re-test full
batch — đổi 1 dòng có thể regress chính tả cổ ("văn-chương" → "văn chương").
"""

from __future__ import annotations

import base64
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from urllib import error as urlerr, request as urlreq

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "google/gemini-3.1-pro-preview"

PROMPT = """Bạn là OCR engine cho sách/tạp chí tiếng Việt.

NHIỆM VỤ: Trích xuất TOÀN BỘ văn bản tiếng Việt trong ảnh này thành Markdown thuần.

QUY TẮC BẮT BUỘC:
1. Giữ NGUYÊN dấu tiếng Việt (ả, ấ, ầ, ẩ, ẫ, ậ, đ, ...). KHÔNG bỏ dấu, KHÔNG đoán sai dấu.
2. Giữ chính tả/từ vựng cổ NGUYÊN VĂN nếu có (vd: "nhân-loại", "văn-chương", "chánh" thay vì sửa thành "chính"). Đây là văn bản cổ.
3. Layout 2 cột: đọc cột TRÁI trước, cột PHẢI sau. Nối liền văn bản, KHÔNG giữ cấu trúc cột.
4. Heading/title: dùng `## ` hoặc `### `.
5. Bullet/numbered list: dùng `- ` hoặc `1. `.
6. Footnote (số nhỏ trên cao): viết `[^N]` inline, footnote body cuối page dạng `[^N]: nội dung`.
7. Bỏ qua header trang (vd "NAM PHONG") và số trang.
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


def _encode_image(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def _detect_mime(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in (".jpg", ".jpeg"):
        return "image/jpeg"
    if ext == ".webp":
        return "image/webp"
    return "image/png"


def _post_once(api_key: str, model: str, image_b64: str, mime: str, max_tokens: int) -> tuple[str, dict]:
    """1 lần POST, không retry. Raises trên HTTP/parse error với body context."""
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT},
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
            body = json.loads(resp.read().decode("utf-8"))
    except urlerr.HTTPError as exc:
        try:
            err_body = exc.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            err_body = "<unreadable>"
        raise RuntimeError(f"HTTP {exc.code} {exc.reason}: {err_body}") from exc
    latency = time.time() - t0

    if "choices" not in body or not body["choices"]:
        err = body.get("error", body)
        raise RuntimeError(f"no choices in response: {json.dumps(err)[:300]}")

    msg = body["choices"][0].get("message", {})
    text = msg.get("content")
    if text is None or not text.strip():
        finish = body["choices"][0].get("finish_reason", "unknown")
        raise RuntimeError(f"empty content (finish_reason={finish})")

    usage = body.get("usage", {})
    return text, {"latency_s": round(latency, 2), "usage": usage}


def ocr_page(
    api_key: str,
    model: str,
    image_path: Path,
    retries: int = 2,
    max_tokens: int = 8000,
) -> tuple[str, dict]:
    """Single page OCR với retry exponential backoff cho transient error.

    Retry trên 429/5xx/timeout/empty content. Không retry trên 4xx khác."""
    image_b64 = _encode_image(image_path)
    mime = _detect_mime(image_path)
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return _post_once(api_key, model, image_b64, mime, max_tokens)
        except RuntimeError as exc:
            last_exc = exc
            msg = str(exc)
            transient = (
                "HTTP 429" in msg
                or "HTTP 5" in msg
                or "timed out" in msg.lower()
                or "empty content" in msg
            )
            if not transient or attempt == retries:
                raise
            wait = 2 ** attempt + (attempt * 0.5)  # 1, 2.5, 5s
            time.sleep(wait)
    assert last_exc is not None
    raise last_exc


def collect_pending_pages(
    input_dir: Path, pattern: str, output_dir: Path, limit: int | None
) -> tuple[list[Path], int]:
    """Glob input, sort, filter pages đã có output non-empty. Returns (todo, total)."""
    pages = sorted(input_dir.glob(pattern))
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
    max_tokens: int = 8000,
    on_event=None,
) -> dict:
    """Run OCR batch. Returns summary dict.

    `on_event(kind, payload)` — optional callback cho progress logging
    (kind: 'start', 'page_ok', 'page_fail', 'done')."""
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    todo, total = collect_pending_pages(input_dir, pattern, output_dir, limit)
    skipped = total - len(todo) if limit is None else 0

    if on_event:
        on_event("start", {"total": total, "skipped": skipped, "todo": len(todo)})

    if not todo:
        return {"ok": 0, "fail": 0, "skipped": skipped, "total": total, "cost_usd": 0.0}

    total_in = total_out = 0
    ok_count = fail_count = 0
    failures: list[tuple[str, str]] = []

    def work(page_path: Path) -> PageResult:
        try:
            md, meta = ocr_page(api_key, model, page_path, max_tokens=max_tokens)
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
            return PageResult(
                page_path=page_path,
                markdown=None,
                latency_s=0,
                prompt_tokens=0,
                completion_tokens=0,
                error=str(exc),
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
            dst.write_text(r.markdown, encoding="utf-8")
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

    # Cost estimate Gemini 3.1 Pro Preview: $2.5/M in, $10/M out (OpenRouter, May 2026).
    # Tune lại nếu provider đổi giá. Phase 0 đo ~$0.05/page với 1 ảnh A4.
    est_cost = total_in / 1e6 * 2.5 + total_out / 1e6 * 10.0
    summary = {
        "ok": ok_count,
        "fail": fail_count,
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
