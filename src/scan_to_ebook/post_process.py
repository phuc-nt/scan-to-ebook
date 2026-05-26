"""Post-process stage: merge per-page .md → pandoc-ready book.md.

KHÔNG dùng LLM — pure Python text fix. Trách nhiệm:
1. Merge tất cả page_NNN.md theo thứ tự filename → 1 file book.md
2. Strip ```markdown wrapper nếu model lỡ thêm
3. Detect chapter heading (CHƯƠNG/Chương/PHẦN/Phần + số La Mã) → promote h1
4. Inject YAML front matter (title, author, lang) cho pandoc epub metadata

Cross-page hyphen-fix INTENTIONALLY DROPPED.
Lý do: corpus Việt cổ dùng hyphen intentional cho từ ghép ("văn-chương",
"nhân-loại"). Auto-nối khi từ rơi đúng biên page → silent corrupt thành
"vănchương". OCR prompt rule 8 đã handle hyphen trong page.
"""

from __future__ import annotations

import re
from pathlib import Path

CHAPTER_PATTERNS = [
    re.compile(r"^\s*(CHƯƠNG\s+[\dIVXLCDM]+.*)$", re.IGNORECASE),
    re.compile(r"^\s*(Chương\s+[\dIVXLCDM]+.*)$"),
    re.compile(r"^\s*(PHẦN\s+[\dIVXLCDM]+.*)$", re.IGNORECASE),
    re.compile(r"^\s*(Phần\s+[\dIVXLCDM]+.*)$"),
]

CODE_FENCE_OPEN = re.compile(r"^```(?:markdown|md)?\s*$")
CODE_FENCE_CLOSE = re.compile(r"^```\s*$")


def strip_code_fences(text: str) -> str:
    """Bỏ ```markdown wrapper ngoài cùng nếu có."""
    lines = text.splitlines()
    if lines and CODE_FENCE_OPEN.match(lines[0]):
        for i in range(len(lines) - 1, 0, -1):
            if CODE_FENCE_CLOSE.match(lines[i]):
                return "\n".join(lines[1:i])
        return "\n".join(lines[1:])
    return text


def upgrade_chapter_headings(text: str) -> str:
    """Detect chapter line, upgrade thành `# Title` (h1, pandoc split point)."""
    out_lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# ") or stripped.startswith("## "):
            if stripped.startswith("## "):
                body = stripped[3:].strip()
                if any(p.match(body) for p in CHAPTER_PATTERNS):
                    out_lines.append(f"# {body}")
                    continue
            out_lines.append(line)
            continue
        matched = False
        for pat in CHAPTER_PATTERNS:
            m = pat.match(stripped)
            if m:
                out_lines.append(f"# {m.group(1).strip()}")
                matched = True
                break
        if not matched:
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
    pages = sorted(input_dir.glob(pattern))
    if not pages:
        raise FileNotFoundError(f"no .md pages found in {input_dir} matching {pattern!r}")

    chunks = []
    for p in pages:
        raw = p.read_text(encoding="utf-8").strip()
        if not raw:
            continue
        chunks.append(strip_code_fences(raw))

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
        "output": str(output_path),
    }
