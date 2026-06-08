# Tổng quan sản phẩm

## Vấn đề

Người Việt mua sách giấy nhiều — tiệm sách cũ, hội chợ, hiệu sách online — nhưng cơ hội đọc giảm dần khi cuộc sống bận. Sách bản cứng đọc tốt nhất khi ngồi yên có ánh sáng, thường là ban đêm. Khoảng nghỉ 5–30 phút trong ngày (đợi xe, ngồi quán, lúc làm việc nhà) thì không tiện rút sách giấy ra. Smartphone luôn trong túi, nhưng nội dung đọc trên đó hiếm khi là sách mình đã mua.

Mua lại ebook bản digital tốn tiền 2 lần và nhiều sách Việt cũ (xuất bản trước 2000, tạp chí cổ, sách dịch hiếm) chưa bao giờ có bản ebook chính thức. Scan thủ công + OCR truyền thống (Tesseract, ABBYY) cho corpus tiếng Việt có dấu kém, đặc biệt với font cổ hoặc chính tả thời đầu Quốc ngữ.

## Đối tượng

Người dùng cá nhân muốn đọc lại sách mình sở hữu vật lý trên thiết bị di động. Pipeline này không phải sản phẩm thương mại, không phải dịch vụ public, không phải tool để pirate ebook. Giả định người dùng có sách giấy trong tay, có chút kỹ thuật để chạy CLI Python, có OpenRouter API key, có chỗ lưu output an toàn.

Cụ thể hơn, target user là một người: (1) đọc nhiều, (2) chấp nhận trả $5–15 OpenRouter cost cho một quyển sách để có ebook cá nhân, (3) thoải mái với pipeline command-line không có UI, (4) tự chịu trách nhiệm về copyright với sách mình scan.

## Đề xuất giá trị

Pipeline giải quyết 3 việc cùng lúc. Việc thứ nhất là OCR tiếng Việt chất lượng cao kể cả với corpus cổ (1900–1950) nhờ Gemini 3.1 Pro hiểu cả ngữ cảnh chứ không chỉ pattern-match ký tự — verified zero error trên 75 trang Nam Phong 1917. Việc thứ hai là đầu ra epub có TOC, chapter split, metadata đầy đủ, đọc thẳng trên Books.app/Kindle không cần xử lý thêm. Việc thứ ba là toàn bộ chạy local trên máy người dùng, output local, không có server bên ngoài lưu sách của bạn — chỉ ảnh được gửi lên OpenRouter cho OCR rồi xoá khỏi pipeline.

Chi phí thực tế khoảng $0.05/trang A4 cho Gemini 3.1 Pro, tức $10 cho sách 200 trang. So với mua lại ebook ($5–20 mỗi quyển nếu có) thì cùng tầm giá nhưng cover được cả sách không tồn tại bản ebook. So với scan ngoài tiệm (~$30/quyển + chậm + chất lượng OCR Tesseract kém) thì rẻ và nhanh hơn nhiều.

## Phạm vi

Pipeline làm 4 việc theo thứ tự cố định.

Đầu vào là một trong ba loại:
1. **Thư mục ảnh** — PNG, JPG, HEIC hoặc HEIF, mỗi ảnh một trang (từ app scan vFlat, Adobe Scan, ScannerPro hoặc scanner phẳng).
2. **File PDF** — local file (từ Calibre, app scanner, v.v.) hoặc tải từ Google Drive link công khai.
3. **Google Drive file link** — file PDF shared công khai; pipeline tải và xử lý như PDF local.

PDF bất kỳ (scan hay born-digital) render từng trang thành JPG qua backend-chain (pdftoppm/magick/sips, first available). **Strategy PDF**: Luôn render→OCR, không trích text layer, vì PDF born-digital thường có ToUnicode CMap hỏng → pdftotext yield ký tự rác. HEIC/HEIF (mặc định iPhone) tự động convert→JPG tại stage import (`init --from`). Google Drive file phải công khai ("Bất kỳ ai có link"); file private/folder link fail với thông báo rõ. Do đó OCR stage cuối cùng chỉ nhận JPG/PNG. `scans/` có thể có thêm `metadata.json` và `cover.jpg` (tùy chọn; manual override).

Đầu ra là một file `.epub` (tự động include cover nếu detect được hoặc user đặt manual) cùng các file `.md` trung gian (per-page và book-level) để người dùng có thể chỉnh sửa thủ công nếu cần trước khi build epub lại.

Stage giữa là parallel OCR (12 worker default) với resumable state lưu trên filesystem — page nào đã có `.md` non-empty thì skip khi rerun.

Sau khi build epub, người dùng có thể tự upload Drive qua lệnh CLI riêng (rclone) nếu muốn đồng bộ giữa nhiều thiết bị.

## Ngoài phạm vi (non-goals)

Pipeline không scan ảnh tự động bằng OCR offline (Tesseract, EasyOCR). Lý do: chất lượng tiếng Việt kém, không xứng đáng so với cost $0.05/page Gemini.

Pipeline không có web UI, không có notification (Telegram/email), không có folder watcher tự động trigger. Mọi action đều on-demand qua CLI. Lý do: complexity không cần thiết cho personal tool, dễ ăn rác/spam, tăng surface bug.

Pipeline không cung cấp tính năng share, publish, hoặc host epub. Output local-only, người dùng tự xử lý sync (Drive, Dropbox, AirDrop). Lý do: copyright legal là vấn đề người dùng tự lo, tool không nên facilitate publish.

Pipeline không support ngôn ngữ khác ngoài tiếng Việt làm default. Lang code có thể override qua `metadata.json`, prompt OCR có thể tune cho ngôn ngữ khác, nhưng đó là user customization chứ không phải built-in. Lý do: corpus calibration tốn thời gian, mỗi ngôn ngữ cần verify riêng.

## Quan hệ với các tool khác

Pipeline forked từ một prototype trong Hermes Agent framework (5/2026). Ban đầu định làm dạng skill trong agent profile với Telegram trigger và folder watcher cron. Sau khi pilot 75 trang Nam Phong Q01 thành công, nhận ra phần agent/Telegram thừa cho personal use case — pipeline thuần stdlib + pandoc + rclone hoạt động đủ tốt, agent runtime chỉ thêm sandbox HOME issue làm hỏng credential access.

Repo standalone này là phiên bản clean, có thể chạy trên bất kỳ máy macOS/Linux nào có Python 3.10+, pandoc, và rclone (rclone optional cho upload). Không có Hermes dependency, không có Telegram, không có cron.
