<p align="center">
  <img src="assets/logo-512.png" alt="scan-to-ebook logo" width="160" height="160">
</p>

<h1 align="center">scan-to-ebook</h1>

Turn scanned paper books (PNG/JPG/HEIC/HEIF) into EPUBs you can read in Books.app
or on a Kindle. The pipeline runs OCR through an OpenRouter vision model,
post-processes with the Python standard library, builds the EPUB with pandoc, and
optionally uploads to Google Drive via rclone. HEIC/HEIF (the iPhone default) is
auto-converted to JPG at import — cross-platform, trying `sips` (macOS) →
ImageMagick `magick` → `heif-convert` → `pillow-heif` in order.

Verified with zero OCR errors on hard Vietnamese corpora: an early-Quốc-ngữ
journal (Nam Phong 1917, 75 pages) and a 152-image iPhone-scanned book
(119 HEIC + 33 JPG).

The runtime is **pure Python standard library** — no third-party packages
required (pandoc and rclone are external CLIs).

## Quickstart

```bash
brew install pandoc rclone           # macOS; on Linux use apt/your package manager
git clone <repo> ~/workspace/scan-to-ebook && cd ~/workspace/scan-to-ebook
python3 -m venv .venv && .venv/bin/pip install -e .
cp .env.example .env && $EDITOR .env   # paste OPENROUTER_API_KEY=...

# Verify your setup (python / pandoc / key / HEIC backend):
.venv/bin/scan2ebook doctor

# .env loads automatically (no `source` needed). Create a book, then run it:
.venv/bin/scan2ebook init <your-book-slug> --from ~/path/to/scanned-images
.venv/bin/scan2ebook all <your-book-slug> --smoke   # OCR 10 pages + estimate full cost
# Each book lives at ~/scan2ebook/<your-book-slug>/ with three zones:
#   scans/  (source images — never auto-deleted)
#   work/   (cache: context, OCR text, intermediate book.md)
#   dist/   (the final <slug>.epub)
# Review the smoke EPUB, confirm at the prompt, and the full run continues.
```

Pass `--yes` to skip the confirmation prompt, or `--upload` to push the finished
EPUB to Google Drive (requires a configured rclone remote).

## For agents and automated pipelines

If you are an AI agent (or CI script) handed this repo and asked to "make an
EPUB from the scans in `<folder>`", this is the canonical non-interactive path.

**Prerequisites you cannot set up yourself — verify these first.** Run
`scan2ebook doctor --json` and require every `essential: true` check to report
`ok: true` before spending anything:

| Check | Essential | If missing |
| --- | --- | --- |
| `python` (≥ 3.10) | yes | install Python 3.10+ |
| `pandoc` | yes | `brew install pandoc` (or apt) |
| `openrouter_key` | yes | the user must provide an OpenRouter API key + credit |
| `rclone` | no | only needed for `--upload` |
| `heic_convert` | no | only needed for HEIC/HEIF input (one of sips/magick/heif-convert/pillow-heif) |

The OpenRouter API key, account credit, and the key's spend cap are **user
actions you cannot perform** — they require signup, payment, and dashboard
access. Surface them to the user; do not try to work around them.

**Injecting the key (no interactive editor):** write one `KEY=VALUE` line to
`.env` at the repo root (no `export`, no surrounding quotes), or export it in the
environment. A shell `export` always wins over `.env`.

```bash
printf 'OPENROUTER_API_KEY=%s\n' "$OPENROUTER_API_KEY" > .env   # or set it in the env
```

**Happy path (cost-gated, machine-readable):**

```bash
.venv/bin/scan2ebook doctor --json                       # gate: every essential check ok
.venv/bin/scan2ebook init <slug> --from <folder>          # register the book
.venv/bin/scan2ebook all <slug> --dry-run --json          # estimate cost, no API spend
.venv/bin/scan2ebook all <slug> --smoke --yes --json      # OCR 10 pages, then full run, no prompt
# result EPUB: ~/scan2ebook/<slug>/dist/<slug>.epub  (path also in the JSON `paths` field)
```

`--json` prints one summary object to stdout (human logs go to stderr);
`--json-lines` streams NDJSON events. The summary carries
`status` (`ok`/`partial`/`error`/`smoke`/`dry-run`), `pages`, `cost_usd`, and
`paths`. **Exit codes:** `0` success, `1` partial/failed pages, `2` user error
(bad args, missing input). The run is resumable — re-running after a crash or a
raised credit cap skips already-OCR'd pages, so retrying is cheap and safe.
Override the model with `--model` or the `OCR_MODEL` env var.

## Documentation

- [Product overview](docs/product-overview.md) — problem, audience, value, non-goals
- [Architecture](docs/architecture.md) — pipeline stages, data flow, design decisions
- [User guide](docs/user-guide.md) — install, preparing scans, running the pipeline, editing
- [Operations](docs/operations.md) — cost, OpenRouter credit/key caps, rclone, swapping models, debugging

## Legal

This tool is for personal use with books you physically own. Do not publish its
output or share generated EPUBs beyond your own devices. Copyright compliance is
the user's responsibility. The sample files under [`samples/`](samples/) are short
excerpts included only to demonstrate OCR quality, not a redistribution of any book.

## License

[MIT](LICENSE).
