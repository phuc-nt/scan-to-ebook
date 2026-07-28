# Dead-page fallback OCR bằng subagent Sonnet 5

Re-OCR các trang **dead-placeholder** (deterministic-fail, qwen không đọc được — thường do
provider moderation ảnh mẫu) bằng subagent Claude Sonnet 5. Sonnet đọc ảnh trực tiếp qua Read
tool nên OCR được những trang qwen từ chối.

**Bản chất:** đây KHÔNG phải script CLI. Harness chạy subagent qua **Agent tool**, chỉ hoạt động
*bên trong một phiên Claude Code*, orchestrate bằng **Workflow tool**. Đó là lý do đóng gói ở
dạng workflow `.js` + runbook này, không phải file Python standalone.

## Khi nào dùng

Sau khi batch/`scan2ebook all` báo `DONE(dead=N)` với N > 0 — nghĩa là còn N trang mang
`<!-- OCR FAILED (deterministic) ... -->` trong `work/ocr/page_*.md`. Placeholder là HTML
comment vô hình trong EPUB → sách "DONE" nhưng thiếu nội dung thật ở N trang đó.

**Chỉ dùng cho vài trang khó** (throughput không quan trọng, review tay được). KHÔNG dùng thay
qwen cho cả cuốn — POC đã đo: Sonnet-subagent thua throughput, đốt token nặng, và vẫn có ~0.2%
xác suất rớt trang câm. qwen vẫn là engine batch chính.

## Cách chạy

1. **Điền cấu hình** trong `dead_page_sonnet_fallback.workflow.js`:
   - `BOOK_HOME` = data-root chứa `<slug>/work/ocr` + `<slug>/scans` (vd thư mục books của batch).
   - `SLUGS` = danh sách slug cần re-OCR dead pages.
   - ⚠️ KHÔNG commit đường dẫn drive thật (copyrighted) — điền lúc dùng, sửa lại `<...>` trước khi lưu.

2. **Chạy trong phiên Claude Code:**
   ```
   Workflow({ scriptPath: 'tools/dead_page_sonnet_fallback.workflow.js' })
   ```
   Workflow qua 3 phase: **Scan** (quét dead-placeholder) → **OCR** (1 subagent Sonnet/trang,
   ghi đè `work/ocr/page_NNN.md`) → **Verify** (đếm lại dead còn sót).

3. **Kiểm kết quả return:**
   - `still_dead == 0` → sạch, sang bước rebuild.
   - `still_dead > 0` → còn trang "rớt câm" hoặc vẫn fail: chạy lại workflow (đã ghi thì bỏ qua,
     chỉ còn trang sót), hoặc kiểm tay từng trang trong `list`.

4. **Rebuild EPUB (ngoài phiên, bằng pipeline qwen thường):**
   ```
   scan2ebook all <slug> --yes --skip-prepass
   ```
   (hoặc `scan2ebook post <slug> && scan2ebook epub <slug>`). Không cần OCR lại — trang đã có md.

## Vì sao bước Verify BẮT BUỘC

POC 4 cuốn / 984 trang đo được: prompt "TUYỆT ĐỐI CẤM suy luận trùng/bỏ trang" **triệt được**
failure mode stub-lý-luận (0/835 stub), NHƯNG subagent song song vẫn có ~0.2% xác suất **rớt
trang câm** — không ghi file, không báo lỗi, chỉ *thiếu*. Chỉ đếm-lại-số-file mới phát hiện.
Vì vậy phase Verify đếm dead-placeholder còn sót là chốt chặn cuối, không được bỏ.

## Prompt rules (đồng bộ với base VI prompt)

Prompt trong workflow bám sát `PROMPT` base tiếng Việt ở `src/scan_to_ebook/ocr.py` (giữ dấu,
trung thành bản gốc, đa cột, heading/footnote), CỘNG khối "TUYỆT ĐỐI CẤM" chống tự-bỏ-trang.
Nếu base prompt đổi, đồng bộ lại `RULES` trong workflow.

## Liên quan

- `batch_ocr_runner.py` — driver qwen chính, phân loại `DONE(dead=N)`.
- `src/scan_to_ebook/ocr.py` — `DEAD_PREFIX`, `list_dead_pages()`, cơ chế dead-placeholder.
