# Hướng dẫn người dùng

## Cài đặt

Pipeline cần Python 3.10+, pandoc, và rclone (optional cho stage upload).

Trên macOS, dùng Homebrew để cài system dependencies. Pandoc và rclone đều có trong brew tap chính.

```bash
brew install pandoc rclone python@3.12
```

Trên Linux (Ubuntu/Debian), apt cũng có cả ba.

```bash
sudo apt install pandoc rclone python3.12 python3.12-venv
```

Sau đó clone repo và cài Python package trong virtual environment riêng.

```bash
git clone <repo-url> ~/workspace/scan-to-ebook
cd ~/workspace/scan-to-ebook
python3 -m venv .venv
.venv/bin/pip install -e .
```

Tạo file `.env` chứa OpenRouter API key. Lấy key tại https://openrouter.ai/keys, sau đó.

```bash
cp .env.example .env
# Mở .env bằng editor, paste key vào sau OPENROUTER_API_KEY=
```

Pipeline **tự nạp `.env`** (tìm ở thư mục hiện tại rồi repo root) nên không cần `source .env` mỗi shell. Nếu đã `export OPENROUTER_API_KEY` sẵn thì biến đó được ưu tiên (không bị `.env` ghi đè). Parser `.env` đơn giản: chỉ `KEY=value` mỗi dòng (bỏ comment đầu dòng, strip nháy bao ngoài). KHÔNG hỗ trợ prefix `export ` hay comment inline (`KEY=val # ...`) — viết thuần `KEY=value`.

**Setup self-check**: Sau khi `pip install -e .`, chạy `scan2ebook doctor` để verify môi trường.

```bash
.venv/bin/scan2ebook doctor
```

Lệnh này kiểm tra Python >= 3.10, pandoc có cài, key OpenRouter present/absent, rclone optional. Exit code 0 nếu essential checks pass (python + pandoc + key). Chỉ essential mới blocking — rclone vắng chỉ warning (upload sẽ không chạy).

Nếu chưa có credit OpenRouter, nạp $5–10 để start. Cost dao động ~$0.004/page A4 với Qwen 3.7-Plus (default), một quyển 200 trang khoảng $0.80.

## Cấu hình Drive (tùy chọn)

Stage 4 chỉ cần khi muốn upload epub lên Google Drive để đọc trên thiết bị khác. Bỏ qua bước này nếu chỉ đọc local.

Chạy `rclone config` một lần. Browser OAuth flow khoảng 3 phút.

```bash
rclone config

# n) New remote
# name> gdrive
# Storage> drive
# client_id> (Enter để dùng default)
# client_secret> (Enter)
# scope> 1 (Full access)
# service_account_file> (Enter)
# Edit advanced config> n
# Use auto config> y
# (Browser tự mở, login Google account, allow rclone)
# Configure as Shared Drive? n
# Confirm> y
# q) Quit
```

Sau đó verify.

```bash
rclone lsd gdrive:
# Liệt kê folder trong My Drive
```

## Chuẩn bị scan

Sách giấy cần được scan thành PNG, JPG, HEIC hoặc HEIF, mỗi trang một file. App Phúc đang dùng là vFlat trên iPhone — auto crop, auto deskew, output PNG. Adobe Scan và ScannerPro cũng tốt. iPhone mặc định chụp HEIC/HEIF — pipeline tự động convert sang JPG tại stage import, không cần bước thêm. Tránh chụp thường bằng camera vì lệch perspective.

Ngoài ảnh scan, bạn cũng có thể bắt đầu từ một file PDF của sách (chẳng hạn từ Calibre hoặc scan PDF từ app scanner). Pipeline sẽ render từng trang PDF thành JPG rồi chạy qua OCR pipeline bình thường — không trích text layer (PDF born-digital thường có encoding hỏng).

Hoặc dùng **Google Drive file link**. Nếu bạn đã upload file PDF lên Google Drive và chia sẻ công khai ("Bất kỳ ai có link" hoặc "Public"), pipeline tự động tải xuống (temp file), validate là PDF thực sự, rồi xử lý như file PDF local. Chỉ hỗ trợ FILE link (không folder link hoặc URL bất kỳ). Ví dụ: `https://drive.google.com/file/d/1RAG...nOA/view?usp=drivesdk`

Cách nhanh nhất: dùng lệnh `init` để tạo inbox + import ảnh + rename tự động (xem mục "Tạo inbox nhanh" bên dưới). Pipeline dùng **natural-sort** nên tên file không bắt buộc zero-pad — `page_5.png`..`page_80.png` vẫn sort đúng số học. Tuy vậy đặt `page_001.png`, `page_002.png`... vẫn gọn và dễ đọc hơn.

Nếu muốn rename thủ công (app scan đặt tên khác như vFlat `IMG_001.png`):

```bash
cd ~/Books-inbox/<slug>/
ls *.png | nl | while read n f; do
  mv "$f" "$(printf 'page_%03d.png' $n)"
done
```

Pipeline nhận cả **PNG, JPG/JPEG, HEIC, HEIF, PDF**. Ảnh được xử lý bằng cách chuyển đổi HEIC/HEIF qua chain backend (sips macOS → magick ImageMagick → heif-convert → pillow-heif, first available). File PDF được render từng trang thành JPG qua backend-chain: pdftoppm (poppler) → magick (ImageMagick + Ghostscript) → sips (macOS, single-page fallback). **PDF render strategy**: Luôn render→OCR (không trích text layer), vì PDF born-digital (Calibre, Quartz) thường có ToUnicode CMap hỏng → pdftotext yield ký tự rác, trong khi ảnh render qua vision model OCR sạch. Xử lý nhất quán cả PDF scan lẫn PDF text-layer-broken. Cross-platform: Windows/macOS/Linux tất cả supported. Nếu NO backend available (HEIC/PDF), raise error (mất trang = sách hỏng, never skip silent). Lệnh `doctor` check backend: HEIC convert (non-essential, warning only nếu thiếu — chỉ cần khi input HEIC), PDF render (non-essential, warning only nếu thiếu — chỉ cần khi input PDF).

DPI tối thiểu khuyến nghị là 300 DPI cho text rõ ràng. Vision model tolerate được DPI thấp hơn nhưng dấu Việt có thể đoán sai.

Thư mục scans hoàn chỉnh trông như sau (ở `~/scan2ebook/<slug>/scans/`).

```
~/scan2ebook/namphong-q01/scans/
├── page_001.png
├── page_002.png
├── ...
├── page_075.png
├── metadata.json
└── cover.jpg (optional; manual override)
```

`metadata.json` không bắt buộc nhưng nên có với sách dài. Pipeline dùng metadata này làm title epub, author, language tag cho thiết bị đọc.

```json
{
  "title": "Nam Phong Tạp Chí — Quyển I (số 1-6, 1917)",
  "author": "Phạm Quỳnh (chủ bút)",
  "lang": "vi",
  "year": "1917"
}
```

**Cover (bìa EPUB)**: Pipeline tự động phát hiện trang bìa màu từ stage 0 (pre-pass). Thứ tự ưu tiên:
  1. **`scans/cover.jpg`** (nếu bạn tự đặt) — luôn thắng, override rõ ràng. Ảnh đẹp nhất là 1600x2400 portrait, nhưng pandoc accept mọi kích thước.
  2. **Auto-detect từ `context.json`** (pre-pass) — nếu lần đầu chạy, LLM tự dò ảnh bìa màu (ví dụ `page_001.jpg`) rồi lưu vào `cover_page` trong `work/context.json`. Lần chạy lại, cover được dùng cache này (cost 0).
  3. **Không có cover** — nếu sách scan toàn trắng đen, hoặc pre-pass không phát hiện được (context.json không có `cover_page` hoặc giá trị null), EPUB build mà không cover (đặc biệt phổ biến với tạp chí/journal cũ).

Bìa tự động + body text ở cùng một EPUB: trang bìa (nếu được auto-detect) vẫn được OCR vào thân sách dạng đoạn văn bình thường (không dùng heading tránh lọt TOC). Chỉ ảnh bìa được embed qua pandoc `--epub-cover-image` — text bìa/colophon ở body.

**Cấp độ cache & resume**: Nếu sách cũ (chạy TRƯỚC khi feature này được thêm) có `context.json` mà không `cover_page` field, resume sẽ KHÔNG có cover. Để lấy cover, xóa `work/context.json` (bắt lại pre-pass, cost ~$0.07) rồi chạy lại. OCR cache không bị mất, chỉ context tái tạo.

## Tạo inbox nhanh

Lệnh `init` gộp các bước chuẩn bị (tạo folder + scans zone, copy ảnh / render PDF, rename `page_NNN`, sinh `metadata.json` mẫu) thành một lệnh.

```bash
# Từ thư mục ảnh
scan2ebook init namphong-q01 --from ~/Desktop/scan-output \
  --title "Nam Phong Tạp Chí Q01 (1917)" --author "Phạm Quỳnh"

# Hoặc từ file PDF local
scan2ebook init chuyen-thu --from ~/Books/book.pdf \
  --title "Chuyện Thứ" --author "Nguyễn Văn Tác"

# Hoặc từ Google Drive file link công khai
scan2ebook init mybook --from "https://drive.google.com/file/d/1RAGxunS5cgjCM6qbxvHG84gMuWrZ_nOA/view?usp=drivesdk" \
  --title "Tiêu đề sách" --author "Tác giả"

# Sách tiếng Nhật (prose pipeline, kích hoạt OCR dọc + RTL spread handling)
scan2ebook init japanese-novel --from ~/Books/scans/ \
  --title "花子物語" --author "田中太郎" --lang ja
```

Kết quả: tạo `~/scan2ebook/{slug}/scans/`, copy + natural-sort + rename ảnh từ `--from` thành `page_001.<ext>`..., và ghi `metadata.json` vào `scans/`. Nếu `--from` là PDF (local hoặc Drive link), pipeline render từng trang thành `page_NNN.jpg` rồi đặt vào scans/. HEIC/HEIF file tự động convert→JPG trong quá trình này (EXIF + orientation được giữ nguyên). Override data root bằng `--home`.

**`--lang` parameter**: Đặt ngôn ngữ sách (mặc định `vi` cho tiếng Việt). Pipeline dùng ngôn ngữ để chọn OCR prompt chuyên biệt. `--lang ja` dùng cho sách tiếng Nhật (novels, essays) — kích hoạt OCR dọc (tategaki), đọc phải→trái, bỏ qua chrome app (menu bar, header, footer, dock), và render guidance "đọc trang PHẢI trước rồi trang TRÁI" cho ảnh trang đôi. Lưu ngôn ngữ vào `metadata.json` — lệnh `all` đọc tự động và chọn đúng prompt cho pipeline. **Lưu ý**: `--lang ja` này dành cho OCR prose pipeline (sách dọc thường). Riêng manga dùng lệnh `scan2ebook manga` (pipeline khác, không OCR, fixed-layout).

Bỏ `--from` nếu muốn tự copy ảnh sau (lệnh chỉ tạo folder + metadata mẫu). `metadata.json` đã tồn tại sẽ được giữ nguyên, không ghi đè. Nếu `scans/` đã có file `page_*`, `init --from` sẽ báo lỗi (rc=2) thay vì import — xoá page cũ trước rồi chạy lại, tránh để lại page rác bị OCR nhầm (tốn tiền).

### Google Drive link (hạn chế)

`--from` chấp nhận Google Drive file link công khai (`https://drive.google.com/file/d/<ID>/view?...`, `https://drive.google.com/open?id=<ID>`, hoặc `https://drive.google.com/uc?id=<ID>&export=download`). Pipeline tải về temp file, validate là PDF thật (`%PDF` magic bytes), rồi render→page như PDF local. Hạn chế:

- **Chỉ FILE link** — không folder link, không URL bất kỳ.
- **File phải công khai** — "Bất kỳ ai có link" hoặc "Public". File riêng/hạn chế fail với thông báo "link không public".
- **File lớn (> ~100MB)** — nếu vượt ngưỡng quét virus của Drive, pipeline sẽ gặp trang interstitial với confirm-token. Code xử lý tự động (HTTP cookie jar + token parse) — bạn không cần làm gì.
- **Content validate** — nếu Drive trả về cái không phải PDF (HTML error, folder index, v.v.), error rõ ràng bằng tiếng Việt thay vì silent fail.

## Bối cảnh sách (context.json)

Trước khi OCR từng trang, pipeline tự động trích bối cảnh sách (title, author, translator, pages_per_image, table_of_contents, proper names, terminology, layout notes, **cover_page**) từ 15 sample ảnh. Kết quả lưu thành `context.json` và `context.md` trong thư mục `work/`.

**context.json** (source-of-truth, hand-editable)
- Tệp JSON cấu trúc chứa metadata sách, OCR guidance, và cover_page
- **Hand-editable**: nếu phát hiện lỗi (ví dụ `pages_per_image` detect sai 1 thay vì 2, hoặc `cover_page` sai), edit trực tiếp JSON và re-run (cache hit, cost 0)
- Khi resume (chạy lại sách cũ), pipeline kiểm tra `work/context.json` tồn tại → skip API, re-derive guidance từ JSON (bao gồm cover)
- Thay đổi tay: sửa `pages_per_image`, tên riêng, hay `cover_page` trong JSON → chạy lại sẽ sử dụng giá trị mới

**context.md** (mirror, chỉ đọc)
- Render đã format của `context.json`, dùng để xem nhanh bên ngoài editor
- Header comment: "edit context.json to change" — file này tự động generate từ JSON
- Chỉnh sửa `context.md` đơn lẻ **sẽ bị bỏ qua** (JSON là authoritative), chỉnh sửa không có tác dụng

**Cover/colophon rule**: Context block luôn chứa quy tắc cố định: trang bìa/tựa đề (đầu sách) và trang thông tin xuất bản/colophon (cuối sách: tên sách, tác giả, dịch giả, NXB, giấy phép, giá bán) phải để dạng đoạn thường, TUYỆT ĐỐI KHÔNG dùng `## `/`### ` heading. Quy tắc này áp mọi sách tự động, không phụ thuộc trường nào trong context.json — mục đích tránh pandoc `--toc` nhặt chữ trang trí vào mục lục.

**Ví dụ**: sách dịch từ Pháp, OCR detect đúng translator nhưng sai tên riêng. Mở `~/scan2ebook/namphong-q01/work/context.json`, tìm `"proper_names"` array, sửa "Miraben" → "Miraudy" (canonical), save. Chạy lại `scan2ebook all namphong-q01`, pipeline sẽ dùng context cache này (cost 0) và OCR toàn bộ với tên đúng.

## Smoke test sách mới (--smoke gate)

Khi scan sách mới chưa từng test, đừng chạy thẳng full pipeline — cost rủi ro $10+ nếu OCR ra rác. Dùng **`--smoke`** để test 10 trang đầu, build mini epub, ước cost full, rồi gate xác nhận trước chi tiền.

```bash
scan2ebook all namphong-q01 --smoke
```

**Flow chi tiết:**
1. OCR ≤10 trang đầu (~$0.04 với Qwen 3.7-Plus) → ghi vào `work/ocr/`
2. Build mini epub `work/book.smoke.epub` từ 10 trang để preview
3. Ước cost cho phần còn lại: `(số trang còn lại) × giá/trang đo thật từ smoke` (cost token-based của 10 trang smoke chia ra; chính xác hơn flat $0.004, fallback $0.004 nếu smoke 0 trang ok)
4. **Interactive prompt**: `Full run ≈ $X.XX cho Y trang còn lại. Continue? [y/N]`
   - Gõ `y` hoặc `Y` → tiếp tục full pipeline (resume-safe, 10 trang đã OCR sẽ skip)
   - Gõ `n` hoặc Enter (default) → dừng, giữ smoke epub để review

**Kiểm tra smoke epub**: Mở `~/scan2ebook/namphong-q01/work/book.smoke.epub` trên Books.app để check chất lượng. Cần kiểm tra:

- **Dấu tiếng Việt**: có đúng không (chữ ô, ấ, ầ, ậ, ẩ, ẫ, ơ, ờ, ớ, ợ). Nếu bị bỏ dấu hoặc đoán sai, OCR model gặp khó với scan → thử raise DPI scan hoặc đổi model qua `--model`.
- **Chính tả cổ** (nếu sách cổ): có giữ nguyên không. "Văn-chương" có hyphen, "chánh" giữ nguyên (không sửa "chính"). Qwen 3.7-Plus (default) giữ chính tả cũ tốt — verified trên Nam Phong 1917. Khi đổi sang model khác/rẻ hơn, verify lại "Văn-chương" và "chánh" không bị modernize.
- **Layout 2 cột**: nối đúng không. Đọc 2-3 đoạn, xem có flow tự nhiên hay xen cột.
- **Heading**: detect được không. Xem `# Chương` có promote trong epub.

Nếu smoke OK, confirm at prompt để chạy full. Nếu vấn đề, abort, fix input/scan, rồi chạy `--smoke` lại (resumable).

**Non-interactive mode** (CI/script): Dùng `--yes` để bỏ qua prompt.
```bash
scan2ebook all ~/Books-inbox/namphong-q01 --smoke --yes
```

Nếu không tty (pipe/non-interactive) mà không có `--yes`, abort an toàn (không treo `input()`).

## Manga EPUB3 fixed-layout

Để build manga/truyện tranh dưới dạng **EPUB3 fixed-layout (pre-paginated) RTL** mà KHÔNG OCR:

```bash
scan2ebook manga my-manga --from ~/scans-folder
```

**Positional slug** nhận SLUG (folder name) hoặc PATH (đường dẫn tuyệt đối/tương đối). Ví dụ:
```bash
scan2ebook manga /abs/path/to/book --from ~/scans
scan2ebook manga sub/book --from ~/scans
scan2ebook manga my-manga --from ~/scans
```

**Inputs** (tự động normalize thành `scans/page_NNN.<ext>`):
- **Thư mục ảnh** — PNG, JPG (auto-sort tự nhiên)
- **.mobi/.azw3** — carves images từ PDB records, filters by size (>1000 bytes)
- **.cbz/.cbr/.zip** — extracts with zip-slip guard; CBR needs `unar` or `unrar` (install hint if missing)
- **Google Drive file** — single PDF/EPUB to download; hoặc Drive **folder** để list + download toàn bộ
  - Folder listing via undocumented embeddedfolderview scrape (tolerant regex, falls back to manual prompt)
  - SSRF-safe: URL-rebuild từ extracted file-id

**Metadata flags**:
- `--title "..."` — explicit title (thắng luôn, không derive). Nếu bỏ qua + có `--series` + `--series-index` → auto derive title thành `"Series 01"` (vd `"Pluto 02"`). Nếu không có index → fallback slug.
- `--author "..."` — tác giả. **Lưu ý**: khác pipeline OCR prose, manga KHÔNG auto-detect author — bạn phải truyền `--author` hoặc EPUB sẽ hiển thị "Unknown Author".
- `--series "Naruto"` `--series-index 1` — tên bộ + số tập (auto-derive title nếu --title omit)
- `--lang ja` (default) — language code
- `--year` — năm xuất bản
- `--subject` — default "Manga"
- `--publisher` — nhà xuất bản
- `--description` — mô tả

**Display flags**:
- `--rtl` (default true) — right-to-left spine direction (manga Nhật mặc định)
- `--spread-reset 5,12` — manually re-anchor page-spread cadence at given pages (e.g. after color cover)
- `--min-px 400` (default) — drop images smaller than 400px (warns on drop, avoids tiny thumbnails)

**Cover detection**:
- `--cover-index N` (default 1) — trang (1-based, sau lọc min-px) dùng làm bìa. Nếu bản scanlation chèn banner+bìa-sau trước bìa thật, trỏ tới index đúng (vd 3).
- `--auto-cover` — dò bìa tự động qua vision LLM (cần `OPENROUTER_API_KEY`). Gửi vài trang đầu + hỏi model "trang nào là bìa trước thật?" → trả index 1-based. Mô hình không thấy bìa (tập bắt đầu giữa truyện) → fallback index 1. Lỗi mạng/parse → fallback index 1, build tiếp (cover không load-bearing như OCR). Manual `--cover-index N` (N≠1) đè `--auto-cover` (skip LLM, không tốn cost, in cảnh báo).
- `--model <id>` — vision model cho `--auto-cover` (default = model OCR từ env `OCR_MODEL` hoặc `qwen3.7-plus`). Kỳ lạ: auto-cover là tiện ích, mặc định manga vẫn $0/offline, chỉ opt-in khi dùng `--auto-cover` mới gọi LLM.

**Other flags**:
- `--home <dir>` — custom data root (default `~/scan2ebook`)

**Spread cadence** (RTL): cover & landscape images → `page-spread-center`; portrait images alternate `page-spread-right/left` starting right for RTL reading order.

**Example**: Tải toàn bộ manga từ Google Drive folder, build EPUB:
```bash
scan2ebook manga bleach \
  --from "https://drive.google.com/drive/folders/1Ax..." \
  --title "Bleach" --author "Kubo Tite" \
  --series "Bleach" --series-index 1
```

Auto-detect bìa từ LLM (require API key):
```bash
scan2ebook manga bleach --from ~/scans --series "Bleach" --series-index 1 --auto-cover
```

Auto series-title (khỏi gõ title, từ series+index tự derive):
```bash
scan2ebook manga pluto-taps --from ~/scans --series "Pluto" --series-index 2
# → dc:title = "Pluto 02" (auto-derive), không cần --title
```

Rebuild từ existing `scans/` (sau khi chỉnh sửa metadata):
```bash
scan2ebook manga bleach
```

Output: `dist/<slug>.epub` — stable EPUB identity across rebuilds (uuid5 based on slug).

## Chạy full OCR pipeline

Khi đã verify smoke test (hoặc bỏ qua `--smoke` cho sách trusted), chạy `all` để gộp 3 stage (OCR + post + epub).

```bash
scan2ebook all namphong-q01
```

**Output path**: Lệnh in đường dẫn output tuyệt đối ở đầu. Mặc định output sẽ ở `~/scan2ebook/namphong-q01/` với ba zone: `scans/` (source), `work/` (cache), `dist/` (deliverable). Override data root với `--home <path>` hoặc env var `$SCAN2EBOOK_HOME`.

```bash
# Dùng env var SCAN2EBOOK_HOME
export SCAN2EBOOK_HOME=$HOME/MyEbooks
scan2ebook all namphong-q01
# → sách sẽ ở $HOME/MyEbooks/namphong-q01/{scans,work,dist}

# Hoặc cờ --home
scan2ebook all namphong-q01 --home ~/custom-ebooks
```

**Tiến trình**: Real-time console output: mỗi page báo `ok latency=X.Ys in=A out=B`, cuối stage 1 báo tổng cost. Stage 2 báo số page merged, char count, h1/h2 count. Stage 3 báo size epub.

Wall-clock cho ~330 trang, parallel 12 worker (default), khoảng 25–30 phút (verified). Có thể chạy nền (background hoặc `nohup`), pipeline không cần tương tác.

Sau khi xong, mở epub trên Mac.

```bash
open ~/scan2ebook/namphong-q01/dist/namphong-q01.epub
# Books.app sẽ tự mở
```

Verify TOC, dấu Việt, chapter split, metadata. Nếu cần chỉnh title/author, edit `work/book.md` (YAML front matter ở đầu) và rerun `scan2ebook epub` để rebuild.

## Upload Drive

Sau khi verify local OK, upload Drive.

```bash
scan2ebook upload ~/scan2ebook/namphong-q01/dist/namphong-q01.epub --rename "Nam Phong Q01.epub"
```

Hoặc tích hợp trong `all`.

```bash
scan2ebook all namphong-q01 --upload
```

Default folder Drive là `Ebooks`. Override qua `--folder` nếu muốn folder khác.

```bash
scan2ebook upload book.epub --folder "Ebooks/Việt cổ"
```

## Chỉnh sửa thủ công

Nếu OCR có lỗi nhỏ (vài chữ sai, footnote sai số), chỉnh trực tiếp file `.md` trung gian thay vì rerun OCR (tốn cost).

Per-page chỉnh ở `work/ocr/page_NNN.md`. Sau khi chỉnh, rerun stage 2+3.

```bash
scan2ebook post ~/scan2ebook/namphong-q01/work/ocr ~/scan2ebook/namphong-q01/work/book.md \
  --title "Nam Phong Tạp Chí Q01 (1917)" \
  --author "Phạm Quỳnh"

scan2ebook epub ~/scan2ebook/namphong-q01/work/book.md ~/scan2ebook/namphong-q01/dist/namphong-q01.epub
```

Book-level chỉnh ở `work/book.md`. Sau khi chỉnh, chỉ cần rerun stage 3.

```bash
scan2ebook epub ~/scan2ebook/namphong-q01/work/book.md ~/scan2ebook/namphong-q01/dist/namphong-q01.epub
```

YAML front matter ở đầu `book.md` chứa metadata. Chỉnh trực tiếp cũng được, pandoc đọc đúng.

## Sách bị chia nhiều quyển

Một số sách (tạp chí định kỳ, sách nhiều tập) muốn build từng quyển riêng. Tạo nhiều book folder.

```
~/scan2ebook/
├── namphong-q01/      # Quyển 1
├── namphong-q02/
└── namphong-q03/
```

Mỗi folder build riêng.

```bash
for slug in namphong-q01 namphong-q02 namphong-q03; do
  scan2ebook all $slug --upload
done
```

Hoặc gộp nhiều quyển thành 1 epub bằng cách copy tất cả page vào 1 scans folder và đặt tên `page_001.png` đến `page_NNN.png` liên tục.

## Sách scan kém chất lượng

Nếu scan có vấn đề (mờ, lệch, nền vàng nâu), thử các tip sau trước khi chạy lại pipeline.

DPI thấp: rescan với 300 DPI minimum.

Nền vàng/loang: app scan của vFlat có chế độ "Document Mode" auto adjust contrast. Bật trước khi scan.

Trang bị nghiêng: vFlat auto deskew nhưng đôi lúc fail. Adobe Scan deskew tốt hơn.

Bóng đèn rõ trên page: chụp ngoài trời hoặc dưới đèn LED bàn, tránh đèn trần trực tiếp.

Mực mờ (sách cũ): tăng exposure +1 stop khi scan. Vision model tolerate mờ tốt hơn pattern OCR (Tesseract).

Trang trống (bìa, divider): để nguyên trong inbox. Pipeline tự nhận diện trang trống thật (response rỗng + `finish_reason=stop`) và ghi placeholder `<!-- blank page -->`, **không cần can thiệp tay**, không tính là fail.

## Scripting & JSON output

Cho agent/CI integration, dùng `--json` hoặc `--json-lines` trên `ocr` / `all` để output machine-readable format (human logs sang stderr).

```bash
# --json: 1 summary object cuối (human log ra stderr)
scan2ebook all ~/Books-inbox/namphong-q01 --json > book-summary.json 2>run.log

# --json-lines: NDJSON stream mỗi event (progress ra stderr)
scan2ebook ocr ~/Books-inbox/namphong-q01 ~/output/ocr --json-lines > events.ndjson 2>progress.log
```

**JSON summary schema** (cho `--json` mode cuối): `{"status":"ok"|"partial"|"error"|"smoke"|"dry-run", "stage":"ocr"|"all"|"smoke", "pages":{...}, "cost_usd", "paths":{...}, ...}`. Smoke gate trả `"status":"smoke"` kèm `"est_full_cost_usd"`, `"remaining_pages"`, `"total_pages"` để agent/script quyết định re-invoke với `--yes`. Lưu ý: key trong `pages` phụ thuộc `status` — `dry-run` dùng `{todo,skipped,total}`, run thật dùng `{ok,blank,fail,skipped,total}`, `error` có thể là `{}`; parse theo `status` trước.

**NDJSON format** (cho `--json-lines`): Mỗi dòng 1 event JSON phẳng `{"event":"<kind>", ...payload}` (payload merge thẳng vào object, không lồng). Event types: `start`, `page_ok`, `page_blank`, `page_fail`, `done`. Ví dụ: `{"event":"page_ok","page":"page_001","latency_s":1.2,"in":900,"out":1200,"dst":"..."}`.

## Lệnh tham khảo

| Lệnh | Mục đích |
|---|---|
| `scan2ebook doctor` | Self-check môi trường (python/pandoc/key/rclone) |
| `scan2ebook doctor --json` | Self-check, JSON output |
| `scan2ebook init <slug> --from <dir\|book.pdf\|drive-link>` | Tạo book + scans zone + import ảnh / render PDF / tải Drive + metadata mẫu |
| `scan2ebook init <slug> --from <dir> --lang ja` | Init với ngôn ngữ Nhật (OCR dọc, RTL spread, vi mặc định) |
| `scan2ebook manga <slug> --from <dir\|.mobi\|.cbz\|drive-url>` | Build EPUB3 fixed-layout RTL manga (4 input forms); slug = folder name hoặc path |
| `scan2ebook manga <slug> --series "Pluto" --series-index 2` | Manga với auto series-title (dc:title = "Pluto 02") |
| `scan2ebook manga <slug> --auto-cover` | Auto-detect bìa qua vision LLM (cần OPENROUTER_API_KEY) |
| `scan2ebook manga <slug> --cover-index 3` | Chỉ định trang bìa (1-based, sau lọc min-px) |
| `scan2ebook manga <slug> --auto-cover --model google/gemini-3.1-pro-preview` | Auto-cover với model vision tùy chọn |
| `scan2ebook manga <slug> --spread-reset 5,12 --min-px 400` | Manga với tuning cadence + min pixel |
| `scan2ebook ocr <slug-or-path> <out>` | Stage 1: OCR per page (slug hoặc explicit book-home path) |
| `scan2ebook ocr <slug-or-path> <out> --dry-run` | Đếm trang + ước lượng chi phí, không gọi API |
| `scan2ebook ocr <slug-or-path> <out> --limit 10` | OCR tối đa 10 trang đầu |
| `scan2ebook ocr <slug-or-path> <out> --workers 16` | Parallel cao hơn default 12 (cẩn thận rate limit) — `--workers 4` để hạ nếu hay 429 |
| `scan2ebook ocr <slug-or-path> <out> --max-tokens 16000` | Tăng trần output cho trang text rất dày |
| `scan2ebook ocr <slug-or-path> <out> --model <id>` | Đổi vision model (hoặc đặt env `OCR_MODEL`) |
| `scan2ebook ocr <slug-or-path> <out> --json` | JSON summary output |
| `scan2ebook post <ocr-dir> <book.md> --title "..."` | Stage 2: merge → book.md |
| `scan2ebook epub <book.md> <book.epub>` | Stage 3: build epub (cover từ auto-detect hoặc cover.jpg) |
| `scan2ebook epub <book.md> <book.epub> --cover <path>` | Override cover (one-off, không lưu vào context) |
| `scan2ebook upload <book.epub>` | Stage 4: rclone → Drive |
| `scan2ebook upload <book.epub> --rename "..."` | Rename khi upload |
| `scan2ebook all <slug>` | 3 stage chain (slug-or-path) |
| `scan2ebook all <slug> --upload` | 4 stage chain |
| `scan2ebook all <slug> --smoke` | Cost gate: OCR 10 trang + mini epub + confirm |
| `scan2ebook all <slug> --smoke --yes` | Cost gate + bypass prompt (agent mode) |
| `scan2ebook all <slug> --home <path>` | Custom data root |
| `scan2ebook all <slug> --json` | JSON summary output |
| `scan2ebook all <slug> --json-lines` | NDJSON stream output (progress + summary) |
