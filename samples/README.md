# Samples — curated demo outputs

Representative pipeline outputs so you can judge OCR/epub quality before running
your own book. Generated outputs and full books stay local under `output/` (and
are gitignored); this folder is the curated exception.

## Contents

| File | What it is |
|------|------------|
| `demo-scans/page_001.jpg` … `page_010.jpg` | 10 source scan pages (downscaled to 1600px) — the raw input the pipeline OCRs |
| `aragong-q1-quarter-38pages.epub` | EPUB from a quarter run (38 pages) — open in Books.app / Kindle to see the result |
| `aragong-q1-quarter-38pages.md` | Merged book Markdown (OCR text) behind that EPUB |
| `aragong-q1-quarter-context.md` | Context pre-pass output: detected 2-page spreads + proper-name canonicalizations |
| `tho-ngu-ngon-la-fontaine-20pages.epub` | 20-page clean-cache run, default `qwen/qwen3.7-plus` — *Thơ Ngụ-Ngôn* (La Fontaine, tr. Nguyễn Văn Vinh, 1951); old-spelling verse, line breaks preserved ($0.059) |
| `ke-nam-vung-20pages.epub` | 20-page run — *Kẻ Nằm Vùng* (Viet Thanh Nguyen, tr. Lê Tùng Châu); dense modern prose, 60 footnotes ($0.076) |
| `truong-hoc-don-ba-20pages.epub` | 20-page run — *Trường Học Đờn Bà* (André Gide, tr. Bùi Giáng, 2008); blank divider pages correctly skipped ($0.050) |

`demo-scans/` → `*.md` → `*.epub` shows the full input→output chain on real
Vietnamese book pages. The three 20-page EPUBs are real clean-cache runs on the
default model — see the [project README](../README.md#ocr-model) for their
cost/quality numbers and a 100-page estimate.

## Note on content

These samples are excerpts of a copyrighted Vietnamese translation, included
only to demonstrate OCR quality — not a redistribution of the book. Do not use
this tool to publish or share books you do not own. See the project README's
legal note.
