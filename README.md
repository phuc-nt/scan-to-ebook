# scan-to-ebook

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
