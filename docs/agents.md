# For agents and automated pipelines

If you are an AI agent (or CI script) handed this repo and asked to "make an EPUB
from the scans in `<folder>`", this is the canonical non-interactive path.

## 1. Verify prerequisites you cannot set up yourself

Run `scan2ebook doctor --json` and require every `essential: true` check to
report `ok: true` before spending anything:

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

## 2. Inject the key without an editor

Write one `KEY=VALUE` line (no `export`, no quotes) to `.env` at the repo root, or
export it. A shell `export` wins over `.env`:

```bash
printf 'OPENROUTER_API_KEY=%s\n' "$OPENROUTER_API_KEY" > .env
```

## 3. Run the cost-gated happy path

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
