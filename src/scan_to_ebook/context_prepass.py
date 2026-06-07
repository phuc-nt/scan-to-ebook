"""Context pre-pass: đọc trước vài trang mẫu (đầu/giữa/cuối) → trích bối cảnh sách.

MỘT lần gọi OpenRouter đa-ảnh (≤15 ảnh) lấy metadata, tên riêng + chính tả chuẩn,
thuật ngữ, mục lục, layout, và `pages_per_image` (LLM TỰ PHÁT HIỆN — 2 cho ảnh trang
đôi, 1 cho ảnh đơn). Render thành một block compact append vào PROMPT để OCR từng
trang nhất quán toàn sách (tên/thuật ngữ/cấu trúc).

Spread (ảnh trang đôi) KHÔNG hardcode vào base PROMPT: chỉ emit trong block này khi
`pages_per_image >= 2`. Sách 1-trang/ảnh tự đúng vì không emit guidance nào.

Resume rule: context.json là source-of-truth, hand-editable. Tồn tại & hợp lệ →
re-derive block bằng render_block, KHÔNG gọi API (cost 0). context.md chỉ là mirror
(không đọc lại khi resume — sửa context.md đơn lẻ bị bỏ qua).

Pre-pass FAIL (API error HOẶC JSON parse fail) → caller phải ABORT pipeline.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from urllib import error as urlerr
from urllib import request as urlreq

from . import ocr

MAX_SAMPLE = 15
CONTEXT_MAX_TOKENS = 8000
_PROPER_NAME_CAP = 40
SAMPLE_MAX_DIM = 1200

CONTEXT_PROMPT = """Bạn phân tích một SÁCH/TẠP CHÍ tiếng Việt qua một số trang mẫu (đầu, giữa, cuối).
Hãy trích bối cảnh dùng cho OCR nhất quán toàn sách.

QUAN TRỌNG — XÁC ĐỊNH SỐ TRANG MỖI ẢNH (pages_per_image): soi từng ảnh mẫu và đếm
số TRANG SÁCH xuất hiện trong MỘT ảnh (thường 1 hoặc 2). Dấu hiệu của TRANG ĐÔI (=2):
ảnh nằm ngang (landscape) có GÁY/đường đóng gáy ở giữa, HAI khối/cột chữ tách biệt, và
HAI số trang ở hai mép ngoài. Nếu chỉ một khối chữ + một số trang → 1. Trả về số nguyên.

Trả về DUY NHẤT một JSON object (không giải thích, không ```json wrapper):
{
  "title": "tên sách nếu thấy, else null",
  "author": "tác giả nếu thấy, else null",
  "translator": "dịch giả nếu thấy (sách dịch), else null",
  "publisher": "nhà xuất bản nếu thấy, else null",
  "year": "năm nếu thấy, else null",
  "pages_per_image": 2,
  "table_of_contents": [{"title": "tên chương/phần", "page": 12}],
  "proper_names": [{"seen": "dạng xuất hiện", "canonical": "chính tả chuẩn nên dùng"}],
  "terminology": ["thuật ngữ/từ vựng cổ hoặc chuyên ngành đặc thù sách này"],
  "layout_notes": "mô tả layout (số cột, heading style, footnote)",
  "footnote_convention": "cách footnote xuất hiện trong sách này",
  "ocr_pitfalls": ["lỗi OCR dễ gặp với font/chính tả sách này"]
}

Quy tắc: GIỮ dấu tiếng Việt + chính tả cổ nguyên văn (vd nhân-loại, chánh). Mảng
rỗng nếu không xác định. proper_names ưu tiên tên riêng lặp lại (người/địa danh).
pages_per_image: theo hướng dẫn XÁC ĐỊNH SỐ TRANG MỖI ẢNH ở trên (số nguyên, 1 hoặc 2).
table_of_contents: chỉ điền nếu thấy MỤC LỤC trong các trang mẫu, else mảng rỗng."""


def select_sample_pages(pages: list[Path]) -> list[Path]:
    """Chọn ≤15 ảnh mẫu: 7 đầu + 4 giữa + 4 cuối, dedup giữ thứ tự.

    `pages` giả định đã natural-sort. ≤15 → trả hết (không dup)."""
    if len(pages) <= MAX_SAMPLE:
        return pages
    first = pages[:7]
    last = pages[-4:]
    mid_start = (len(pages) - 4) // 2
    middle = pages[mid_start:mid_start + 4]
    # dedup giữ thứ tự (dict insertion order) — đề phòng overlap khi sách ngắn-vừa.
    return list(dict.fromkeys(first + middle + last))


def _strip_json_fence(raw: str) -> str:
    """Bóc ```json fence + prose thừa: lấy từ `{` đầu tới `}` cuối."""
    s = raw.strip()
    if s.startswith("```"):
        # bỏ dòng đầu (```json hoặc ```) và fence cuối
        s = s.split("\n", 1)[-1] if "\n" in s else s
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    start = s.find("{")
    end = s.rfind("}")
    if start != -1 and end != -1 and end > start:
        return s[start:end + 1]
    return s


def _encode_sample(path: Path) -> tuple[str, str]:
    """Encode 1 ảnh mẫu cho pre-pass, downscale ~SAMPLE_MAX_DIM bằng sips nếu có.

    Trả (b64, mime). Downscale vào file TẠM (xoá ngay sau encode) → không đụng ảnh
    gốc. sips vắng (Linux/CI) hoặc fail → fallback encode ảnh gốc full-res (có thể
    413 với ảnh lớn, nhưng macOS luôn có sips). Output JPEG để nhẹ + đồng nhất mime."""
    if shutil.which("sips") is None:
        return ocr._encode_image(path), ocr._detect_mime(path)
    tmp_dir = tempfile.mkdtemp(prefix="s2e_prepass_")
    tmp = Path(tmp_dir) / "sample.jpg"
    try:
        result = subprocess.run(
            ["sips", "-s", "format", "jpeg", "-Z", str(SAMPLE_MAX_DIM),
             str(path), "--out", str(tmp)],
            capture_output=True, text=True,
        )
        if result.returncode != 0 or not tmp.exists():
            return ocr._encode_image(path), ocr._detect_mime(path)
        return ocr._encode_image(tmp), "image/jpeg"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _post_context_once(
    api_key: str, model: str, sample_b64s: list[tuple[str, str]], max_tokens: int
) -> tuple[str, dict]:
    """1 POST đa-ảnh (1 text block + N image_url block). Reuse idiom _post_once.

    `sample_b64s`: list (b64, mime). Raises RuntimeError trên HTTP/parse error."""
    content: list[dict] = [{"type": "text", "text": CONTEXT_PROMPT}]
    for b64, mime in sample_b64s:
        content.append(
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
        )
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "temperature": 0.0,
        "max_tokens": max_tokens,
    }
    req = urlreq.Request(
        ocr.OPENROUTER_URL,
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
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"malformed response (JSON parse): {exc} | body[:200]={raw[:200]!r}") from exc
    latency = time.time() - t0
    if "choices" not in body or not body["choices"]:
        err = body.get("error", body)
        raise RuntimeError(f"no choices in response: {json.dumps(err)[:300]}")
    choice = body["choices"][0]
    text = choice.get("message", {}).get("content")
    if text is None or not text.strip():
        raise RuntimeError("empty content from context pre-pass")
    # finish_reason=length → JSON bị cắt giữa chừng (max_tokens quá nhỏ so với
    # reasoning + TOC dài) → parse chắc chắn fail. Báo rõ để user tăng CONTEXT_MAX_TOKENS.
    if choice.get("finish_reason") == "length":
        raise RuntimeError(
            "context response cut off (finish_reason=length) — JSON chưa hoàn chỉnh; "
            f"tăng CONTEXT_MAX_TOKENS (hiện {max_tokens})"
        )
    return text, {"latency_s": round(latency, 2), "usage": body.get("usage", {})}


def _post_and_parse_context_once(
    api_key: str, model: str, sample_b64s: list[tuple[str, str]], max_tokens: int
) -> tuple[dict, dict]:
    """1 POST + parse JSON strict trong CÙNG 1 lần thử → (ctx_dict, meta).

    Gộp POST + parse để retry loop bao cả parse: body cắt/garble (non-empty nhưng
    JSON hỏng) là lỗi transient của provider stream, đáng retry như whitespace body
    (giống robustness của ocr.ocr_page). Parse-fail raise RuntimeError chứa marker
    'parse' để ocr._is_transient bắt được → retry."""
    content, meta = _post_context_once(api_key, model, sample_b64s, max_tokens)
    try:
        ctx = json.loads(_strip_json_fence(content))
    except json.JSONDecodeError as exc:
        # "malformed response" marker → _is_transient = True → retry (body có thể bị
        # cắt giữa chừng do provider stream, lần sau thường lành).
        raise RuntimeError(
            f"malformed response (context JSON parse): {exc} | content[:500]={content[:500]!r}"
        ) from exc
    if not isinstance(ctx, dict) or "title" not in ctx:
        raise RuntimeError("context JSON missing required structure (need dict with 'title')")
    return ctx, meta


def _extract_with_retry(
    api_key: str, model: str, sample_b64s: list[tuple[str, str]], max_tokens: int,
    retries: int = 2,
) -> tuple[dict, dict]:
    """_post_and_parse_context_once + retry transient (malformed/parse-fail/429/5xx/timeout).

    Provider stream đôi khi trả body whitespace HOẶC JSON cắt dở (cả hai transient,
    đáng retry như ocr_page). Reuse ocr._is_transient. Non-transient (4xx/length/
    missing-structure) raise luôn."""
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return _post_and_parse_context_once(api_key, model, sample_b64s, max_tokens)
        except RuntimeError as exc:
            last_exc = exc
            if not ocr._is_transient(str(exc)) or attempt == retries:
                raise
            time.sleep(2 ** attempt + attempt * 0.5)  # 1, 2.5s
    assert last_exc is not None
    raise last_exc


def extract_context(
    api_key: str, model: str, sample_paths: list[Path], max_tokens: int
) -> tuple[dict, dict]:
    """Gọi pre-pass đa-ảnh → strict JSON (POST+parse có retry). Raises nếu non-dict /
    thiếu cấu trúc sau khi hết retry."""
    sample_b64s = [_encode_sample(p) for p in sample_paths]
    ctx, meta = _extract_with_retry(api_key, model, sample_b64s, max_tokens)
    ctx["_generated_by"] = model
    return ctx, meta


def render_block(ctx: dict) -> str:
    """Render block compact append vào PROMPT. Bỏ field rỗng/None.

    Spread block CHỈ emit khi pages_per_image >= 2 (substitute N)."""
    lines = ["--- BỐI CẢNH SÁCH (dùng để OCR nhất quán) ---"]

    title = ctx.get("title")
    if title:
        head = f"Sách: {title}"
        author = ctx.get("author")
        if author:
            head += f" — {author}"
        translator = ctx.get("translator")
        if translator:
            head += f" (dịch: {translator})"
        pub, year = ctx.get("publisher"), ctx.get("year")
        if pub or year:
            head += f" ({', '.join(str(x) for x in (pub, year) if x)})"
        lines.append(head)

    try:
        ppi = int(ctx.get("pages_per_image") or 1)
    except (TypeError, ValueError):
        # context.json có thể bị sửa tay thành giá trị phi số → coi như 1 trang/ảnh
        # thay vì crash với raw traceback (giữ workflow sửa tay an toàn).
        ppi = 1
    lines.append(f"Số trang mỗi ảnh: {ppi}")
    if ppi >= 2:
        lines.append(
            f"ẢNH TRANG ĐÔI: mỗi ảnh có {ppi} trang sách (trái→phải). Đọc HẾT trang trái "
            "rồi trang phải, nối thành một dòng Markdown liên tục; bỏ gáy/ngón tay/nền."
        )

    toc = ctx.get("table_of_contents") or []
    if toc:
        items = [
            f"{t.get('title')} tr.{t.get('page')}"
            for t in toc
            if isinstance(t, dict) and t.get("title")
        ]
        if items:
            lines.append("MỤC LỤC (cấu trúc sách): " + "; ".join(items))

    names = ctx.get("proper_names") or []
    pairs = [
        f"{n.get('seen')}→{n.get('canonical')}"
        for n in names[:_PROPER_NAME_CAP]
        if isinstance(n, dict) and n.get("canonical")
    ]
    if pairs:
        lines.append("Tên riêng (giữ chính tả chuẩn): " + ", ".join(pairs))

    terms = [t for t in (ctx.get("terminology") or []) if t]
    if terms:
        lines.append("Thuật ngữ giữ nguyên: " + ", ".join(terms))

    if ctx.get("layout_notes"):
        lines.append(f"Layout: {ctx['layout_notes']}")
    if ctx.get("footnote_convention"):
        lines.append(f"Footnote: {ctx['footnote_convention']}")
    pitfalls = [p for p in (ctx.get("ocr_pitfalls") or []) if p]
    if pitfalls:
        lines.append("Lưu ý OCR: " + "; ".join(pitfalls))

    return "\n".join(lines)


def save_context(book_dir: Path, ctx: dict, block: str) -> tuple[Path, Path]:
    """Ghi context.json (source-of-truth) + context.md (mirror đã render)."""
    book_dir.mkdir(parents=True, exist_ok=True)
    json_path = book_dir / "context.json"
    md_path = book_dir / "context.md"
    ocr._atomic_write(
        json_path, json.dumps(ctx, ensure_ascii=False, indent=2) + "\n"
    )
    header = "<!-- auto-generated; edit context.json to change OCR injection; this file is a mirror -->\n"
    ocr._atomic_write(md_path, header + block + "\n")
    return json_path, md_path


def load_context(book_dir: Path) -> dict | None:
    """Đọc context.json nếu có & hợp lệ (dict), else None."""
    json_path = book_dir / "context.json"
    try:
        ctx = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return ctx if isinstance(ctx, dict) else None


def run_prepass(
    api_key: str,
    model: str,
    inbox_dir: Path,
    pattern: str,
    max_tokens: int = CONTEXT_MAX_TOKENS,
) -> dict:
    """Orchestrator resume-aware. Returns context/block/cost/tokens/from_cache.

    Cache hit (context.json) → re-derive block, cost 0, KHÔNG gọi API."""
    cached = load_context(inbox_dir)
    if cached is not None:
        return {
            "context": cached,
            "block": render_block(cached),
            "cost_usd": 0.0,
            "tokens_in": 0,
            "tokens_out": 0,
            "from_cache": True,
        }

    pages = sorted(ocr._glob_patterns(inbox_dir, pattern), key=ocr.natural_sort_key)
    if not pages:
        raise RuntimeError("no images for context pre-pass")

    samples = select_sample_pages(pages)
    ctx, meta = extract_context(api_key, model, samples, max_tokens)
    block = render_block(ctx)
    save_context(inbox_dir, ctx, block)

    usage = meta.get("usage", {})
    tokens_in = usage.get("prompt_tokens", 0)
    tokens_out = usage.get("completion_tokens", 0)
    cost = tokens_in / 1e6 * 2.5 + tokens_out / 1e6 * 10.0
    return {
        "context": ctx,
        "block": block,
        "cost_usd": round(cost, 4),
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "from_cache": False,
    }
