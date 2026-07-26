# Vận hành

## Cost management

OpenRouter charge theo token in/out. Qwen 3.7-Plus (default) là $0.40/M input, $1.60/M output. **Số đo thực tế trên 41.191 trang (batch 3, 181 cuốn): ~$0,0033/trang, ~$0,75/cuốn** — sách 300 trang ≈ $1, nhóm ~14k trang ≈ $45–48. Gemini 3.1 Pro đắt ~15× ($0.05/trang), chỉ dùng backup cho trang Qwen chịu thua.

Pipeline in cost ước tính cuối stage 1 (dòng `cost~$` cuối log). Lưu ý: **dòng cost cuối mỗi cuốn KHÔNG phải tổng cộng dồn** — mỗi pass (all-1, retry, all-2) in cost riêng cho trang pass đó xử lý; chi thực = tổng mọi dòng cost mọi pass (group3: $48.43 thực vs $43.86 theo dòng cuối).

`--workers` mặc định 12; chạy 1 cuốn đơn lẻ 12–24 đều ổn (verified 331 trang, 0 fail). Chạy nhiều sách song song xem section "Batch OCR" bên dưới — 192 concurrent trên 1 key vẫn không bị throttle. Trang text-dày thỉnh thoảng "stutter" (1 trang nổ 12k–25k output token, latency 200–450s) — với nhiều worker, trang nổ không khoá batch, cứ để nó chạy.

Tips giảm cost: `--dpi 72` khi nguồn là PDF scan ~1024px (default 150 DPI upscale 2× vô ích, đắt hơn ~12%); crop viền trắng (`mogrify -trim`, tiết kiệm 10–20% input); JPG q85 thay PNG cho ảnh chụp (nhỏ hơn ~50%, vision model không phân biệt).

## OpenRouter credit và key cap

OpenRouter có 2 loại limit dễ confuse.

Credit balance là tiền còn lại trong account, tính bằng USD. Pipeline charge dần khi gọi API. Khi balance = 0, request fail với HTTP 402. Cần nạp thêm credit trên dashboard https://openrouter.ai/credits.

Key cap là limit per-API-key, đặt khi tạo key. Default mới tạo có thể là $5–20. Khi cumulative cost qua key đạt cap, request fail với HTTP 403 "Key limit exceeded (total limit)". Sửa cap tại https://openrouter.ai/keys, click key, raise limit.

Pipeline phân biệt 2 lỗi trong log. HTTP 402 nghĩa là nạp credit. HTTP 403 nghĩa là raise key cap. Cả hai đều cần action manual trên dashboard rồi rerun (resumable picks up đúng chỗ).

Tip thực tế: tạo riêng 1 OpenRouter key cho pipeline với cap $50–100, không dùng chung key research/dev khác. Phân biệt cost rõ trong analytics dashboard.

## Trang trống & trang chết — pipeline TỰ xử lý

Hai loại trang "không ra text" đều được tự động hoá, không cần can thiệp tay:

**Trang trống thật** (bìa sau, divider): model trả rỗng + `finish_reason=stop` → pipeline tự ghi `<!-- blank page -->`, đếm là `blank` (KHÔNG phải fail), pass sau skip. Không retry trang trắng.

**Trang chết deterministic** (provider trả rỗng/malformed y hệt mỗi lần): retry loop so sánh *error class* giữa các lần — cùng class lặp 2 lần → **early-abort** (không đợi hết retry), tự ghi `<!-- OCR FAILED (deterministic) — cần xử lý tay: <lý do> -->`. Trang vẫn đếm là `fail` (summary trung thực) nhưng có file `.md` size>0 nên **các pass sau skip, không ôm lại vòng retry**. Trước fix này, 1 trang chết kéo cả cuốn tới 8521s; sau fix, cuốn tệ nhất group3 chỉ 1966s.

Muốn thử OCR lại một trang có placeholder (trắng oan / muốn cứu trang chết): xoá file `work/ocr/page_NNN.md` tương ứng rồi rerun `scan2ebook all <slug>` — chỉ trang đó bị OCR lại, phần còn lại $0.

Nếu trang KHÔNG blank mà vẫn bị báo rỗng liên tục: hoặc safety filter (sách war/political — xem "Moderation-block" ở section Batch), hoặc ảnh quá mờ — rescan DPI cao hơn hay đổi model backup.

## Rclone setup

Rclone config lưu credential tại `~/.config/rclone/rclone.conf` (Linux/macOS). File này chứa OAuth refresh token, treat như password.

```bash
chmod 600 ~/.config/rclone/rclone.conf
```

Nếu chia sẻ máy với người khác hoặc dùng máy chung, xóa config sau khi xong.

```bash
rclone config delete gdrive
```

Test rclone hoạt động.

```bash
rclone lsd gdrive:
rclone touch gdrive:Ebooks/test.txt
rclone delete gdrive:Ebooks/test.txt
```

Multiple Drive account: tạo remote khác nhau, mỗi remote một OAuth flow.

```bash
rclone config  # name=gdrive-work, lặp lại OAuth flow với Google work account
```

Sau đó override qua `--remote`.

```bash
scan2ebook upload book.epub --remote gdrive-work
```

Throttle upload nếu băng thông yếu (rclone default unlimited). Set qua flag rclone trong env hoặc edit `drive_upload.py` để thêm `--bwlimit 5M`.

## Model swap

Default model `qwen/qwen3.7-plus` (6/2026) là tối ưu nhất cho corpus Việt: rẻ ($0.004/page), nhanh, giữ chính tả cũ tốt. Khi cần override, có 2 cách.

Override per-run qua CLI flag.

```bash
scan2ebook ocr <inbox> <out> --model google/gemini-3.1-pro-preview
```

Override qua env: đặt `OCR_MODEL=<id>` (cả `ocr` lẫn `all` đọc env này làm default cho `--model`).

Danh sách model đã test trên corpus Nam Phong 1917 (20 trang, 6/2026 benchmark).

`qwen/qwen3.7-plus` — zero fail, $0.004/page (~0.004–0.0038 old-text). Default, recommended. Giữ nguyên chính tả cũ (chánh, nhời, văn-chương).

`google/gemini-3.1-pro-preview` — quality tương đương Qwen nhưng gặp vấn đề trang dày (blank page, token spiral). ~15× đắt ($0.05/page). Backup nếu Qwen fail trang cụ thể.

`anthropic/claude-opus-4` — không nằm trong benchmark này; rất đắt, chỉ cân nhắc khi sách cực khó (corrupt scan, calligraphy) và verify trước.

`z-ai/glm-4.6v` — drops italic, slow (~489s/20pg old-text), fail 3 page (token budget overflow). Không recommend.

`baidu/ernie-4.5-vl-424b-a47b` — weakest, hallucinate text trên trang trắng, most error. Tránh.

Khi đổi model, smoke test 10 trang trước khi commit full pipeline. Output style mỗi model khác — Claude verbose hơn, GPT terse hơn, có thể cần chỉnh prompt nếu chuyển hẳn.

## Prompt tuning

Prompt OCR ở `src/scan_to_ebook/ocr.py`, biến `PROMPT`. Đã verified zero error trên Nam Phong 1917 với Gemini 3.1 Pro. Đừng đổi nếu không có lý do rõ.

Lý do hợp lệ để tune prompt: ngôn ngữ khác (English ngoại trừ Japanese, math heavy với LaTeX, music score), layout đặc biệt (newspaper 4 cột). **Tiếng Nhật là FIRST-CLASS**: dùng `--lang ja` khi init — pipeline sẽ dùng dedicated `JA_PROMPT` (xử lý tategaki/dọc, đọc phải→trái, bỏ qua app chrome), context-prepass `CONTEXT_PROMPT_JA` (phát hiện đúng spread RTL), và post-process chuẩn hóa space-less ATX headings. Không cần manual prompt tuning cho tiếng Nhật.

Quy trình tune. Một là branch riêng. Hai là edit `PROMPT`. Ba là smoke test 10–20 trang trên một cuốn có ground truth (ví dụ `samples/demo-scans/`, hoặc tự build fixture từ sách bạn sở hữu). Bốn là so diff với version cũ qua `git diff` hoặc dùng tool diff trực quan. Năm là chỉ merge khi diff acceptable (không corrupt chữ nào, không drop dấu).

Nếu test corpus mới (sách khác), build ground truth bằng cách chạy version cũ + manual fix 20–50 trang, lưu thành regression fixture.

## Migration giữa các máy

Pipeline portable hoàn toàn. Để migrate từ máy A sang máy B.

Máy B: cài system deps (pandoc, rclone, Python).

Máy B: clone repo, `pip install -e .`.

Máy B: copy `.env` từ máy A (hoặc tạo mới với cùng OPENROUTER_API_KEY).

Máy B: chạy `rclone config` lại (OAuth flow per-machine, không transfer được).

Máy B: copy inbox folder + output folder qua nếu muốn tiếp tục pipeline đã chạy dở.

Resumable pipeline cho phép kill máy A giữa chừng, transfer state, tiếp tục máy B. Filesystem state là source of truth, không có database lock.

## Debugging

OCR fail trên 1 page: chạy ocr 1 page riêng để có log chi tiết.

```bash
python3 -c "
from pathlib import Path
import os
from scan_to_ebook import ocr
md, meta = ocr.ocr_page(os.environ['OPENROUTER_API_KEY'], ocr.DEFAULT_MODEL, Path('~/Books-inbox/<slug>/page_065.png').expanduser())
print('latency:', meta['latency_s'])
print('---')
print(md[:500])
"
```

Pandoc warn duplicate footnote: pandoc gặp `[^1]` lặp ở nhiều page. Warn non-fatal, epub vẫn valid. Nếu muốn fix, edit `book.md` rename footnote unique per chapter (manual job).

Epub mở Books.app trống/lỗi: check magic bằng `file book.epub` phải ra `EPUB document`. Nếu không, rerun stage 3 với verbose pandoc.

```bash
pandoc book.md -o book.epub --toc --split-level=1 --verbose 2>&1 | head -50
```

Drive upload báo "Couldn't decrypt": rclone config corrupt. Xóa config và setup lại.

```bash
mv ~/.config/rclone/rclone.conf ~/.config/rclone/rclone.conf.bak
rclone config
```

## Backup

Dist folder (final EPUB) không có backup tự động. User tự backup.

Recommended: rclone sync entire scan2ebook folder lên Drive định kỳ. Scans zone quan trọng nhất vì không reproducible — nếu mất scans/, không thể rebuild.

```bash
rclone sync ~/scan2ebook/ gdrive:Backup/scan-to-ebook-books/ --progress
```

Scans PNG có thể backup bằng Time Machine (macOS) hoặc rclone tương tự. Work zone (cache + OCR temp) không cần backup — có thể xoá `rm -rf work/` bất kỳ lúc nào, chỉ tốn cost lại prepass (~$0.01 với Qwen 3.7-Plus, scales với model price).

Loại trừ `.env` khỏi backup public.

```bash
rclone sync ~/workspace/scan-to-ebook/ gdrive:Backup/repo/ --exclude .env --exclude .venv/ --progress
```

## Manga EPUB3 fixed-layout — Auto-cover operation

**`--auto-cover` cost and scope**
- Strictly opt-in: default manga build is $0/offline, no API key required.
- `--auto-cover` is the ONLY manga path that spends money — sends first N filtered pages (after min-px crop) + prompt to vision LLM asking "which is the real front cover?" (~$0.01/book, a few downscaled pages).
- Model failure (network timeout, parse error) or null result (no cover detected) → fallback to `cover_index=1` and build still succeeds. Cover is NOT load-bearing like OCR prepass.
- Manual `--cover-index N` (where N≠1) overrides `--auto-cover` → skips LLM call, prints warning.

**"Unknown Author" fix**
- Manga does NOT auto-backfill author (unlike OCR prose). You must pass `--author "..."` or EPUB shows "Unknown Author".
- Cheap workaround: pre-populate `scans/metadata.json` with `author` field, then rebuild from existing scans (no re-download, no LLM cost). Manga reuses `metadata.json` like prose pipeline.

## Manga EPUB3 fixed-layout — Troubleshooting

**CBR backend absent** — pipeline shells `unar` or `unrar` to extract .cbr. If missing, install:
```bash
brew install unar        # macOS
sudo apt install unar    # Ubuntu/Debian
```
Pipeline detects + hints at install if absent.

**Drive folder listing fragility** — `embeddedfolderview` is undocumented HTML scrape. If real-world folder has different structure, tolerant regex may fail. Fallback: manual prompt guides user to manually download folder as .zip, then `--from <downloaded.zip>`.

**Page order scrambled from Drive** — if filenames in folder aren't naturally sortable (random IDs), pipeline reorders by enumeration index during download to preserve folder order (fixes opaque-ID regression). Natural-sort always applied to final page set.

**Spread cadence off** — RTL pagination may differ from reader's display (reader rendering unverified). Use `--spread-reset 5,12` to re-anchor cadence after unexpected breaks (e.g., inserted color cover between chapters).

**min_px filter too aggressive** — small images (<400px) dropped with warning. Raise limit: `--min-px 200` to keep tiny art. Warn logged but visual impact hard to assess without reader.

**EPUB validation fails** — `.epub` must validate structurally (7 stdlib checks). If error: check that ALL images in `scans/` are readable (try `file scans/*.jpg`) and exist in OPF manifest before rebuild.

## Batch OCR nhiều sách song song

Khi phải OCR cả một hàng đợi lớn (hàng trăm cuốn PDF scan), không chạy tay từng cuốn. Chia thành các **đợt (batch)**, mỗi đợt tách thành **nhóm theo ngân sách** (vd ~$50/nhóm — giới hạn credit dễ kiểm soát), chạy 1 nhóm một lần và xác nhận credit giữa các nhóm. Quy trình dưới đây đã verified trên một đợt thực tế 181 cuốn / ~41k trang (~$0,0033/trang với qwen3.7-plus + `--dpi 72`).

### Tổ chức workdir

Mỗi cuốn một book-home chuẩn của pipeline, gom dưới 1 thư mục gốc (đặt ở đâu tuỳ bạn — ổ ngoài OK, pipeline đã lọc sidecar exFAT):

```
<BATCH_ROOT>/
├── group<N>-slugs.csv                 # input: tối thiểu slug,title,authors,pdf_path
├── books/<slug>/{scans,work,dist}/    # workdir mỗi cuốn (init tạo)
└── logs/<slug>-{init,all-N,retry-N}.log
```

### Bước 1 — Chạy nhóm bằng N-lane parallel driver

```bash
OPENROUTER_API_KEY=sk-... nohup python3 tools/batch_ocr_runner.py \
  --csv <BATCH_ROOT>/group1.csv --home <BATCH_ROOT>/books \
  --log-dir <BATCH_ROOT>/logs --lanes 8 --workers 24 --dpi 72 \
  > <BATCH_ROOT>/group1-run.log 2>&1 &
```

Nguyên tắc thiết kế (`tools/batch_ocr_runner.py --help` tự đủ):
- N lane rút sách từ **1 queue chung** → không cuốn nào bị 2 lane đụng (không collision file).
- Mỗi cuốn: `init --from <pdf> --dpi 72 --author --title` (skip nếu đã có scans) → `all --yes` × 2 pass, giữa 2 pass chạy `ocr` retry (tự nạp context cache); pass `all` cuối tự bỏ pre-pass khi 0 trang cần OCR → sách bị moderation chặn ảnh mẫu vẫn tự ra EPUB.
- Kết quả phân loại rõ: `DONE | WARN(no-epub) | WARN(init-fail) | STOP(402)` — không còn WARN hộp đen.
- **8 lane × 24 worker = 192 concurrent trên 1 key là an toàn** — verified: 34 cuốn / ~14.6k trang trong 79 phút, speedup 7.2x, chỉ 2 lần 429 lẻ. `qwen3.7-plus` không throttle ở mức này.
- **`--dpi 72` khi scan nguồn ~1024px**: default 150 DPI upscale 2× vô ích, đắt hơn ~12%.
- **HTTP 402 ở bất cứ lane nào → dừng nhận việc mới.** 402 = hết credit (KHÔNG phải lỗi sách). Nạp credit rồi rerun — OCR cache khiến resume chỉ làm trang còn thiếu, trang xong = $0.

### Bước 2 — Sau khi driver kết thúc: rebuild WARN + validate

Cuốn báo `WARN` (thay vì `DONE`) là cuốn `all` không ra được EPUB ở pass cuối nhưng md thường đã đủ. Kiểm md (`ls books/<slug>/work/ocr/*.md | wc -l` so với số scan) rồi rebuild ($0, dùng md cache):

```bash
SCAN2EBOOK_HOME=<BATCH_ROOT>/books scan2ebook all <slug> --yes
```

**Moderation-block (sách chiến tranh, ảnh nhạy cảm):** provider từ chối ảnh SAMPLE ở context pre-pass (`data_inspection_failed` HTTP 400). Pipeline tự xử lý: khi **0 trang cần OCR** (rebuild từ md cache), `all` bỏ qua pre-pass hẳn → rebuild sách moderation-block chỉ là `all <slug> --yes`. Nếu sách còn trang dở dang mà pre-pass vẫn bị chặn: `all <slug> --yes --skip-prepass` (trang còn lại OCR bằng base prompt + cache context nếu có).

Nếu vẫn cần build 2 stage tay (pipeline cũ), nhớ: **KHÔNG truyền `--pattern "*.md"` vào `post` trên ổ exFAT** — nó nhặt cả sidecar `._page_*.md` → UnicodeDecodeError (byte 0xb0). Default `page_*.md` tự loại `._`.

**Validate bằng `scan2ebook verify`** (zipfile.testzip bên dưới — KHÔNG dùng vòng lặp shell `unzip -t`: bash mis-split CSV có dấu phẩy trong field, zsh `if cmd >/dev/null` nuốt exit code, âm thầm skip):

```bash
scan2ebook verify <BATCH_ROOT>/books        # cả thư mục book-homes
scan2ebook verify books/<slug>              # 1 cuốn
# per-file: OK/TINY/BADZIP/MISSING + summary; rc 0 chỉ khi tất cả OK
```

### Bước 3 — Dọn TOC rác + rebuild cuốn bị đổi

```bash
python3 tools/fix_toc_junk.py --home <BATCH_ROOT>/books --csv group1.csv
# in danh sách SLUGS đã sửa → rebuild CHỈ các cuốn đó ($0):
SCAN2EBOOK_HOME=<BATCH_ROOT>/books scan2ebook all <slug> --yes
```

OCR biến trang "MỤC LỤC" của sách thành heading (pandoc tự sinh TOC → trùng) + chữ trên bìa thành `## <title>`/`## <author>`. Tool dọn CHỈ 2 loại chắc chắn an toàn: xoá block MỤC LỤC + hạ heading title/author **khi body rỗng thật** (heading trùng title NHƯNG có prose sau = chương thật, vd tuyển tập đặt tên theo 1 truyện — không đụng). Backup `.bak` cạnh book.md.

### Lưu ý về cost

**Chi thực của một cuốn = tổng sổ `work/cost.json`** — pipeline tự ghi 1 entry mỗi lần tiêu tiền (pre-pass, mỗi pass OCR); `all` in "Chi phí cộng dồn cuốn này" cuối build. Đừng diễn giải dòng `cost~$` trong log: mỗi pass in cost riêng cho trang pass đó xử lý, **dòng cuối KHÔNG phải tổng** (thực đo một nhóm: lệch ~10%). Trang cache = $0 khi resume.

## OCR Pipeline — Limits đã biết

Pipeline không xử lý tốt: sách có ảnh minh họa nhiều (model mô tả ảnh thay vì OCR, output rác), sách formula toán/khoa học (LaTeX rendering cần prompt riêng), sách nhạc với khuông nhạc (vision model không transcribe sheet music chính xác).

Pipeline xử lý OK nhưng cần manual review: sách có table phức tạp (column alignment đôi lúc lệch trong markdown), sách footnote dày (numbering có thể duplicate giữa chapter).

Pipeline xử lý tốt: sách prose tiếng Việt hiện đại, sách prose tiếng Việt cổ (1900-1950), sách tiểu thuyết, sách non-fiction text-heavy, tạp chí 2 cột, PDF scans, PDF born-digital (text-layer hỏng).
