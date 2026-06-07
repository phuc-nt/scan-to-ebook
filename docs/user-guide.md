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

Nếu chưa có credit OpenRouter, nạp $5–10 để start. Cost dao động $0.05/page A4 với Gemini 3.1 Pro Preview, một quyển 200 trang khoảng $10.

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

Cách nhanh nhất: dùng lệnh `init` để tạo inbox + import ảnh + rename tự động (xem mục "Tạo inbox nhanh" bên dưới). Pipeline dùng **natural-sort** nên tên file không bắt buộc zero-pad — `page_5.png`..`page_80.png` vẫn sort đúng số học. Tuy vậy đặt `page_001.png`, `page_002.png`... vẫn gọn và dễ đọc hơn.

Nếu muốn rename thủ công (app scan đặt tên khác như vFlat `IMG_001.png`):

```bash
cd ~/Books-inbox/<slug>/
ls *.png | nl | while read n f; do
  mv "$f" "$(printf 'page_%03d.png' $n)"
done
```

Pipeline nhận cả **PNG, JPG/JPEG, HEIC, HEIF** — HEIC/HEIF tự convert qua chain backend (sips macOS → magick ImageMagick → heif-convert → pillow-heif, first available). Cross-platform: Windows/macOS/Linux tất cả supported. Nếu NO backend available, raise error (mất trang = sách hỏng, never skip silent).

DPI tối thiểu khuyến nghị là 300 DPI cho text rõ ràng. Vision model tolerate được DPI thấp hơn nhưng dấu Việt có thể đoán sai.

Thư mục scans hoàn chỉnh trông như sau (ở `~/scan2ebook/<slug>/scans/`).

```
~/scan2ebook/namphong-q01/scans/
├── page_001.png
├── page_002.png
├── ...
├── page_075.png
├── metadata.json
└── cover.jpg (optional)
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

`cover.jpg` optional trong `scans/`. Nếu có, pandoc tự embed thành bìa epub. Ảnh đẹp nhất là 1600x2400 portrait, nhưng pandoc accept mọi kích thước.

## Tạo inbox nhanh

Lệnh `init` gộp các bước chuẩn bị (tạo folder + scans zone, copy ảnh, rename `page_NNN`, sinh `metadata.json` mẫu) thành một lệnh.

```bash
scan2ebook init namphong-q01 --from ~/Desktop/scan-output \
  --title "Nam Phong Tạp Chí Q01 (1917)" --author "Phạm Quỳnh"
```

Kết quả: tạo `~/scan2ebook/namphong-q01/scans/`, copy + natural-sort + rename ảnh từ `--from` thành `page_001.<ext>`..., và ghi `metadata.json` vào `scans/`. HEIC/HEIF file tự động convert→JPG trong quá trình này (EXIF + orientation được giữ nguyên). Override data root bằng `--home`.

Bỏ `--from` nếu muốn tự copy ảnh sau (lệnh chỉ tạo folder + metadata mẫu). `metadata.json` đã tồn tại sẽ được giữ nguyên, không ghi đè. Nếu `scans/` đã có file `page_*`, `init --from` sẽ báo lỗi (rc=2) thay vì import — xoá page cũ trước rồi chạy lại, tránh để lại page rác bị OCR nhầm (tốn tiền).

## Bối cảnh sách (context.json)

Trước khi OCR từng trang, pipeline tự động trích bối cảnh sách (title, author, translator, pages_per_image, table_of_contents, proper names, terminology, layout notes) từ 15 sample ảnh. Kết quả lưu thành `context.json` và `context.md` trong thư mục `work/`.

**context.json** (source-of-truth, hand-editable)
- Tệp JSON cấu trúc chứa metadata sách và OCR guidance
- **Hand-editable**: nếu phát hiện lỗi (ví dụ `pages_per_image` detect sai 1 thay vì 2), edit trực tiếp JSON và re-run (cache hit, cost 0)
- Khi resume (chạy lại sách cũ), pipeline kiểm tra `work/context.json` tồn tại → skip API, re-derive guidance từ JSON
- Thay đổi tay: sửa `pages_per_image`, tên riêng, hay tên sách trong JSON → chạy lại sẽ sử dụng giá trị mới

**context.md** (mirror, chỉ đọc)
- Render đã format của `context.json`, dùng để xem nhanh bên ngoài editor
- Header comment: "edit context.json to change" — file này tự động generate từ JSON
- Chỉnh sửa `context.md` đơn lẻ **sẽ bị bỏ qua** (JSON là authoritative), chỉnh sửa không có tác dụng

**Ví dụ**: sách dịch từ Pháp, OCR detect đúng translator nhưng sai tên riêng. Mở `~/scan2ebook/namphong-q01/work/context.json`, tìm `"proper_names"` array, sửa "Miraben" → "Miraudy" (canonical), save. Chạy lại `scan2ebook all namphong-q01`, pipeline sẽ dùng context cache này (cost 0) và OCR toàn bộ với tên đúng.

## Smoke test sách mới (--smoke gate)

Khi scan sách mới chưa từng test, đừng chạy thẳng full pipeline — cost rủi ro $10+ nếu OCR ra rác. Dùng **`--smoke`** để test 10 trang đầu, build mini epub, ước cost full, rồi gate xác nhận trước chi tiền.

```bash
scan2ebook all namphong-q01 --smoke
```

**Flow chi tiết:**
1. OCR ≤10 trang đầu (~$0.50) → ghi vào `work/ocr/`
2. Build mini epub `work/book.smoke.epub` từ 10 trang để preview
3. Ước cost cho phần còn lại: `(số trang còn lại) × giá/trang đo thật từ smoke` (cost token-based của 10 trang smoke chia ra; chính xác hơn flat $0.05, fallback $0.05 nếu smoke 0 trang ok)
4. **Interactive prompt**: `Full run ≈ $X.XX cho Y trang còn lại. Continue? [y/N]`
   - Gõ `y` hoặc `Y` → tiếp tục full pipeline (resume-safe, 10 trang đã OCR sẽ skip)
   - Gõ `n` hoặc Enter (default) → dừng, giữ smoke epub để review

**Kiểm tra smoke epub**: Mở `~/scan2ebook/namphong-q01/work/book.smoke.epub` trên Books.app để check chất lượng. Cần kiểm tra:

- **Dấu tiếng Việt**: có đúng không (chữ ô, ấ, ầ, ậ, ẩ, ẫ, ơ, ờ, ớ, ợ). Nếu bị bỏ dấu hoặc đoán sai, OCR model gặp khó với scan → thử raise DPI scan hoặc đổi model qua `--model`.
- **Chính tả cổ** (nếu sách cổ): có giữ nguyên không. "Văn-chương" có hyphen, "chánh" giữ nguyên (không sửa "chính"). Nếu modernize, model có bias — tránh Qwen3 VL, GLM 4.5V.
- **Layout 2 cột**: nối đúng không. Đọc 2-3 đoạn, xem có flow tự nhiên hay xen cột.
- **Heading**: detect được không. Xem `# Chương` có promote trong epub.

Nếu smoke OK, confirm at prompt để chạy full. Nếu vấn đề, abort, fix input/scan, rồi chạy `--smoke` lại (resumable).

**Non-interactive mode** (CI/script): Dùng `--yes` để bỏ qua prompt.
```bash
scan2ebook all ~/Books-inbox/namphong-q01 --smoke --yes
```

Nếu không tty (pipe/non-interactive) mà không có `--yes`, abort an toàn (không treo `input()`).

## Chạy full pipeline

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

Wall-clock cho 200 trang, parallel 4 worker, khoảng 30–45 phút. Có thể chạy nền (background hoặc `nohup`), pipeline không cần tương tác.

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
| `scan2ebook init <slug> --from <dir>` | Tạo book + scans zone + import ảnh + metadata mẫu |
| `scan2ebook ocr <slug-or-path> <out>` | Stage 1: OCR per page (slug hoặc explicit book-home path) |
| `scan2ebook ocr <slug-or-path> <out> --dry-run` | Đếm trang + ước lượng chi phí, không gọi API |
| `scan2ebook ocr <slug-or-path> <out> --limit 10` | OCR tối đa 10 trang đầu |
| `scan2ebook ocr <slug-or-path> <out> --workers 8` | Parallel cao hơn (cẩn thận rate limit) |
| `scan2ebook ocr <slug-or-path> <out> --max-tokens 16000` | Tăng trần output cho trang text rất dày |
| `scan2ebook ocr <slug-or-path> <out> --model <id>` | Đổi vision model (hoặc đặt env `OCR_MODEL`) |
| `scan2ebook ocr <slug-or-path> <out> --json` | JSON summary output |
| `scan2ebook post <ocr-dir> <book.md> --title "..."` | Stage 2: merge → book.md |
| `scan2ebook epub <book.md> <book.epub>` | Stage 3: build epub |
| `scan2ebook epub <book.md> <book.epub> --cover cover.jpg` | Embed cover |
| `scan2ebook upload <book.epub>` | Stage 4: rclone → Drive |
| `scan2ebook upload <book.epub> --rename "..."` | Rename khi upload |
| `scan2ebook all <slug>` | 3 stage chain (slug-or-path) |
| `scan2ebook all <slug> --upload` | 4 stage chain |
| `scan2ebook all <slug> --smoke` | Cost gate: OCR 10 trang + mini epub + confirm |
| `scan2ebook all <slug> --smoke --yes` | Cost gate + bypass prompt (agent mode) |
| `scan2ebook all <slug> --home <path>` | Custom data root |
| `scan2ebook all <slug> --json` | JSON summary output |
| `scan2ebook all <slug> --json-lines` | NDJSON stream output (progress + summary) |
