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
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from urllib import error as urlerr, request as urlreq

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "qwen/qwen3.7-plus"

# Timeout 1 request OCR (giây). Trang bình thường trả trong ~5-10s; trang text dày
# cùng lắm ~30-40s. 90s là dư. Trước dùng 300s → khi provider ôm connection im lặng
# (0 byte, không đóng), 1 trang kẹt 300s × 4 retry ≈ 25 phút, kéo cả cuốn theo
# (đo thật batch3 phi-long: 2 cuốn dính 1 trang kẹt mỗi cuốn → 27-29 phút/cuốn).
# Hạ xuống 90s → trang kẹt fail-fast, chuyển sang retry ngay (transient → backoff).
# Worst-case còn 90s × 5 lần ≈ 7.5 phút/trang thay vì 25 phút.
REQUEST_TIMEOUT_S = 90

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

# Placeholder ghi cho trang FAIL deterministic (provider trả rỗng/malformed lặp lại y
# hệt sau retry — không cứu được). Ghi file này để pass sau (ocr retry + all-2) bỏ qua
# thay vì OCR lại từ đầu: 1 trang chết đơn lẻ từng kéo cả cuốn tới 2h+ khi mỗi pass ôm
# lại đủ vòng retry. Placeholder có size>0 nên collect_pending_pages skip; text nêu rõ
# lỗi + "cần xử lý tay" để người sửa sau (hiếm). Trang vẫn tính là FAIL, không phải ok.
DEAD_PLACEHOLDER = "<!-- OCR FAILED (deterministic) — cần xử lý tay: {reason} -->"
# Prefix nhận diện dead-placeholder khi quét lại (pipeline cảnh báo trước khi build).
DEAD_PREFIX = "<!-- OCR FAILED (deterministic)"
# Số lần lặp lại CÙNG error class trong 1 ocr_page call thì abort sớm (khỏi đợi hết
# retries). Trang provider trả deterministic thì retry thêm chỉ tốn thời gian + tiền.
_DETERMINISTIC_ABORT_AFTER = 2


class DeadPageError(RuntimeError):
    """Trang fail DETERMINISTIC — loại DUY NHẤT được ghi DEAD_PLACEHOLDER để pass
    sau skip. Hai đường vào: (1) lỗi content-class (empty/malformed) lặp cùng class
    liên tiếp; (2) HTTP 400 moderation/định-dạng-ảnh (_DEAD_400_MARKERS) ngay lần đầu.

    Tách class riêng vì scope placeholder phải HẸP: fail vì 402 (hết credit),
    403/401 (config), hay 429/5xx/timeout (hạ tầng — kể cả lặp cùng class trong
    burst) đều PHẢI để trang trống cho pass sau / lần chạy lại OCR tiếp —
    placeholder hoá chúng là mất nội dung vĩnh viễn mà mọi tín hiệu downstream
    (fail=0, verify OK) vẫn xanh."""

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


# Prompt OCR riêng cho sách tiếng NHẬT (dọc, đọc phải→trái). KHÔNG dùng quy tắc dấu
# tiếng Việt. Nguồn thường là ẢNH CHỤP MÀN HÌNH app đọc (Kindle…) → có thanh menu hệ
# điều hành + header/footer app + dock; phải BỎ QUA, chỉ lấy vùng chữ thật của sách.
JA_PROMPT = """You are an OCR engine for JAPANESE books (novels, essays, literature).

TASK: Extract ALL Japanese book text in this image into clean Markdown.

THE IMAGE IS A SCREENSHOT of a reading app (e.g. Kindle). IGNORE everything that is
not book body text: the OS menu bar, the app's title/header bar (running book title),
the footer (reading progress, "N% / N minutes left in chapter", page indicators), the
dock, window chrome. Transcribe ONLY the book's own text region.

MANDATORY RULES:
1. Japanese is written VERTICALLY (tategaki) and read TOP→BOTTOM, then columns
   RIGHT→LEFT. Read each column top to bottom; move to the NEXT column to the LEFT.
2. TWO-PAGE SPREAD (landscape image, two separate text blocks with a gutter in the
   middle): this is right-to-left reading order — read the RIGHT page fully FIRST,
   then the LEFT page. Concatenate into continuous text.
3. Reproduce the text EXACTLY as printed: kanji, hiragana, katakana, punctuation
   (。、「」『』…—), and ruby/furigana base text. Do NOT translate, do NOT romanize,
   do NOT modernize kanji. Proper names and foreign words: copy exactly as printed.
4. Furigana (small reading glosses beside kanji): transcribe the MAIN kanji text; you
   may drop the furigana gloss (it is a pronunciation aid, not body text).
5. Chapter/section titles: use `## `.
6. Paragraphs: separate with a blank line. Do NOT hard-wrap inside a paragraph — join
   a paragraph's lines/columns into one continuous line.
7. Skip running headers/footers (book/chapter title repeated at the page edge) and
   page numbers.

Output Markdown ONLY. No explanation, no ```markdown wrapper, no extra comments.
"""


# Registry prompt OCR theo ngôn ngữ. `lang` (từ metadata.json / --lang) chọn prompt;
# thiếu/không khớp → fallback PROMPT tiếng Việt (mặc định, verified artifact). Base
# PROMPT (vi) GIỮ NGUYÊN byte-for-byte; ngôn ngữ mới = THÊM entry, không sửa vi.
PROMPTS: dict[str, str] = {
    "vi": PROMPT,
    "ja": JA_PROMPT,
}


def prompt_for_lang(lang: str | None) -> str:
    """Chọn base prompt OCR theo lang. Lạ/None → PROMPT tiếng Việt (mặc định)."""
    return PROMPTS.get((lang or "vi").strip().lower(), PROMPT)


@dataclass
class PageResult:
    page_path: Path
    markdown: str | None
    latency_s: float
    prompt_tokens: int
    completion_tokens: int
    error: str | None
    is_blank: bool = False  # trang trống thật → ghi placeholder, không tính fail
    is_dead: bool = False   # fail deterministic → ghi placeholder để skip pass sau, VẪN tính fail


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
    lang: str | None = None,
) -> tuple[str, dict]:
    """1 lần POST, không retry. Raises trên HTTP/parse error với body context.

    `prompt_context` (block bối cảnh sách từ context pre-pass) được append vào base
    prompt khi non-empty. `lang` chọn base prompt (vi mặc định, ja cho sách Nhật);
    base prompt mỗi ngôn ngữ giữ nguyên byte-for-byte."""
    text = prompt_for_lang(lang) + ("\n\n" + prompt_context if prompt_context else "")
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
        with urlreq.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
            raw = resp.read().decode("utf-8")
    except urlerr.HTTPError as exc:
        try:
            err_body = exc.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            err_body = "<unreadable>"
        raise RuntimeError(f"HTTP {exc.code} {exc.reason}: {err_body}") from exc
    except (TimeoutError, urlerr.URLError) as exc:
        # Timeout (provider ôm connection im lặng) hoặc lỗi mạng (DNS/reset) → transient.
        # urlopen raise TimeoutError/URLError (KHÔNG phải RuntimeError) → ocr_page sẽ
        # bỏ qua retry nếu không wrap. Đổi thành RuntimeError chứa "timed out" để
        # _is_transient bắt được → ocr_page retry với backoff thay vì fail thẳng.
        reason = getattr(exc, "reason", exc)
        raise RuntimeError(f"request timed out / network error: {reason}") from exc
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


def _error_class(msg: str) -> str:
    """Chuẩn hoá 1 error message về 'lớp lỗi' để so 2 lần fail có cùng nguyên nhân.

    Bỏ phần biến thiên giữa các lần gọi cùng 1 trang: số dòng/cột/char trong lỗi JSON
    (`line 2997 column 1 (char 16478)`) và body snippet (`body[:200]=...`). Nhờ vậy
    2 lần malformed liên tiếp cùng trang → cùng class → coi là deterministic, abort sớm.
    """
    head = msg.split(" | body[:")[0]        # cắt body snippet biến thiên
    return _NUM_RE.sub("#", head)           # số → '#' để bỏ line/col/char


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


# HTTP 400 mang các marker này = lỗi DETERMINISTIC theo NỘI DUNG ảnh: provider
# moderation chặn ảnh (data_inspection_failed — sách chiến tranh/lịch sử hay dính)
# hoặc ảnh sai định dạng. Retry CÙNG ảnh không bao giờ khác kết quả → DeadPageError
# ngay lần đầu (placeholder → pass sau skip → sách VẪN build được, trang tính fail).
# Không có nhánh này, trang kẹt `todo` vĩnh viễn → mọi pass `all` fail>0 → không bao
# giờ ra EPUB (WARN loop vô hạn trong batch — review 2026-07-26 B1). HTTP 400 KHÁC
# (không marker) vẫn fail thường: không đoán bừa nguyên nhân.
_DEAD_400_MARKERS = ("data_inspection_failed", "image format is illegal")


def _is_dead_400(msg: str) -> bool:
    """HTTP 400 content-deterministic (moderation / định dạng ảnh) → đáng DeadPageError."""
    return "HTTP 400" in msg and any(m in msg for m in _DEAD_400_MARKERS)


def _counts_as_deterministic(msg: str) -> bool:
    """Lỗi được phép ĐẾM vào early-abort deterministic: CHỈ content-class.

    'empty content (finish_reason=…)' / 'malformed response': provider đọc cùng ảnh
    trả cùng kết quả hỏng → lặp class = deterministic thật, abort sớm hợp lý.
    429/5xx/timeout là lỗi HẠ TẦNG: burst rate-limit / incident / nghẽn trả lỗi y hệt
    nhau trong 1-2s nên "cùng class 2 lần" KHÔNG chứng minh trang hỏng — phải hưởng
    trọn retry budget và không bao giờ DeadPageError (trang nghẽn tạm bị placeholder
    hoá = mất nội dung vĩnh viễn — review 2026-07-26 B2)."""
    return "empty content" in msg or "malformed response" in msg


def ocr_page(
    api_key: str,
    model: str,
    image_path: Path,
    retries: int = 4,
    max_tokens: int = 12000,
    prompt_context: str = "",
    lang: str | None = None,
) -> tuple[str, dict]:
    """Single page OCR với retry exponential backoff cho transient error.

    Retry trên 429/5xx/timeout/empty content/malformed JSON. Không retry trên
    4xx khác, cũng không retry blank page (empty+finish_reason=stop) — trang
    trống thật, run_batch sẽ ghi placeholder. `prompt_context` từ context pre-pass
    và `lang` (chọn base prompt) được thread xuống _post_once."""
    image_b64 = _encode_image(image_path)
    mime = _detect_mime(image_path)
    last_exc: Exception | None = None
    prev_class: str | None = None
    same_class_count = 0
    for attempt in range(retries + 1):
        try:
            return _post_once(api_key, model, image_b64, mime, max_tokens, prompt_context, lang)
        except RuntimeError as exc:
            last_exc = exc
            msg = str(exc)
            # HTTP 400 content-deterministic (moderation chặn ảnh / định dạng ảnh):
            # retry cùng ảnh không bao giờ khác → DeadPageError NGAY lần đầu để
            # run_batch ghi placeholder (sách vẫn build, trang tính fail). 401/402/403
            # và 400 khác vẫn raise thường (trang trống cho pass sau) — xem docstring
            # DeadPageError.
            if _is_dead_400(msg):
                raise DeadPageError(msg) from exc
            if not _is_transient(msg) or attempt == retries:
                raise
            # Early-abort: CHỈ lỗi content-class (empty/malformed) được đếm — cùng lớp
            # lặp _DETERMINISTIC_ABORT_AFTER lần → trang deterministic-fail, retry thêm
            # vô ích → DeadPageError (run_batch ghi placeholder). 429/5xx/timeout là
            # hạ tầng (burst trả lỗi y hệt trong 1-2s): KHÔNG đếm, hưởng trọn retry;
            # hết vòng raise thường → trang trống, pass sau cứu.
            if _counts_as_deterministic(msg):
                cls = _error_class(msg)
                same_class_count = same_class_count + 1 if cls == prev_class else 1
                prev_class = cls
                if same_class_count >= _DETERMINISTIC_ABORT_AFTER:
                    raise DeadPageError(msg) from exc
            else:
                # Lỗi hạ tầng xen giữa: reset streak — không để 1 lần empty trước đó
                # + 1 lần empty sau chuỗi 429 bị ghép thành "2 lần liên tiếp".
                prev_class, same_class_count = None, 0
            wait = 2 ** attempt + (attempt * 0.5)  # 1, 2.5, 5s
            time.sleep(wait)
    assert last_exc is not None
    raise last_exc


def list_dead_pages(ocr_dir: Path) -> list[str]:
    """Tên trang (stem) đang mang DEAD_PLACEHOLDER trong output dir, natural-sort.

    Để pipeline CẢNH BÁO to trước khi build: placeholder là HTML comment vô hình
    trong EPUB, không báo thì sách 'DONE' mà thiếu nội dung không ai biết
    (fail=0 vì trang 'đã có md', verify zip vẫn OK)."""
    dead = []
    probe_len = len(DEAD_PREFIX) + 8
    for p in sorted(ocr_dir.glob("page_*.md"), key=natural_sort_key):
        try:
            with open(p, encoding="utf-8") as f:
                head = f.read(probe_len)
        except OSError:
            continue
        if head.startswith(DEAD_PREFIX):
            dead.append(p.stem)
    return dead


def _is_sidecar(path: Path) -> bool:
    """File rác của filesystem, KHÔNG phải trang sách.

    macOS ghi lên volume không hỗ trợ metadata gốc (exFAT/FAT/SMB — ổ ngoài, NAS)
    đẻ kèm AppleDouble `._page_001.jpg` cho MỖI file: cùng đuôi ảnh nên lọt glob,
    nhưng ruột là metadata → vision API trả HTTP 400 "image format is illegal".

    Nguy hiểm vì âm thầm: nhân đôi số trang, mỗi trang rác vẫn tính tiền retry và
    đội fail-rate lên ~50% mà chẳng có gì hỏng thật. Cũng bỏ `.DS_Store`,
    `Thumbs.db` (Windows) cho trọn."""
    name = path.name
    return name.startswith("._") or name in {".DS_Store", "Thumbs.db"}


def _glob_patterns(input_dir: Path, pattern: str) -> list[Path]:
    """Glob 1 hoặc nhiều pattern (phân tách bằng dấu phẩy), dedupe theo path.

    `pattern="*.png,*.jpg,*.jpeg"` → gộp kết quả cả 3 ext, bỏ trùng (file khớp
    nhiều glob), trả list chưa sort. Cho phép `all` quét cả PNG lẫn JPG.
    """
    seen: dict[Path, None] = {}
    for pat in (p.strip() for p in pattern.split(",") if p.strip()):
        for path in input_dir.glob(pat):
            if _is_sidecar(path):
                continue
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
    lang: str | None = None,
) -> dict:
    """Run OCR batch. Returns summary dict.

    `on_event(kind, payload)` — optional callback cho progress logging
    (kind: 'start', 'page_ok', 'page_fail', 'done').
    `prompt_context` — block bối cảnh sách (context pre-pass) append vào base prompt
    mỗi trang. `lang` chọn base prompt theo ngôn ngữ (vi mặc định, ja cho sách Nhật)."""
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

    # Circuit breaker 402: credit cạn giữa cuốn → MỌI call còn lại chắc chắn fail
    # 402 (fail-fast nhưng vẫn là 1 HTTP round-trip vô ích × hàng trăm trang × N lane
    # cùng wind-down = hàng nghìn call chết dội API — review 2026-07-26 B3). Trang
    # đầu tiên dính 402 set event; trang chưa gọi API bỏ qua tại chỗ, để trống →
    # resume sau nạp credit OCR tiếp như thường. Message giữ "HTTP 402 Payment
    # Required" để batch driver grep log vẫn nhận diện STOP(402).
    credit_dead = threading.Event()

    def work(page_path: Path) -> PageResult:
        if credit_dead.is_set():
            return PageResult(
                page_path=page_path,
                markdown=None,
                latency_s=0,
                prompt_tokens=0,
                completion_tokens=0,
                error="HTTP 402 Payment Required — bỏ qua không gọi API (credit đã cạn trong batch)",
            )
        try:
            md, meta = ocr_page(
                api_key, model, page_path, max_tokens=max_tokens,
                prompt_context=prompt_context, lang=lang,
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
            if "HTTP 402" in msg:
                credit_dead.set()  # trang sau bỏ qua tại chỗ, không bắn call chết
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
            if isinstance(exc, DeadPageError):
                # CHỈ deterministic-fail mới ghi placeholder (pass sau skip, cắt vòng
                # re-OCR cross-pass — 1 trang chết từng kéo cả cuốn 2h+). Vẫn tính
                # fail (error != None) nên note/summary báo đúng số trang hỏng.
                # Reason cắt 1 dòng/120 ký tự: placeholder nằm trong book.md → vào
                # EPUB dạng HTML comment, không nhét body/request-id của provider.
                reason = _error_class(msg).splitlines()[0][:120]
                return PageResult(
                    page_path=page_path,
                    markdown=DEAD_PLACEHOLDER.format(reason=reason),
                    latency_s=0,
                    prompt_tokens=0,
                    completion_tokens=0,
                    error=msg,
                    is_dead=True,
                )
            # Fail khác (402 hết credit, 403 config, transient hết retry): KHÔNG
            # placeholder — trang phải còn trống để pass retry / lần chạy lại sau
            # nạp credit OCR tiếp. Placeholder hoá chúng = mất nội dung vĩnh viễn
            # mà fail=0 + verify vẫn xanh (bug C1 review 2026-07-26).
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
                # Ghi dead-placeholder (size>0) để collect_pending_pages pass sau skip
                # trang này — cắt vòng re-OCR cross-pass (1 trang chết từng kéo cả cuốn
                # 2h+). Nếu vì lý do gì markdown None thì bỏ qua ghi.
                if r.is_dead and r.markdown is not None:
                    _atomic_write(output_dir / f"{r.page_path.stem}.md", r.markdown)
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
