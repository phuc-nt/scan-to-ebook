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

`demo-scans/` → `*.md` → `*.epub` shows the full input→output chain on real
Vietnamese book pages.

## Note on content

These samples are excerpts of a copyrighted Vietnamese translation, included
only to demonstrate OCR quality — not a redistribution of the book. Do not use
this tool to publish or share books you do not own. See the project README's
legal note.
