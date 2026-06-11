"""Post-process stage: merge per-page .md → pandoc-ready book.md.

KHÔNG dùng LLM — pure Python text fix. Trách nhiệm:
1. Merge tất cả page_NNN.md theo thứ tự filename → 1 file book.md
2. Strip ```markdown wrapper nếu model lỡ thêm
3. Renumber footnote cross-page (mỗi page đánh [^1] độc lập → shift theo counter)
4. Detect chapter heading (CHƯƠNG/Chương/PHẦN/Phần/HỒI + số La Mã/Ả Rập/chữ) → h1
5. Inject YAML front matter (title, author, lang) cho pandoc epub metadata

Cross-page hyphen-fix INTENTIONALLY DROPPED.
Lý do: corpus Việt cổ dùng hyphen intentional cho từ ghép ("văn-chương",
"nhân-loại"). Auto-nối khi từ rơi đúng biên page → silent corrupt thành
"vănchương". OCR prompt rule 8 đã handle hyphen trong page.
"""

from __future__ import annotations

import re
from pathlib import Path

from .ocr import natural_sort_key

# Số viết chữ tiếng Việt cho heading (Hồi/Chương/Phần thứ <chữ>).
# Anchor bằng các từ này để tránh false-positive ("Phần lớn", "Hồi đó").
_VN_ORDINAL = (
    r"(?:nhất|nhị|tam|tứ|ngũ|lục|thất|bát|cửu|thập"
    r"|một|hai|ba|bốn|năm|sáu|bảy|tám|chín|mười"
    r"|mở\s+đầu|kết|cuối|chót)"
)
# Sau từ khoá: hoặc số (La Mã / Ả Rập), hoặc "thứ <chữ>", hoặc trực tiếp <chữ>.
_HEADING_NUM = rf"(?:[\dIVXLCDM]+|thứ\s+{_VN_ORDINAL}|{_VN_ORDINAL})"
_KEYWORDS = r"(?:CHƯƠNG|Chương|PHẦN|Phần|HỒI|Hồi|THIÊN|Thiên|QUYỂN|Quyển)"

# Đuôi hợp lệ SAU keyword+số: hết dòng, HOẶC dấu câu tiêu đề (: . - —) rồi tiêu đề.
# Tiêu đề sau dấu phải không bắt đầu bằng chữ THƯỜNG tiếng Việt (văn xuôi "...của",
# "...với" → loại). Chặn `.*` cũ nuốt cả đoạn văn mở bằng "Phần thứ hai...".
# Lưu ý: nhoa/thường xét THỦ CÔNG (không IGNORECASE) vì IGNORECASE phá phân biệt này.
_LOWER_VN = "a-zàáảãạăằắẳẵặâầấẩẫậeèéẻẽẹêềếểễệiìíỉĩịoòóỏõọôồốổỗộơờớởỡợuùúủũụưừứửữựyỳýỷỹỵđ"
_HEADING_TAIL = rf"(?:\s*$|\s*[:.\-–—]\s*[^{_LOWER_VN}\s].*$)"
# Độ dài tối đa cả dòng heading — heading thật ngắn; đoạn văn dài thì loại.
_HEADING_MAX_LEN = 80

CHAPTER_PATTERNS = [
    re.compile(rf"^\s*({_KEYWORDS}\s+{_HEADING_NUM}\b{_HEADING_TAIL})"),
]


def _is_chapter_heading(line: str) -> bool:
    """True nếu `line` là dòng heading chương thật (không phải văn xuôi mở bằng từ khoá).

    Kết hợp 2 lớp chặn false-positive:
    1. Độ dài: heading thật ngắn (≤ _HEADING_MAX_LEN). Đoạn văn 400 chữ → loại.
    2. Đuôi hợp lệ: sau keyword+số phải hết dòng hoặc dấu câu tiêu đề + tiêu đề
       (không bắt đầu bằng chữ thường tiếng Việt). "Phần thứ hai của..." → loại.
    """
    stripped = line.strip()
    if len(stripped) > _HEADING_MAX_LEN:
        return False
    return any(p.match(stripped) for p in CHAPTER_PATTERNS)

CODE_FENCE_OPEN = re.compile(r"^```(?:markdown|md)?\s*$")
CODE_FENCE_CLOSE = re.compile(r"^```\s*$")

# Footnote markdown: ref `[^1]` trong body, def `[^1]:` đầu dòng.
_FOOTNOTE_REF = re.compile(r"\[\^(\d+)\]")


def renumber_footnotes(text: str, offset: int) -> tuple[str, int]:
    """Cộng `offset` vào mọi số footnote trong page → unique sau khi merge.

    Mỗi page OCR đánh footnote độc lập từ [^1]; merge thẳng sẽ đụng [^1] trùng
    (pandoc chỉ giữ note đầu, "Duplicate note reference"). Shift mỗi page theo
    counter chạy. Returns (text mới, số note distinct trong page) để cộng dồn.

    offset=0 → no-op (giữ nguyên), cho page đầu / page không footnote.
    Bỏ qua [^N] nằm trong fenced code block (``` … ```) — đó là literal code,
    không phải footnote thật.
    """
    seen = set()

    def _shift(m: re.Match) -> str:
        n = int(m.group(1))
        seen.add(n)
        return f"[^{n + offset}]"

    out_lines = []
    in_fence = False
    for line in text.splitlines():
        if CODE_FENCE_OPEN.match(line) or CODE_FENCE_CLOSE.match(line):
            in_fence = not in_fence
            out_lines.append(line)
            continue
        out_lines.append(line if in_fence else _FOOTNOTE_REF.sub(_shift, line))
    return "\n".join(out_lines), (max(seen) if seen else 0)


def strip_code_fences(text: str) -> str:
    """Bỏ ```markdown wrapper ngoài cùng nếu có."""
    lines = text.splitlines()
    if lines and CODE_FENCE_OPEN.match(lines[0]):
        for i in range(len(lines) - 1, 0, -1):
            if CODE_FENCE_CLOSE.match(lines[i]):
                return "\n".join(lines[1:i])
        return "\n".join(lines[1:])
    return text


# ATX heading thiếu space sau dấu #: `##幽霊の家` (model CJK hay bỏ space ASCII).
# CommonMark BẮT BUỘC space sau # → không có thì pandoc render thành text thường,
# mất heading + mất split point. Chuẩn hoá `#`/`##` (≤6) liền ký tự non-# → chèn space.
_ATX_NO_SPACE = re.compile(r"^(#{1,6})(?=[^#\s])")


def _normalize_atx_heading(stripped: str) -> str:
    """`##幽霊の家` → `## 幽霊の家`. Dòng không phải ATX heading: trả nguyên."""
    return _ATX_NO_SPACE.sub(r"\1 ", stripped)


def upgrade_chapter_headings(text: str) -> str:
    """Detect chapter line, upgrade thành `# Title` (h1, pandoc split point)."""
    out_lines = []
    for line in text.splitlines():
        stripped = _normalize_atx_heading(line.strip())
        if stripped.startswith("# ") or stripped.startswith("## "):
            if stripped.startswith("## "):
                body = stripped[3:].strip()
                if _is_chapter_heading(body):
                    out_lines.append(f"# {body}")
                    continue
            # giữ heading đã chuẩn-hoá (vd `##幽霊の家`→`## 幽霊の家`), KHÔNG dùng line gốc.
            out_lines.append(stripped)
            continue
        if _is_chapter_heading(stripped):
            out_lines.append(f"# {stripped}")
        else:
            out_lines.append(line)
    return "\n".join(out_lines)


def build_front_matter(title: str, author: str | None, lang: str, year: str | None) -> str:
    """Pandoc YAML front matter cho epub metadata."""
    lines = ["---", f"title: {title}"]
    if author:
        lines.append(f"author: {author}")
    lines.append(f"lang: {lang}")
    if year:
        lines.append(f"date: {year}")
    lines.append("---\n")
    return "\n".join(lines)


def merge_pages(
    *,
    input_dir: Path,
    output_path: Path,
    title: str,
    author: str | None = None,
    lang: str = "vi",
    year: str | None = None,
    pattern: str = "page_*.md",
) -> dict:
    pages = sorted(input_dir.glob(pattern), key=natural_sort_key)
    if not pages:
        raise FileNotFoundError(f"no .md pages found in {input_dir} matching {pattern!r}")

    chunks = []
    footnote_offset = 0
    for p in pages:
        raw = p.read_text(encoding="utf-8").strip()
        if not raw:
            continue
        cleaned = strip_code_fences(raw)
        cleaned, page_max = renumber_footnotes(cleaned, footnote_offset)
        footnote_offset += page_max
        chunks.append(cleaned)

    merged = "\n\n".join(chunks)
    merged = upgrade_chapter_headings(merged)

    h1_count = sum(1 for line in merged.splitlines() if line.startswith("# "))
    h2_count = sum(1 for line in merged.splitlines() if line.startswith("## "))

    fm = build_front_matter(title, author, lang, year)
    final = fm + "\n" + merged + "\n"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(final, encoding="utf-8")

    return {
        "pages_merged": len(pages),
        "chars": len(final),
        "h1": h1_count,
        "h2": h2_count,
        "footnotes": footnote_offset,
        "output": str(output_path),
    }
