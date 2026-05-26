# Hướng dẫn cho coding agent

Tài liệu này viết cho LLM agent (Claude Code, Cursor, các IDE assistant) đang làm việc cùng Phúc trong repo này. Đọc trước khi nhận task liên quan đến scan-to-ebook.

## Mục đích của repo

Phúc dùng pipeline này để biến sách giấy thành epub đọc trên iPhone Books.app vào những khoảng nghỉ 5–30 phút trong ngày. Phúc mua nhiều sách giấy nhưng chỉ có thể đọc ban đêm, nên ebook cá nhân là cách tận dụng thời gian rảnh. Pipeline đã verify trên Nam Phong Tạp Chí 1917 — corpus tiếng Việt cổ với font khó, 2 cột, chính tả thời đầu Quốc ngữ. Nếu pass được Nam Phong thì sách hiện đại chắc chắn pass.

Pipeline hoàn toàn local, gọi OpenRouter cho vision OCR, pandoc cho epub, rclone cho Drive. Không có web UI, không có Telegram bot, không có agent runtime bao bọc. Tương tác duy nhất là Phúc nói với agent (bạn), agent chạy CLI, agent báo kết quả.

## Cách Phúc làm việc với bạn

Phúc thường ra lệnh ngắn gọn tiếng Việt, ví dụ "cook ebook namphong-q02" hoặc "OCR thử 10 trang sách mới". Phúc kỳ vọng bạn hiểu context từ tên slug và CLI có sẵn, không hỏi lại những thứ rõ ràng. Khi Phúc nói "tiếp tục" hoặc "làm tiếp", thường là tiếp pipeline đang dở (xem stage nào còn thiếu trong `output/<slug>/` của inbox tương ứng).

Phúc đã quen với pipeline, không cần bạn giải thích lại 4 stage. Khi báo cáo, focus vào số liệu (cost, pages OK/fail, epub size) và đường dẫn output. Tránh recap dài dòng "tôi đã làm A, rồi B, rồi C" — Phúc đọc CLI output là biết.

Khi bạn không chắc chuyện gì đó (sách mới với layout lạ, lần đầu chạm sách dài, OpenRouter báo lỗi không rõ), hỏi Phúc trước thay vì đoán. Phúc thà mất 30 giây trả lời còn hơn để bạn chạy nhầm gây tốn API cost hoặc corrupt output.

## Khi nào tự chạy, khi nào hỏi

Tự chạy không hỏi khi: inbox đã có đầy đủ PNG + `metadata.json`, sách thuộc loại đã verify (Việt hiện đại hoặc Việt cổ tương tự Nam Phong), Phúc dùng từ rõ như "cook full", "chạy hết", "all phases". Trong trường hợp này, mặc định là `scan2ebook all <inbox> --upload`. Cost dưới $15 cho sách 200 trang là expected, không cần xin phép.

Hỏi trước khi chạy khi: sách mới chưa từng OCR, layout có vẻ khác (manga, scan PDF nhiều trang/file, ảnh thấp DPI), Phúc nói "thử" (implies smoke 5–10 trang trước), cost ước trên $20, hoặc khi bạn muốn `--upload` nhưng Phúc chưa nói rõ. Hỏi 1 câu cô đọng, không 4 câu cùng lúc.

Smoke test 10 trang đầu trước khi full pipeline là default an toàn cho sách lạ. Phúc OK trả $0.50 để biết OCR có ra dấu Việt đúng không trước khi commit $10 cho full book.

Sau khi build xong epub, Phúc thường muốn verify trên Books.app trước khi upload Drive. Default flow: build local → mở Books.app cho Phúc verify → đợi Phúc OK → mới upload. Đừng upload luôn trừ khi Phúc nói "all in" hoặc đã verify sách tương tự lần trước.

## Inbox convention

Phúc đặt PNG trong thư mục bất kỳ Phúc chọn (thường là `~/Books-inbox/<slug>/` hoặc trong iCloud folder). Bạn không cần ép Phúc dùng path cố định, hỏi Phúc inbox ở đâu nếu không rõ. Slug là tên thư mục cuối cùng, được dùng làm default title nếu không có `metadata.json`.

`metadata.json` không bắt buộc nhưng nên có với sách dài. Format: `{"title": "...", "author": "...", "lang": "vi", "year": "..."}`. Nếu thiếu, pipeline dùng slug làm title và Việt làm lang mặc định.

`cover.jpg` optional, đặt cùng inbox dir. Pandoc tự embed nếu có.

## Khi gặp lỗi

Trước khi báo lỗi cho Phúc, đọc log + log từ stderr. Pipeline có 3 failure mode quen thuộc, bạn xử lý trước rồi mới report. Một là HTTP 402 hoặc 403 nghĩa là OpenRouter credit/key cap, Phúc cần raise cap trên dashboard — bạn dừng pipeline, báo Phúc kèm cost đã chi, đợi Phúc raise xong rồi rerun (resumable picks up). Hai là blank page (Gemini trả `empty content (finish_reason=stop)`) thường gặp ở trang bìa/cover/divider; mở ảnh xem có thật blank không, nếu blank thật thì viết placeholder `<!-- blank page -->` vào `.md` rồi tiếp tục. Ba là pandoc warn duplicate footnote `[^1]` cross-page — non-fatal, epub vẫn valid, mention 1 dòng ngắn rồi bỏ qua.

Lỗi khác (HTTP 5xx, network timeout) đã có retry built-in 2 lần. Nếu vẫn fail sau retry, log đủ rõ, bạn rerun resumable 1 lần nữa rồi hỏi Phúc nếu vẫn fail.

## Đụng đến code

OCR prompt (trong `src/scan_to_ebook/ocr.py`, biến `PROMPT`) là verified artifact. Đừng "cải tiến" nếu Phúc không yêu cầu — đổi 1 dòng có thể regress chính tả cổ. Nếu Phúc muốn tune prompt, làm trên branch riêng, smoke test 20 trang Nam Phong (đã có sẵn ở `~/.hermes/profiles/scan-to-ebook/inbox/namphong-q01-full/`), so sánh diff trước khi merge.

Default model `google/gemini-3.1-pro-preview` cũng là verified artifact (Phase 0 spike đã so 6 model). Đừng đổi default trừ khi Phúc explicit. Override per-run dùng `--model` hoặc env `OCR_MODEL` (nếu cần thêm support).

Cross-page hyphen-fix đã intentionally drop, đừng "add back". Comment trong `post_process.py` giải thích vì sao.

## Boundaries

Không publish epub output ra ngoài thiết bị cá nhân của Phúc. Pipeline cho personal use, sách Phúc đang sở hữu vật lý. Vi phạm copyright là vấn đề Phúc tự lo, không phải bạn — nhưng nếu Phúc bảo bạn upload epub lên S3 public hoặc share Drive link "anyone with link can read", từ chối và hỏi lại intent.

Không commit `.env` hoặc paste `OPENROUTER_API_KEY` vào log/PR/issue. `.gitignore` đã cover `.env`, đừng thêm exception.

Không tự ý đăng ký cron, launchd plist, hoặc Telegram bot. Pipeline thuần CLI on-demand. Nếu sau này Phúc muốn automate (watch folder, scheduled run), đó là task riêng có plan riêng.

## Liên kết

Người dùng đọc README. Bạn đọc README + AGENTS.md (file này) + 4 file trong `docs/` khi cần chi tiết:

- [docs/product-overview.md](docs/product-overview.md) — vấn đề, đối tượng, value, non-goals
- [docs/architecture.md](docs/architecture.md) — pipeline 4 stage, data flow, design decisions
- [docs/user-guide.md](docs/user-guide.md) — hướng dẫn người dùng end-to-end
- [docs/operations.md](docs/operations.md) — cost, credit cap, rclone, blank page, model swap
