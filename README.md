# scan-to-ebook

Pipeline: scanned book pages (PNG/JPG) → epub readable on iPhone Books.app, Kindle, etc.

- **OCR:** OpenRouter vision model (default `google/gemini-3.1-pro-preview` — verified zero error on Vietnamese 1917 corpus, ~$0.05/page)
- **Post-process:** chapter detection (CHƯƠNG/Chương/PHẦN/Phần + roman numerals) → h1 split, YAML front matter
- **Epub:** pandoc with TOC, optional cover
- **Upload (optional):** rclone → Google Drive

Pipeline thuần stdlib + 2 CLI tool (pandoc, rclone). Không có runtime dependency Python.

## Legal

Pipeline này dùng cho **bản copy cá nhân** từ sách bản quyền của riêng bạn. **KHÔNG** publish epub output, **KHÔNG** chia sẻ ngoài thiết bị cá nhân. Vi phạm copyright là vấn đề của bạn, không phải tác giả pipeline.

## Quickstart

### 1. Install

```bash
git clone <repo> ~/workspace/scan-to-ebook
cd ~/workspace/scan-to-ebook
pip install -e .

# System deps (macOS):
brew install pandoc rclone

# Set API key
cp .env.example .env
$EDITOR .env   # paste OPENROUTER_API_KEY=sk-or-v1-...
```

### 2. (Optional) Configure Drive

```bash
rclone config        # new remote, name=gdrive, type=drive, scope=drive
                     # OAuth browser flow ~3 phút, one-time per machine
```

### 3. Prepare inbox

```
~/Books-inbox/<slug>/
├── page_001.png
├── page_002.png
├── ...
├── metadata.json    # optional
└── cover.jpg        # optional
```

`metadata.json`:
```json
{
  "title": "Nam Phong Tạp Chí — Quyển I (1917)",
  "author": "Phạm Quỳnh (chủ bút)",
  "lang": "vi",
  "year": "1917"
}
```

### 4. Run

```bash
# Full pipeline (OCR + post + epub)
source .env && scan2ebook all ~/Books-inbox/namphong-q01

# With Drive upload
source .env && scan2ebook all ~/Books-inbox/namphong-q01 --upload

# Smoke test 10 pages first
source .env && scan2ebook ocr ~/Books-inbox/namphong-q01 ./output/namphong-q01/ocr --limit 10
```

Output:
```
~/Books-inbox/../output/<slug>/
├── ocr/page_001.md, page_002.md, ...
├── book.md
└── book.epub
```

## Commands

| Command | Purpose |
|---------|---------|
| `scan2ebook ocr <inbox> <out>` | Parallel OCR (4 workers default), resumable, retry on transient |
| `scan2ebook post <ocr-dir> <book.md> --title ...` | Merge pages, detect chapters, YAML metadata |
| `scan2ebook epub <book.md> <book.epub>` | Pandoc → epub, TOC, optional cover |
| `scan2ebook upload <book.epub>` | rclone copy to Drive |
| `scan2ebook all <inbox>` | 3-stage chain (+ `--upload` for stage 4) |

Resumable: re-running `ocr` skips pages có `.md` non-empty trong output dir.

## Costs

Gemini 3.1 Pro Preview, May 2026 pricing: $2.5/M in, $10/M out. Typical Vietnamese A4 scan: ~1421 in / ~4500 out tokens ≈ **$0.05/page**. A 200-page book ≈ **$10**.

## Failure modes

- **HTTP 402 "credit limit"** → raise OpenRouter credit cap, then re-run (resumable picks up where it left off)
- **`empty content (finish_reason=stop)`** trên blank page (cover, divider) → manually placeholder:
  ```bash
  echo '<!-- blank page -->' > output/<slug>/ocr/page_NN.md
  ```
- **HTTP 403 "Key limit exceeded"** → key có per-key hard cap, raise trên OpenRouter dashboard
- **Pandoc duplicate footnote warnings** → non-fatal, output epub vẫn valid

## Origin

Forked from a Hermes Agent skill prototype (Phase 0-3 Nam Phong 1917 pilot, May 2026). Repo standalone vì pipeline không cần agent/Telegram runtime — chỉ stdlib + 2 CLI tool.
