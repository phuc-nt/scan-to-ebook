<p align="center">
  <img src="assets/logo-512.png" alt="scan-to-ebook logo" width="160" height="160">
</p>

<h1 align="center">scan-to-ebook</h1>

<p align="center">
  Turn scanned paper books into clean EPUBs — OCR by a vision LLM, assembled with pandoc.
</p>

<p align="center">
  <img alt="License: MIT" src="https://img.shields.io/badge/License-MIT-blue.svg">
  <img alt="Python 3.10+" src="https://img.shields.io/badge/Python-3.10%2B-3776AB.svg">
  <img alt="Runtime: stdlib only" src="https://img.shields.io/badge/runtime-stdlib--only-success.svg">
</p>

scan-to-ebook converts photos or scans of a paper book (PNG / JPG / HEIC / HEIF),
a PDF file, or a Google Drive file link — into an EPUB you can read in Books.app or on a Kindle.
It runs each page through an OpenRouter vision model for OCR, cleans and merges the
text with the Python standard library, and builds the EPUB with pandoc.

It was built for **hard Vietnamese corpora** and verified with **zero OCR errors**
on an early-Quốc-ngữ journal (Nam Phong, 1917 — 75 pages) and on a 152-image
iPhone-scanned book (119 HEIC + 33 JPG). See the [samples](#ocr-model--samples)
to judge the output before running your own book.

## Features

- **Pure-stdlib runtime** — no third-party Python packages. pandoc and rclone are
  the only external tools, and only pandoc is required.
- **Book-aware OCR** — a context pre-pass reads a handful of pages first to detect
  the title, proper names, spelling conventions, two-page spreads, and the color
  cover (auto-embedded), then feeds that back into every page's prompt for
  consistent results — and keeps cover/back-matter decoration out of the TOC.
- **Image, PDF, or Google Drive input** — a folder of page images, a local PDF, or
  a publicly-shared Drive file link. PDFs (scanned or born-digital with a broken
  text layer) are rendered to per-page images and OCR'd.
- **Cross-platform HEIC/HEIF** — iPhone photos auto-converted at import.
- **Resumable & cost-gated** — already-OCR'd pages are skipped on re-run; a smoke
  run OCRs 10 pages and estimates full cost before you commit.
- **Agent-friendly CLI** — `doctor` self-check, `--dry-run`, `--json` /
  `--json-lines`, and `--yes` for non-interactive runs.

## Quickstart

```bash
brew install pandoc rclone                                   # pandoc required; rclone only for upload
git clone <repo> ~/workspace/scan-to-ebook && cd ~/workspace/scan-to-ebook
python3 -m venv .venv && .venv/bin/pip install -e .
cp .env.example .env && $EDITOR .env                         # set OPENROUTER_API_KEY=...
.venv/bin/scan2ebook doctor                                  # check python / pandoc / key
.venv/bin/scan2ebook init my-book --from <folder | book.pdf | drive-link>
.venv/bin/scan2ebook all my-book --smoke                     # OCR 10, preview, then confirm full run
```

The finished book is at `~/scan2ebook/my-book/dist/my-book.epub`.

Full walkthrough — preparing scans, editing context, manual fixes, upload — is in
the **[User guide](docs/user-guide.md)**. Automating it in CI or from an agent? See
**[For agents and automated pipelines](docs/agents.md)**.

## OCR model & samples

The default OCR model is **`qwen/qwen3.7-plus`**, picked from a benchmark against
`google/gemini-3.1-pro-preview` on a modern translated book and an old-spelling
scan (Nam Phong, 1917). It reads every page (including dense old-text pages where
Gemini blanked or truncated), preserves archaic spelling (`nhời`, `nhơn`,
hyphenation), and costs **~$0.003/page** — roughly **15× cheaper than Gemini** with
no read-failures. A typical 100-page book costs **≈ $0.30–0.40**. The pipeline is
model-agnostic — any OpenRouter vision model works via `--model` or `OCR_MODEL`;
see [Operations → Model swap](docs/operations.md#model-swap) for the full benchmark.

Three finished EPUBs below — each a 20-page clean-cache run on `qwen/qwen3.7-plus`.
Download and open in Books.app / Kindle to judge quality before spending anything:

| Sample (20 pages) | Book | Cost | Highlight |
| --- | --- | --- | --- |
| [`tho-ngu-ngon-la-fontaine-20pages.epub`](samples/tho-ngu-ngon-la-fontaine-20pages.epub) | *Thơ Ngụ-Ngôn* (La Fontaine, 1951) | $0.059 | old-spelling **verse**, line breaks preserved |
| [`ke-nam-vung-20pages.epub`](samples/ke-nam-vung-20pages.epub) | *Kẻ Nằm Vùng* (Viet Thanh Nguyen) | $0.076 | dense modern prose, **60 footnotes** |
| [`truong-hoc-don-ba-20pages.epub`](samples/truong-hoc-don-ba-20pages.epub) | *Trường Học Đờn Bà* (André Gide, 2008) | $0.050 | **blank divider pages** correctly skipped |

See [`samples/README.md`](samples/README.md) for the full input→output chain.
These files are short excerpts included only to demonstrate OCR quality — see the [Legal](#legal) note.

## Documentation

- [Product overview](docs/product-overview.md) — problem, audience, value, non-goals
- [Architecture](docs/architecture.md) — pipeline stages, data flow, design decisions
- [User guide](docs/user-guide.md) — install, preparing scans, running the pipeline, editing
- [For agents](docs/agents.md) — the non-interactive CLI path for CI / agents
- [Operations](docs/operations.md) — cost, OpenRouter credit/key caps, rclone, swapping models, debugging

## Legal

This tool is for personal use with books you physically own. Do not publish its
output or share generated EPUBs beyond your own devices — copyright compliance is
the user's responsibility. The sample files under [`samples/`](samples/) are short
excerpts included only to demonstrate OCR quality, not a redistribution of any book.

## License

[MIT](LICENSE).
