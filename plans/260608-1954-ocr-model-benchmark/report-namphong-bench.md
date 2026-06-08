# OCR Model Benchmark — 5 model TQ vs Gemini 3.1 Pro baseline

- Corpus: 20 trang đầu `namphong-bench` (Nam Phong Tạp Chí 1917 (số 1) - chính tả cổ tiền-1945, scan PDF)
- Baseline (ground-truth): `google/gemini-3.1-pro-preview`
- Chấm: SequenceMatcher ratio vs baseline + delta ký tự có dấu + delta số dòng

## Tổng hợp

| Model | Char-sim vs baseline | Δdấu (avg/trang) | Δdòng (avg) | Fails | Cost 20tr | $/M in,out | Latency tổng |
|-------|----------------------|------------------|-------------|-------|-----------|------------|--------------|
| `google/gemini-3.1-pro-preview` (baseline) | 1.000 | — | — | 1 | $1.0506 | 2.5,10.0 | 185s |
| `qwen/qwen3.7-plus` | 0.826 | +69.8 | +3.1 | 0 | $0.0763 | 0.4,1.6 | 217s |
| `qwen/qwen3.6-plus` | 0.849 | +60.0 | +1.2 | 0 | $0.0930 | 0.325,1.95 | 174s |
| `minimax/minimax-m3` | 0.820 | +85.5 | +5.0 | 0 | $0.0733 | 0.3,1.2 | 393s |
| `z-ai/glm-4.6v` | 0.642 | +22.1 | +0.1 | 3 | $0.0724 | 0.3,0.9 | 489s |
| `baidu/ernie-4.5-vl-424b-a47b` | 0.481 | +50.6 | +3.4 | 0 | $0.0362 | 0.42,1.25 | 154s |

## Đọc số liệu
- **Char-sim**: 1.0 = giống baseline hoàn toàn. Cao = OCR gần Gemini.
- **Δdấu**: âm = bỏ/mất dấu so baseline (xấu); ~0 = giữ dấu tốt.
- **Δdòng**: âm = gộp/mất dòng; dương = tách dư.
- Char-sim KHÔNG phải accuracy tuyệt đối (baseline cũng có thể sai); nó đo độ ĐỒNG THUẬN với model tốt nhất hiện tại.

> So tay: xem `out-namphong-bench/<model>/page_NNN.md` cạnh baseline.

## Diễn giải (QUAN TRỌNG — số liệu thô gây hiểu lầm)

Char-sim tụt (0.93→0.82) và Δdấu vọt (+0→+70) trên văn bản cổ **KHÔNG** phải vì
ứng viên kém đi. Diff tay cho thấy **ngược lại** — phần lớn do BASELINE Gemini hỏng
trên trang chữ-dày cổ:

- **page_008: Gemini trả về RỖNG** (`finish_reason=stop`, tự coi 1 trang chữ đặc là
  "blank"). qwen3.7/qwen3.6/minimax đọc đủ (~3300 ký tự). 1 trang baseline trắng này
  bơm ~3300 ký tự có dấu "thừa" vào MỌI ứng viên → **một mình nó đẩy Δdấu lên +60..+85
  và kéo char-sim xuống**. Là artifact của baseline, không phải lỗi ứng viên.
- **page_007: Gemini token-spiral** (out≈12000, 94.7s) → text lưu bị cắt còn ~40%.
  Ứng viên đọc đủ.
- ⇒ Gemini `fails=1` ghi nhận thiếu: thực tế nó BLANK trang 8 + CẮT trang 7 trên chữ
  cổ dày. Trên corpus này **các model rẻ ĐỌC ĐỦ HƠN baseline.**

### So tay trang sạch (page_013) — không trang nào fail

| | Baseline Gemini | qwen3.7-plus |
|--|------------------|---------------|
| Lỗi dấu hỏi/ngã | **nhiều, có hệ thống** (biều→biểu, hiều→hiểu, thề→thể, đề→để, quyền→quyển, tiều-thuyết) — Gemini đọc nhầm `ể`→`ề` trên scan này | đọc ĐÚNG các chữ đó |
| Lỗi chữ riêng | ít | vài (nhàn-đàm→nhân-đàm, tính-tình→tinh-tinh, thơm→thom, xác→sắc, "mình"→"mind") |
| **Giữ chính tả cổ** | ✅ (nhời, nhớn, ư, hyphen) | ✅ (nhời, **nhơn** — còn cổ hơn, ư, hyphen) |

**Phát hiện then chốt:** nỗi lo "qwen hiện-đại-hoá chính tả cổ" (từ run sách hiện đại)
**KHÔNG xảy ra** trên Nam Phong — qwen giữ nguyên nhời/nhơn/hyphen, thậm chí đọc dạng
cổ hơn baseline. Hai model chỉ TRAO ĐỔI lỗi khác nhau (Gemini sai dấu hỏi/ngã hệ thống;
qwen sai lác đác chữ riêng), không bên nào trội hẳn về chất lượng chữ.

### glm-4.6v: loại
3 trang FAIL `finish_reason=length` (reasoning tokens nổ budget), + chậm nhất (489s).
char-sim 0.642 phản ánh 3 trang trống. Không dùng cho corpus cổ dày.

### ernie: loại
char-sim 0.481, Δdấu +50 — nhiều lỗi nhất, như run trước.

## Kết luận — văn bản cổ

- **Gemini KHÔNG vô địch trên văn bản cổ:** blank 1 trang dày + token-spiral cắt 1 trang,
  và sai dấu hỏi/ngã có hệ thống trên scan này. Đắt nhất ($1.05/20tr, gấp đôi sách hiện đại).
- **qwen3.7-plus: 0 fail, đọc đủ mọi trang, giữ chính tả cổ tốt**, lỗi chữ lác đác ngang
  ngửa (kiểu khác) Gemini. **$0.076/20tr ≈ rẻ hơn ~14×.**
- **Chốt:** văn bản cổ KHÔNG còn là lý do giữ Gemini — qwen3.7-plus sòng phẳng (đôi chỗ
  hơn) về chữ, vượt hẳn về độ phủ (0 fail vs Gemini blank/cắt) và chi phí. Khuyến nghị
  đổi default sang `qwen/qwen3.7-plus` cho CẢ sách hiện đại lẫn văn bản cổ.
- **Lưu ý vận hành:** cả 6 model thỉnh thoảng coi trang dày là "blank"/spiral; cơ chế
  retry + smoke-gate hiện có vẫn cần thiết bất kể model nào.
