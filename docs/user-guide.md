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

Verify cài thành công.

```bash
.venv/bin/scan2ebook --help
```

Tạo file `.env` chứa OpenRouter API key. Lấy key tại https://openrouter.ai/keys, sau đó.

```bash
cp .env.example .env
# Mở .env bằng editor, paste key vào sau OPENROUTER_API_KEY=
```

Pipeline **tự nạp `.env`** (tìm ở thư mục hiện tại rồi repo root) nên không cần `source .env` mỗi shell. Nếu đã `export OPENROUTER_API_KEY` sẵn thì biến đó được ưu tiên (không bị `.env` ghi đè). Parser `.env` đơn giản: chỉ `KEY=value` mỗi dòng (bỏ comment đầu dòng, strip nháy bao ngoài). KHÔNG hỗ trợ prefix `export ` hay comment inline (`KEY=val # ...`) — viết thuần `KEY=value`.

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

Sách giấy cần được scan thành PNG hoặc JPG, mỗi trang một file. App Phúc đang dùng là vFlat trên iPhone — auto crop, auto deskew, output PNG. Adobe Scan và ScannerPro cũng tốt. Tránh chụp thường bằng camera vì lệch perspective.

Cách nhanh nhất: dùng lệnh `init` để tạo inbox + import ảnh + rename tự động (xem mục "Tạo inbox nhanh" bên dưới). Pipeline dùng **natural-sort** nên tên file không bắt buộc zero-pad — `page_5.png`..`page_80.png` vẫn sort đúng số học. Tuy vậy đặt `page_001.png`, `page_002.png`... vẫn gọn và dễ đọc hơn.

Nếu muốn rename thủ công (app scan đặt tên khác như vFlat `IMG_001.png`):

```bash
cd ~/Books-inbox/<slug>/
ls *.png | nl | while read n f; do
  mv "$f" "$(printf 'page_%03d.png' $n)"
done
```

Pipeline nhận cả **PNG lẫn JPG/JPEG** — không cần convert.

DPI tối thiểu khuyến nghị là 300 DPI cho text rõ ràng. Vision model tolerate được DPI thấp hơn nhưng dấu Việt có thể đoán sai.

Thư mục inbox hoàn chỉnh trông như sau.

```
~/Books-inbox/namphong-q01/
├── page_001.png
├── page_002.png
├── ...
├── page_075.png
├── metadata.json
└── cover.jpg
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

`cover.jpg` optional. Nếu có, pandoc tự embed thành bìa epub. Ảnh đẹp nhất là 1600x2400 portrait, nhưng pandoc accept mọi kích thước.

## Tạo inbox nhanh

Lệnh `init` gộp các bước chuẩn bị (tạo folder, copy ảnh, rename `page_NNN`, sinh `metadata.json` mẫu) thành một lệnh.

```bash
scan2ebook init namphong-q01 --from ~/Desktop/scan-output \
  --title "Nam Phong Tạp Chí Q01 (1917)" --author "Phạm Quỳnh"
```

Kết quả: tạo `~/Books-inbox/namphong-q01/`, copy + natural-sort + rename ảnh từ `--from` thành `page_001.<ext>`..., và ghi `metadata.json` từ các cờ `--title/--author/--lang/--year`. Đổi thư mục gốc bằng `--base`.

Bỏ `--from` nếu muốn tự copy ảnh sau (lệnh chỉ tạo folder + metadata mẫu). `metadata.json` đã tồn tại sẽ được giữ nguyên, không ghi đè. Nếu inbox đã có file `page_*`, `init --from` sẽ báo lỗi (rc=2) thay vì import — xoá page cũ trước rồi chạy lại, tránh để lại page rác bị OCR nhầm (tốn tiền).

## Smoke test sách mới

Khi scan sách mới chưa từng test, đừng chạy thẳng full pipeline. Cost rủi ro $10+ nếu OCR ra rác. Test 10 trang đầu trước.

```bash
scan2ebook ocr ~/Books-inbox/namphong-q01 ~/output/namphong-q01/ocr --limit 10
```

Tốn khoảng $0.50. Mở vài file `.md` bằng editor để check chất lượng. Cần check.

Dấu tiếng Việt có đúng không. Đặc biệt chữ ô, ấ, ầ, ậ, ẩ, ẫ, ơ, ờ, ớ, ợ. Nếu bị bỏ dấu hoặc đoán sai dấu, có thể OCR model gặp khó với scan của bạn — thử raise DPI scan hoặc đổi model qua `--model`.

Chính tả cổ có giữ nguyên không (nếu sách cổ). "Văn-chương" có giữ hyphen không, "chánh" có bị sửa thành "chính" không. Nếu bị modernize, model đang dùng có bias — tránh Qwen3 VL, GLM 4.5V cho corpus cổ.

Layout 2 cột có nối đúng không. Đọc thử 2-3 đoạn xem có flow tự nhiên hay bị xen cột trái với cột phải.

Heading có được detect không. Mở `book.md` (chạy stage 2 trước) để xem `# Chương` có promote không.

Nếu OCR 10 trang ra OK, tiếp tục full book. Resumable nên 10 trang đã chạy sẽ skip.

## Chạy full pipeline

Khi đã verify smoke test, chạy `all` để gộp 3 stage (OCR + post + epub).

```bash
scan2ebook all ~/Books-inbox/namphong-q01
```

Output sẽ ở `~/Books-inbox/../output/namphong-q01/` (tức `~/output/namphong-q01/` nếu inbox đặt ở `~/Books-inbox/`).

Tiến trình in ra console real-time: mỗi page báo `ok latency=X.Ys in=A out=B`, cuối stage 1 báo tổng cost. Stage 2 báo số page merged, char count, h1/h2 count. Stage 3 báo size epub.

Wall-clock cho 200 trang, parallel 4 worker, khoảng 30–45 phút. Có thể chạy nền (background hoặc `nohup`), pipeline không cần tương tác.

Sau khi xong, mở epub trên Mac.

```bash
open ~/output/namphong-q01/book.epub
# Books.app sẽ tự mở
```

Verify TOC, dấu Việt, chapter split, metadata. Nếu cần chỉnh title/author, edit `book.md` (YAML front matter ở đầu) và rerun `scan2ebook epub` để rebuild.

## Upload Drive

Sau khi verify local OK, upload Drive.

```bash
scan2ebook upload ~/output/namphong-q01/book.epub --rename "Nam Phong Q01.epub"
```

Hoặc tích hợp trong `all`.

```bash
scan2ebook all ~/Books-inbox/namphong-q01 --upload
```

Default folder Drive là `Ebooks`. Override qua `--folder` nếu muốn folder khác.

```bash
scan2ebook upload book.epub --folder "Ebooks/Việt cổ"
```

## Chỉnh sửa thủ công

Nếu OCR có lỗi nhỏ (vài chữ sai, footnote sai số), chỉnh trực tiếp file `.md` trung gian thay vì rerun OCR (tốn cost).

Per-page chỉnh ở `output/<slug>/ocr/page_NNN.md`. Sau khi chỉnh, rerun stage 2+3.

```bash
scan2ebook post ~/output/namphong-q01/ocr ~/output/namphong-q01/book.md \
  --title "Nam Phong Tạp Chí Q01 (1917)" \
  --author "Phạm Quỳnh"

scan2ebook epub ~/output/namphong-q01/book.md ~/output/namphong-q01/book.epub
```

Book-level chỉnh ở `output/<slug>/book.md`. Sau khi chỉnh, chỉ cần rerun stage 3.

```bash
scan2ebook epub ~/output/namphong-q01/book.md ~/output/namphong-q01/book.epub
```

YAML front matter ở đầu `book.md` chứa metadata. Chỉnh trực tiếp cũng được, pandoc đọc đúng.

## Sách bị chia nhiều quyển

Một số sách (tạp chí định kỳ, sách nhiều tập) muốn build từng quyển riêng. Tạo nhiều inbox folder.

```
~/Books-inbox/
├── namphong-q01/      # Quyển 1
├── namphong-q02/
└── namphong-q03/
```

Mỗi folder build riêng.

```bash
for slug in namphong-q01 namphong-q02 namphong-q03; do
  scan2ebook all ~/Books-inbox/$slug --upload
done
```

Hoặc gộp nhiều quyển thành 1 epub bằng cách copy tất cả page vào 1 inbox và đặt tên `page_001.png` đến `page_NNN.png` liên tục.

## Sách scan kém chất lượng

Nếu scan có vấn đề (mờ, lệch, nền vàng nâu), thử các tip sau trước khi chạy lại pipeline.

DPI thấp: rescan với 300 DPI minimum.

Nền vàng/loang: app scan của vFlat có chế độ "Document Mode" auto adjust contrast. Bật trước khi scan.

Trang bị nghiêng: vFlat auto deskew nhưng đôi lúc fail. Adobe Scan deskew tốt hơn.

Bóng đèn rõ trên page: chụp ngoài trời hoặc dưới đèn LED bàn, tránh đèn trần trực tiếp.

Mực mờ (sách cũ): tăng exposure +1 stop khi scan. Vision model tolerate mờ tốt hơn pattern OCR (Tesseract).

Trang trống (bìa, divider): để nguyên trong inbox. Pipeline tự nhận diện trang trống thật (response rỗng + `finish_reason=stop`) và ghi placeholder `<!-- blank page -->`, **không cần can thiệp tay**, không tính là fail.

## Lệnh tham khảo

| Lệnh | Mục đích |
|---|---|
| `scan2ebook init <slug> --from <dir>` | Tạo inbox + import ảnh + metadata mẫu |
| `scan2ebook ocr <inbox> <out>` | Stage 1: OCR per page |
| `scan2ebook ocr <inbox> <out> --dry-run` | Đếm trang + ước lượng chi phí, không gọi API |
| `scan2ebook ocr <inbox> <out> --limit 10` | Smoke test 10 trang đầu |
| `scan2ebook ocr <inbox> <out> --workers 8` | Parallel cao hơn (cẩn thận rate limit) |
| `scan2ebook ocr <inbox> <out> --max-tokens 16000` | Tăng trần output cho trang text rất dày |
| `scan2ebook ocr <inbox> <out> --model <id>` | Đổi vision model (hoặc đặt env `OCR_MODEL`) |
| `scan2ebook post <ocr-dir> <book.md> --title "..."` | Stage 2: merge → book.md |
| `scan2ebook epub <book.md> <book.epub>` | Stage 3: build epub |
| `scan2ebook epub <book.md> <book.epub> --cover cover.jpg` | Embed cover |
| `scan2ebook upload <book.epub>` | Stage 4: rclone → Drive |
| `scan2ebook upload <book.epub> --rename "..."` | Rename khi upload |
| `scan2ebook all <inbox>` | 3 stage chain |
| `scan2ebook all <inbox> --upload` | 4 stage chain |
