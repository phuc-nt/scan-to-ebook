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
iPhone-scanned book (119 HEIC + 33 JPG). See the [samples](#samples) to judge the
output before running your own book.

- [Features](#features)
- [How it works](#how-it-works)
- [Quickstart](#quickstart)
- [Samples](#samples)
- [For agents and automated pipelines](#for-agents-and-automated-pipelines)
- [Documentation](#documentation)
- [Legal](#legal)
- [License](#license)

## Features

- **Pure-stdlib runtime** — no third-party Python packages to install. pandoc and
  rclone are the only external tools, and only pandoc is required.
- **Book-aware OCR** — a context pre-pass reads a handful of sample pages first to
  detect the title, proper names, spelling conventions, two-page spreads, **and the
  color cover page** (auto-embedded in EPUB), then feeds that back into every page's
  prompt for consistent results. Also prevents cover and back-matter decoration
  (title, publisher, price) from being marked as headings and appearing in the
  table of contents.
- **Image, PDF, or Google Drive file input** — point it at a folder of page images,
  a local PDF file, or a publicly-shared Google Drive file link. PDFs are rendered
  to per-page JPGs at import (`pdftoppm` → `magick` → `sips`, whichever is available).
  Works on both scanned PDFs and born-digital PDFs whose text layer is broken — it
  OCRs the rendered pages, never the garbled text, giving consistent results for both.
  Google Drive file links are automatically downloaded and processed the same way.
- **Cross-platform HEIC/HEIF** — iPhone photos are auto-converted to JPG at import,
  trying `sips` (macOS) → ImageMagick `magick` → `heif-convert` → `pillow-heif`,
  whichever is available.
- **Resumable & cost-gated** — already-OCR'd pages are skipped on re-run (crash or
  raised credit cap = cheap retry); a smoke run OCRs 10 pages and estimates full
  cost before you commit to spending.
- **Agent-friendly CLI** — `doctor` self-check, `--dry-run` cost estimate, `--json`
  / `--json-lines` machine output, and `--yes` for non-interactive runs.

## How it works

Five stages; the last is optional:

```
scans/ ─► Stage 0  context pre-pass   detect title, names, spreads → work/context.json
       ─► Stage 1  OCR                 each page → work/ocr/page_NNN.md  (OpenRouter)
       ─► Stage 2  post-process        clean + merge → work/book.md
       ─► Stage 3  build               pandoc → dist/<slug>.epub
       ─► Stage 4  upload (optional)   rclone → Google Drive
```

Each book lives under a single data-root with three zones:

| Zone | Holds | Lifecycle |
| --- | --- | --- |
| `scans/` | source images + optional `metadata.json`, `cover.jpg` (manual override) | never auto-deleted |
| `work/` | context (incl. auto-detected `cover_page`), per-page OCR, merged `book.md` | safe to `rm -rf` (rebuilds from scans) |
| `dist/` | the final `<slug>.epub` (with auto-detected or manual cover) | the deliverable |

The default location is `~/scan2ebook/<slug>/`; override with `--home` or
`$SCAN2EBOOK_HOME`. Default OCR model is `google/gemini-3.1-pro-preview`.

## Quickstart

```bash
# 1. Install external tools (pandoc required, rclone only for upload)
brew install pandoc rclone           # macOS; on Linux use apt / your package manager

# 2. Install the package
git clone <repo> ~/workspace/scan-to-ebook && cd ~/workspace/scan-to-ebook
python3 -m venv .venv && .venv/bin/pip install -e .

# 3. Add your OpenRouter API key (loads automatically — no `source` needed)
cp .env.example .env && $EDITOR .env   # set OPENROUTER_API_KEY=...

# 4. Check everything is ready (python / pandoc / key / HEIC backend)
.venv/bin/scan2ebook doctor

# 5. Register a book and run it (--from takes an image folder, .pdf, OR a Google Drive file link)
.venv/bin/scan2ebook init my-book --from ~/path/to/scanned-images    # image folder
.venv/bin/scan2ebook init my-book --from ~/path/to/book.pdf          # or: local PDF file
.venv/bin/scan2ebook init my-book --from "https://drive.google.com/file/d/1RAG...nOA/view?usp=drivesdk"  # or: Google Drive file link
.venv/bin/scan2ebook all my-book --smoke
```

`--smoke` OCRs the first 10 pages, builds a preview EPUB, and prints the estimated
full-book cost, then **stops at a confirmation prompt** — review the preview, type
`y`, and the full run continues. Useful extra flags:

- `--yes` — skip the prompt and run the full book straight through (CI / agents).
- `--dry-run` — count pages and estimate cost without calling the API.
- `--upload` — push the finished EPUB to Google Drive (needs a configured rclone remote).
- `--model <id>` — use a different OpenRouter model (or set `OCR_MODEL`).

The finished book is at `~/scan2ebook/my-book/dist/my-book.epub`.

## Samples

Real pipeline input and output, so you can judge quality before spending anything.
A quarter-book run (38 pages) of a Vietnamese translation:

| Sample | What it is |
| --- | --- |
| [`samples/demo-scans/`](samples/demo-scans/) (`page_001.jpg` … `page_010.jpg`) | 10 source scan pages (downscaled) — the raw input |
| [`samples/aragong-q1-quarter-38pages.epub`](samples/aragong-q1-quarter-38pages.epub) | the resulting EPUB — open in Books.app / Kindle |
| [`samples/aragong-q1-quarter-38pages.md`](samples/aragong-q1-quarter-38pages.md) | the merged book Markdown (OCR text) behind that EPUB |
| [`samples/aragong-q1-quarter-context.md`](samples/aragong-q1-quarter-context.md) | the context pre-pass output (detected spreads + proper-name canonicalizations) |

See [`samples/README.md`](samples/README.md) for the full input→output chain.
These files are short excerpts included only to demonstrate OCR quality — see [Legal](#legal).

## For agents and automated pipelines

If you are an AI agent (or CI script) handed this repo and asked to "make an EPUB
from the scans in `<folder>`", this is the canonical non-interactive path.

**1. Verify prerequisites you cannot set up yourself.** Run
`scan2ebook doctor --json` and require every `essential: true` check to report
`ok: true` before spending anything:

| Check | Essential | If missing |
| --- | --- | --- |
| `python` (≥ 3.10) | yes | install Python 3.10+ |
| `pandoc` | yes | `brew install pandoc` (or apt) |
| `openrouter_key` | yes | the user must provide an OpenRouter API key + credit |
| `rclone` | no | only for `--upload` |
| `heic_convert` | no | only for HEIC/HEIF input (one of sips/magick/heif-convert/pillow-heif) |
| `pdf_render` | no | only for PDF input (one of pdftoppm/magick/sips — poppler or imagemagick+ghostscript) |

The API key, account credit, and the key's spend cap require signup, payment, and
dashboard access — **surface these to the user; do not work around them.**

**2. Inject the key without an editor** — write one `KEY=VALUE` line (no `export`,
no quotes) to `.env` at the repo root, or export it. A shell `export` wins over `.env`:

```bash
printf 'OPENROUTER_API_KEY=%s\n' "$OPENROUTER_API_KEY" > .env
```

**3. Run the cost-gated happy path:**

```bash
.venv/bin/scan2ebook doctor --json                  # gate: every essential check ok
.venv/bin/scan2ebook init <slug> --from <folder>    # register the book
.venv/bin/scan2ebook all <slug> --dry-run --json    # estimate cost, no API spend
.venv/bin/scan2ebook all <slug> --smoke --yes --json # OCR 10, then full run, no prompt
# result: ~/scan2ebook/<slug>/dist/<slug>.epub  (also in the JSON `paths` field)
```

`--json` prints one summary object to stdout (human logs go to stderr);
`--json-lines` streams NDJSON events. The summary carries `status`
(`ok`/`partial`/`error`/`smoke`/`dry-run`), `pages`, `cost_usd`, and `paths`.
**Exit codes:** `0` success, `1` partial/failed pages, `2` user error. Runs are
resumable, so retrying after a crash or a raised credit cap is cheap and safe.

## Documentation

- [Product overview](docs/product-overview.md) — problem, audience, value, non-goals
- [Architecture](docs/architecture.md) — pipeline stages, data flow, design decisions
- [User guide](docs/user-guide.md) — install, preparing scans, running the pipeline, editing
- [Operations](docs/operations.md) — cost, OpenRouter credit/key caps, rclone, swapping models, debugging

## Legal

This tool is for personal use with books you physically own. Do not publish its
output or share generated EPUBs beyond your own devices — copyright compliance is
the user's responsibility. The sample files under [`samples/`](samples/) are short
excerpts included only to demonstrate OCR quality, not a redistribution of any book.

## License

[MIT](LICENSE).
