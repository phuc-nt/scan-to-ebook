# OCR Model Benchmark — 5 model TQ vs Gemini 3.1 Pro baseline

- Corpus: 20 trang đầu `bench-ocr` (Trường Học Đờn Bà - André Gide/Bùi Giáng, scan PDF)
- Baseline (ground-truth): `google/gemini-3.1-pro-preview`
- Chấm: SequenceMatcher ratio vs baseline + delta ký tự có dấu + delta số dòng

## Tổng hợp

| Model | Char-sim vs baseline | Δdấu (avg/trang) | Δdòng (avg) | Fails | Cost 20tr | $/M in,out | Latency tổng |
|-------|----------------------|------------------|-------------|-------|-----------|------------|--------------|
| `google/gemini-3.1-pro-preview` (baseline) | 1.000 | — | — | 3 | $0.5505 | 2.5,10.0 | 137s |
| `qwen/qwen3.7-plus` | 0.928 | +0.0 | -1.9 | 2 | $0.0369 | 0.4,1.6 | 63s |
| `qwen/qwen3.6-plus` | 0.879 | +1.1 | -1.1 | 1 | $0.0405 | 0.325,1.95 | 83s |
| `minimax/minimax-m3` | 0.855 | +0.5 | -0.8 | 1 | $0.0436 | 0.3,1.2 | 99s |
| `z-ai/glm-4.6v` | 0.916 | +0.3 | +0.1 | 3 | $0.0579 | 0.3,0.9 | 222s |
| `baidu/ernie-4.5-vl-424b-a47b` | 0.736 | +3.6 | -1.7 | 0 | $0.0291 | 0.42,1.25 | 57s |

## Đọc số liệu
- **Char-sim**: 1.0 = giống baseline hoàn toàn. Cao = OCR gần Gemini.
- **Δdấu**: âm = bỏ/mất dấu so baseline (xấu); ~0 = giữ dấu tốt.
- **Δdòng**: âm = gộp/mất dòng; dương = tách dư.
- Char-sim KHÔNG phải accuracy tuyệt đối (baseline cũng có thể sai); nó đo độ ĐỒNG THUẬN với model tốt nhất hiện tại.

## Lưu ý đọc kết quả

- **3 "fails" của baseline (trang 10/12/14) là TRANG TRẮNG THẬT** — đa số model đồng thuận
  blank. Không phải mất chất lượng. ernie là model DUY NHẤT "đọc" được 3 trang này
  (0 fail) → nghi ngờ HALLUCINATE chữ trên trang trắng (xấu, không tốt).
- **Char-sim phần lớn lệch do MARKDOWN, không phải sai chữ.** Diff tay trang text-dày
  (page_008): khác biệt giữa baseline và qwen3.7-plus gần như 100% là vị trí dấu `*` in
  nghiêng — text gần như y hệt. Nên qwen3.7-plus thực chất tốt hơn con số 0.928 thể hiện.

## So tay trang text-dày (page_008) — lỗi OCR THẬT

| Model | Lỗi chữ thật | Dấu tiếng Việt | Markdown italic | Giữ chính tả cổ |
|-------|--------------|----------------|-----------------|------------------|
| `qwen/qwen3.7-plus` | ~1 (`tậpở` thiếu cách) | ✅ chuẩn | bám sát baseline | hiện-đại-hoá nhẹ (Sỹ→Sĩ) |
| `z-ai/glm-4.6v` | vài (`Chợp Biên`, `giới đến`, `vinh hoa`) | ✅ chuẩn | **bỏ hết italic** | hiện-đại-hoá (kỷ→kỉ) |
| `qwen/qwen3.6-plus` | trung bình | ✅ ổn | một phần | — |
| `minimax/minimax-m3` | trung bình + vài trang out thấp (bỏ sót) | ✅ ổn | một phần | — |
| `baidu/ernie-4.5-vl-424b-a47b` | **nhiều** (`giáng lượng`, `sổ lương`, `di cáo` lặp, `vĩnh hằng`) | lỗi rải rác | bỏ italic | + hallucinate trang trắng |

## Kết luận & khuyến nghị

**Người thắng: `qwen/qwen3.7-plus`** — chất lượng OCR sát baseline Gemini nhất (lỗi chữ thật
gần như bằng 0, dấu chuẩn, giữ markdown tốt), latency NHANH NHẤT trong nhóm (63s, nhanh hơn
cả baseline 137s), **chi phí $0.037/20tr ≈ rẻ hơn baseline ~15×** ($0.55 → $0.037).

- **Hạng 2 — `z-ai/glm-4.6v`**: chữ tốt, dấu chuẩn nhưng BỎ markdown nghiêng + chậm (222s, có
  reasoning tokens). Dùng được nếu không cần in nghiêng; chậm là nhược điểm cho sách dài.
- **Tránh `baidu/ernie-4.5-vl-424b-a47b`**: nhiều lỗi chữ + hallucinate trang trắng → rủi ro
  cao cho corpus Việt khó.
- **Lưu ý chính tả cổ**: cả qwen lẫn glm có xu hướng hiện-đại-hoá nhẹ (Sỹ→Sĩ, kỷ→kỉ) — với
  sách HIỆN ĐẠI (như cuốn test này) không sao; với văn bản CỔ (Nam Phong 1917) cần re-test
  vì base PROMPT yêu cầu giữ nguyên chính tả cổ.

**Đề xuất tiếp theo:** đổi default sang `qwen/qwen3.7-plus` để tiết kiệm ~15× chi phí; nhưng
nên benchmark thêm 1 cuốn VĂN BẢN CỔ (Nam Phong) trước khi chốt, vì đó là lý do Gemini được
chọn ban đầu.

> So tay đầy đủ: xem `out/<model>/page_NNN.md` cạnh `out/google__gemini-3.1-pro-preview/page_NNN.md`.
