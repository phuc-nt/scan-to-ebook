# Prose Pipeline — Run History & Automation Maturity (qua 5 cuốn thật)

**Date:** 2026-06-09
**Severity:** Note (operational track record, không phải incident)
**Component:** OCR-prose pipeline (`scan2ebook all`)
**Status:** Stable — sửa tay hội tụ về 0

---

## Vì sao ghi

Vòng use→log→improve (CLAUDE.md) đã chạy qua 5 cuốn sách chữ thật. Đây là bản
tổng kết "pipeline tự động tới đâu" — đọc các pipeline-log rời trong
`my-ebook-store/library/pipeline-logs/` thì thấy từng cuốn, nhưng KHÔNG thấy
xu hướng. Note này chốt xu hướng để lần sau khỏi suy lại.

## Track record (theo thời gian)

| Cuốn | Trang | Model | Worker | OCR fail | Sửa tay | Chi phí |
|------|-------|-------|--------|----------|---------|---------|
| Chuyến Thư Miền Nam | 143 | Gemini 3.1 Pro | 4 | 0 | **5 dòng** (TOC bìa/colophon) | ~$3.97 |
| Kẻ Nằm Vùng | 429 | Gemini 3.1 Pro | 4 | 0 | 0 | ~$12.85 |
| Thơ Ngụ-Ngôn La Fontaine | 116 | Gemini 3.1 Pro | 4 | 0 | 0 | ~$3.27 |
| Tác Phẩm Aragông | 153 | **qwen3.7-plus** | — | 0 | 0 | ~$1.22 |
| Trường Học Đờn Bà | 331 | **qwen3.7-plus** | **12** | 0 | 0 | **~$1.15** |

**Hai trục cải thiện rõ:**
1. **Sửa tay: 5 → 0 → 0 → 0 → 0.** Cuốn đầu (Chuyến Thư) lộ bug OCR gán heading
   nhầm cho trang bìa/colophon → fix tại nguồn (`633f4f0`, pre-pass cấm heading
   trên cover/colophon). 4 cuốn sau **0 dòng**.
2. **Chi phí: ~8× rẻ** sau khi đổi default Gemini 3.1 Pro → qwen3.7-plus. Trường
   Học Đờn Bà rebuild cùng cuốn: $9.04 (cũ) → $1.15 (mới). Workers 4→12 nuốt gọn
   tail-latency của trang token-stutter (page_327: out 12.7k token, 233s, không
   khoá batch vì 11 worker khác chạy tiếp).

## Công đoạn đã tự động sạch (không cần can thiệp)

- **OCR** — 0 fail/cuốn sau retry (retries=4 chịu 12-worker, validated trên 331 trang).
  Transient fail tự retry (Kẻ Nằm Vùng: 1 lần). Token-stutter không chặn build.
- **Tự dò bìa màu** — pre-pass `cover_page` tự nhúng ảnh bìa.
- **Metadata backfill** — tự điền title/author/year/translator thật vào
  `scans/metadata.json` (fix bug slug-in-TOC).
- **Drive-ingest** — tải scan từ Drive folder (Kẻ Nằm Vùng cuốn đầu dùng).
- **Verse + bilingual heading** — pre-pass tự ngắt dòng thơ (La Fontaine: 2131
  `<br/>`) + ép title cấp `##` để `--toc-depth=2` bắt tựa song ngữ.

## Lỗi cosmetic còn bỏ ngỏ (không chặn cấu trúc, chưa fix)

- **Heading dính chữ** kiểu `## 26 thángmười`, `## GENEVIÈVEhay là …` (thiếu space
  giữa số/chữ) — Trường Học Đờn Bà. Ứng viên: post-process rule chèn space số↔chữ Việt.
- **Dịch giả chưa render vào front-matter EPUB** (mới chỉ vào catalog/metadata).
- **Ảnh bìa màu chưa downscale** → EPUB nặng (Aragông 3848 KB vs ~600 KB trung bình).

## Unresolved

- Heading space-join: rule post-process có an toàn cho mọi corpus không (vd số trong
  tên riêng "K2", "G7")? Cần test trước khi luôn-bật.
