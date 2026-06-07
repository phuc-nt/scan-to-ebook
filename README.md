# scan-to-ebook

Pipeline biến sách giấy đã scan (PNG/JPG/HEIC/HEIF) thành epub đọc trên Books.app, Kindle. OCR qua OpenRouter vision model, post-process bằng Python stdlib, build epub bằng pandoc, upload Drive bằng rclone. HEIC/HEIF (iPhone default) tự convert→JPG tại import stage. Verified zero error trên corpus tiếng Việt cổ (Nam Phong 1917, 75 trang) + 152-image iPhone book (119 HEIC + 33 JPG).

## Quickstart

```bash
brew install pandoc rclone
git clone <repo> ~/workspace/scan-to-ebook && cd ~/workspace/scan-to-ebook
python3 -m venv .venv && .venv/bin/pip install -e .
cp .env.example .env && $EDITOR .env   # paste OPENROUTER_API_KEY=...

# Verify setup: python/pandoc/key ready?
.venv/bin/scan2ebook doctor

# .env tự nạp (không cần source). Tạo inbox rồi chạy:
.venv/bin/scan2ebook init <your-book-slug> --from ~/path/to/scanned-images
.venv/bin/scan2ebook all ~/Books-inbox/<your-book-slug> --smoke  # test 10 trang + estimate cost
# Review smoke epub, confirm at prompt, full run tự tiếp tục
```

## Tài liệu

- [Tổng quan sản phẩm](docs/product-overview.md) — vấn đề, đối tượng, value, non-goals
- [Kiến trúc](docs/architecture.md) — pipeline 4 stage, data flow, design decisions
- [Hướng dẫn người dùng](docs/user-guide.md) — cài đặt, chuẩn bị scan, chạy pipeline, chỉnh sửa
- [Vận hành](docs/operations.md) — cost, OpenRouter credit/key cap, rclone, model swap, debugging
- [Hướng dẫn cho coding agent](AGENTS.md) — interaction protocol cho LLM agent (Claude Code, Cursor)

## Legal

Pipeline cho personal use với sách bạn sở hữu vật lý. Không publish output, không share epub ra ngoài thiết bị cá nhân. Vi phạm copyright là vấn đề người dùng tự chịu.

## Origin

Forked từ Hermes Agent profile prototype (Phase 0-3 Nam Phong 1917 pilot, 5/2026). Standalone vì pipeline thuần stdlib + pandoc + rclone, không cần agent runtime.
