#!/usr/bin/env python3
"""Dọn rác TOC trong book.md sau OCR — batch nhiều cuốn, tham số hoá.

OCR biến trang "MỤC LỤC" của sách thành heading (pandoc --toc tự sinh mục lục
→ block này là rác trùng) và chữ trên bìa thành `## <title>` / `## <author>`.
Tool này dọn CHỈ 2 loại chắc chắn an toàn (heuristics đã verify trên ~180 cuốn):

1. Block 'MỤC LỤC': từ heading mục lục đến heading kế tiếp — xoá.
2. Heading trùng title/author MÀ body RỖNG (dòng kế — bỏ dòng trống — lại là
   heading khác hoặc hết file) — hạ thành text thường. Yêu cầu body rỗng THẬT
   (không phải ngưỡng <40 ký tự) để không xoá nhầm chương thật trùng tên sách
   (vd tuyển tập đặt tên theo 1 truyện).

Backup `<book.md>.bak` cạnh file gốc. In danh sách slug đã sửa — rebuild EPUB
các cuốn đó từ book.md là miễn phí (không OCR lại):
    scan2ebook epub books/<slug>/work/book.md books/<slug>/dist/<slug>.epub
hoặc `scan2ebook all <slug> --yes` (0 trang cần OCR → tự bỏ pre-pass).

Cách chạy:
    python3 tools/fix_toc_junk.py --home <books-dir> --csv <slugs.csv>
    python3 tools/fix_toc_junk.py --home <books-dir> --slugs sach-mot,sach-hai \\
        --title "Tên Sách" --authors "Tác Giả A;Tác Giả B"

CSV cần cột `slug,title,authors` (cột khác bỏ qua; field có dấu phẩy phải
quote chuẩn CSV — tool đọc bằng csv module, không split tay).
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import unicodedata
from pathlib import Path


def _norm(s: str) -> str:
    return unicodedata.normalize("NFC", s).strip().lower()


TOC_HEADINGS = {"mục lục", "muc luc"}


def clean_book_md(text: str, title: str, authors: list[str]) -> tuple[str, int, list[str]]:
    """Trả (text_mới, số_block_MỤC_LỤC_xoá, list_heading_đã_hạ). Thuần, test được."""
    title_n = _norm(title)
    authors_n = [_norm(a) for a in authors if a.strip()]
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    removed_toc = 0
    demoted: list[str] = []
    while i < len(lines):
        line = lines[i]
        if line.startswith("## "):
            heading = line[3:].strip()
            heading_n = _norm(heading)
            if heading_n in TOC_HEADINGS:
                removed_toc += 1
                i += 1
                # dừng ở BẤT KỲ heading nào (kể cả '# ' h1) — không nuốt lố block sau
                while i < len(lines) and not lines[i].startswith("#"):
                    i += 1
                continue
            # body rỗng = dòng kế (bỏ dòng trống) cũng là '## ' hoặc hết file
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            body_empty = j >= len(lines) or lines[j].startswith("## ")
            if (heading_n == title_n or heading_n in authors_n) and body_empty:
                out.append(heading)  # hạ heading → text thường
                demoted.append(heading)
                i += 1
                continue
        out.append(line)
        i += 1
    return "\n".join(out) + "\n", removed_toc, demoted


def _split_authors(raw: str) -> list[str]:
    return [a for a in re.split(r"[;,]", raw or "") if a.strip()]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--home", type=Path, required=True, help="thư mục chứa các book-home (<home>/<slug>/work/book.md)")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--csv", type=Path, help="CSV có cột slug,title,authors")
    src.add_argument("--slugs", help="danh sách slug phân tách dấu phẩy (cần kèm --title/--authors nếu muốn hạ heading bìa)")
    ap.add_argument("--title", default="", help="(chỉ với --slugs) title để so heading bìa")
    ap.add_argument("--authors", default="", help="(chỉ với --slugs) authors, phân tách ';' hoặc ','")
    args = ap.parse_args(argv)

    if args.csv:
        with open(args.csv, encoding="utf-8") as f:
            books = [(r["slug"], r.get("title", ""), _split_authors(r.get("authors", "")))
                     for r in csv.DictReader(f) if r.get("slug")]
    else:
        slugs = [s.strip() for s in args.slugs.split(",") if s.strip()]
        books = [(s, args.title, _split_authors(args.authors)) for s in slugs]

    changed: list[str] = []
    for slug, title, authors in books:
        book_md = args.home / slug / "work" / "book.md"
        if not book_md.exists():
            continue
        original = book_md.read_text(encoding="utf-8")
        cleaned, removed_toc, demoted = clean_book_md(original, title, authors)
        if removed_toc or demoted:
            book_md.with_suffix(".md.bak").write_text(original, encoding="utf-8")
            book_md.write_text(cleaned, encoding="utf-8")
            changed.append(slug)
            print(f"{slug}: -{removed_toc} MỤC LỤC, hạ {demoted}")

    print(f"\n{len(changed)} cuốn sửa")
    print("SLUGS:" + " ".join(changed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
