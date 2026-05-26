# Kiến trúc

## Pipeline 4 stage

Pipeline chia thành 4 stage tuần tự, mỗi stage là một module Python độc lập trong `src/scan_to_ebook/`. Mỗi stage đọc filesystem state của stage trước và ghi output xuống filesystem cho stage sau. Không có in-memory queue, không có database, không có process daemon.

Stage 1 là OCR. Input là một thư mục PNG/JPG, mỗi file là một trang. Output là một thư mục `.md`, mỗi trang một file `page_NNN.md`. Module này gọi OpenRouter API qua urllib stdlib (không có httpx/requests dependency), encode ảnh base64, gửi prompt verified cho Gemini 3.1 Pro Preview, nhận markdown thuần về. ThreadPoolExecutor chạy 4 worker song song (configurable `--workers`) để tận dụng concurrent HTTP. Resumable: trước khi gọi API cho mỗi trang, check xem `output/page_NNN.md` đã có và non-empty chưa, nếu có thì skip. Retry 2 lần với exponential backoff (1s, 2.5s, 5s) trên transient errors (HTTP 429, HTTP 5xx, timeout, empty content).

Stage 2 là post-process. Input là thư mục `.md` từ stage 1. Output là một file `book.md` đơn lẻ với YAML front matter. Module này merge tất cả `page_*.md` theo thứ tự filename, strip `` ```markdown `` wrapper nếu model lỡ thêm dù prompt cấm, detect chapter heading bằng regex (CHƯƠNG / Chương / PHẦN / Phần + số La Mã hoặc decimal) rồi promote thành h1, và inject YAML metadata (title, author, lang, year) để pandoc dùng. Cross-page hyphen-fix intentionally dropped — sẽ giải thích ở phần Design decisions.

Stage 3 là epub build. Input là `book.md`. Output là `book.epub`. Module này subprocess pandoc với flags `--toc --toc-depth=2 --split-level=1`. Pandoc đọc YAML front matter làm metadata, chia spine theo `# ` heading, sinh TOC tự động. Optional `cover.jpg` embed qua `--epub-cover-image`. Sau khi build, kiểm tra file magic bằng `file` command để đảm bảo output thực sự là EPUB chứ không phải file rỗng do pandoc lỗi.

Stage 4 là upload (optional). Input là `book.epub`. Output là file trên Google Drive. Module này subprocess `rclone copy` (hoặc `copyto` nếu cần rename) tới remote đã cấu hình. Mặc định remote name là `gdrive`, folder `Ebooks`, có thể override qua CLI flag. Module này tách riêng và optional — pipeline core dừng ở stage 3, stage 4 chỉ chạy khi user explicit qua `--upload`.

## Data flow

```
~/Books-inbox/<slug>/                  (user-managed input)
├── page_001.png
├── page_002.png
├── metadata.json (optional)
└── cover.jpg (optional)
            │
            ▼  Stage 1: ocr.py
~/Books-inbox/../output/<slug>/ocr/    (auto-created)
├── page_001.md
├── page_002.md
└── ...
            │
            ▼  Stage 2: post_process.py
~/Books-inbox/../output/<slug>/
└── book.md
            │
            ▼  Stage 3: epub_build.py
~/Books-inbox/../output/<slug>/
└── book.epub
            │
            ▼  Stage 4: drive_upload.py (optional)
gdrive:Ebooks/<title>.epub
```

Đường dẫn output mặc định là `<inbox-parent>/../output/<slug>/`, có thể override qua `--output`. Nếu inbox là `~/Books-inbox/foo/` thì output là `~/Books-inbox/../output/foo/` tức `~/output/foo/`. Hơi quirky, nhưng giữ inbox và output ngang cấp thay vì nested.

## File layout repo

```
scan-to-ebook/
├── README.md              # landing, minimal
├── AGENTS.md              # agent interaction protocol
├── pyproject.toml         # hatchling, scan2ebook entry point
├── .env.example
├── .gitignore
├── docs/
│   ├── product-overview.md
│   ├── architecture.md    # file này
│   ├── user-guide.md
│   └── operations.md
└── src/scan_to_ebook/
    ├── __init__.py
    ├── ocr.py             # Stage 1
    ├── post_process.py    # Stage 2
    ├── epub_build.py      # Stage 3
    ├── drive_upload.py    # Stage 4
    └── cli.py             # argparse + cmd_ocr/post/epub/upload/all
```

Mỗi stage là một module độc lập, có thể import và gọi trực tiếp từ Python script khác nếu cần. CLI là một layer mỏng dùng argparse, không có business logic ngoài việc parse args và gọi function của stage tương ứng.

## Thiết kế quyết định

Vì sao Gemini 3.1 Pro Preview làm default model? Phase 0 spike đã so 6 vision model (Gemini 2.5 Pro, Claude Opus 4.6, Qwen3 VL, GLM 4.5V, Gemini 3.1 Pro Preview, GPT-4o) trên 5 trang Nam Phong 1917. Gemini 3.1 Pro Preview cho zero lỗi với chính tả Việt cổ và giá $0.05/page — rẻ hơn Claude Opus 4x, không modernize bias như Qwen3/GLM (sửa "chính" sang "chánh"). Phase 3 verified zero regression trên 75 trang full Nam Phong Q01. Default ổn định ít nhất tới khi OpenRouter cập nhật giá hoặc Gemini 4 ra mắt.

Vì sao stdlib urllib thay vì httpx/requests? OpenRouter API đơn giản, một POST endpoint, không cần streaming, không cần auth library phức tạp. Stdlib có sẵn, không cần `pip install`, không có version conflict. Token cost lớn nhất là OCR cost chứ không phải HTTP overhead.

Vì sao ThreadPoolExecutor chứ không async? OpenRouter rate limit khoảng 60 req/min cho free tier, 600 req/min cho paid. Với latency 30–60s/page, parallel 4 worker đã đủ saturate. Async sẽ phức tạp code mà không cải thiện throughput. Worker count có thể tăng lên 8 cho sách dài nếu API allow.

Vì sao resumable qua filesystem state thay vì SQLite/Redis? Một file `.md` per page là source of truth tự nhiên cho stage 2 đọc anyway. Check `.exists()` + `.size > 0` là idempotency check đủ tốt. Không có database = không có migration, không có corrupt state, không có lock contention.

Vì sao cross-page hyphen-fix bị drop? Corpus tiếng Việt cổ (Nam Phong 1917) dùng hyphen có chủ đích cho từ ghép như "văn-chương", "nhân-loại", "luân-lý". Pattern auto-nối `word-\n\s*word` ở biên page sẽ corrupt "văn-chương" thành "vănchương" khi từ này rơi đúng biên trang — silent corruption, khó detect, regression chính tả. OCR prompt rule 8 đã handle hyphen line-break trong page (an toàn vì hyphen line-break dễ nhận biết hơn). Cross-page hyphen rất hiếm, không an toàn auto-fix.

Vì sao pandoc CLI thay vì ebooklib Python? Pandoc xử lý markdown→epub tốt nhất hiện có, đặc biệt với footnote, TOC, YAML metadata. Ebooklib cần code wrapper dài hơn, support kém hơn. Pandoc là 1 brew/apt package, available trên macOS/Linux/Windows. Subprocess overhead negligible so với OCR cost.

Vì sao rclone thay vì Google Drive Python API? Rclone xử lý OAuth flow một lần qua `rclone config`, credential lưu ở chỗ rclone tự quản (`~/.config/rclone/`). Không phụ thuộc Python google-api-python-client (heavy, lots of deps). Không vướng sandbox HOME issue như Hermes gws skill — rclone là binary độc lập, không bị wrap qua agent runtime. Bonus: rclone support tất cả cloud (Dropbox, S3, Backblaze), user có thể swap remote backend mà không đụng code pipeline.

Vì sao YAML front matter trong markdown thay vì `--metadata` pandoc flag? YAML front matter survive khi user mở `book.md` chỉnh sửa thủ công và rebuild — metadata không bị mất. Pandoc đọc YAML tự động, không cần extra CLI flag, không cần parse `metadata.json` trong build script.

## Resumability

Pipeline resumable ở stage 1 (OCR). Khi rerun, mỗi page check `output/ocr/page_NNN.md` đã tồn tại và size > 0. Nếu có thì skip. Edge case: nếu page OCR fail giữa chừng (network drop), `.md` không được write ra → rerun retry. Nếu page OCR thành công nhưng disk write fail → rất hiếm, user phải xóa thủ công file nếu nội dung corrupt.

Stage 2 và 3 idempotent: rerun với cùng input cho cùng output. Stage 2 ghi đè `book.md`, stage 3 ghi đè `book.epub`. Không có incremental, vì merge + pandoc đều nhanh (giây) so với OCR (phút).

Stage 4 dùng `rclone copy` (skip nếu file Drive đã tồn tại với cùng size+mtime) hoặc `rclone copyto` (upload mới khi rename). Idempotent theo nghĩa rclone — rerun không tạo duplicate.

## Error model

Mỗi stage raise exception khi fatal (input không tồn tại, API auth fail, pandoc crash, rclone fail). CLI bắt exception, in stderr, return non-zero exit code. Subprocess error (pandoc, rclone) được capture stdout/stderr và format thành RuntimeError với context.

Stage 1 không raise khi 1 page fail — chỉ count vào `summary['failures']` và print stderr per-page. Mục đích: cho phép pipeline tiếp tục các page khác, user retry sau (resumable). CLI return code 1 nếu có bất kỳ fail nào, để shell script biết.

Stage 1 retry tự động 2 lần trên transient (429, 5xx, timeout, empty content). Sau retry vẫn fail thì raise exception, count vào failures. Non-transient (403, 400, 401) fail luôn lần đầu — không retry vì là config/auth/quota error, retry vô ích.

## Module boundaries

`ocr.py` xuất `run_batch()`, `ocr_page()`, `require_api_key()`. Function `run_batch` nhận `on_event` callback cho progress logging — cho phép CLI in real-time mà module không bind vào print.

`post_process.py` xuất `merge_pages()`. Function thuần, không I/O ngoài file đọc/ghi rõ ràng.

`epub_build.py` xuất `build_epub()`. Subprocess pandoc, return dict với output path, size, warnings.

`drive_upload.py` xuất `upload()`. Subprocess rclone, return dict với local path, remote path.

`cli.py` import từng module, define subcommand handler. Không có shared state global. Mỗi subcommand độc lập, có thể test riêng.

## Testing strategy

Hiện tại chưa có test suite. Test thủ công bằng pilot Nam Phong 1917 (75 trang, có sẵn ở `~/.hermes/profiles/scan-to-ebook/inbox/namphong-q01-full/`). Future test suite nên có:

Unit test cho `post_process.py` (chapter detection, code fence strip, YAML build) — pure function, không cần fixture lớn.

Unit test cho `epub_build.py` dùng fixture markdown 2 trang đơn giản, verify pandoc output có EPUB magic và unzip được.

Integration test cho `ocr.py` cần mock OpenRouter response (urllib `Request` patching) hoặc dùng VCR cassette. Real API call tốn cost, không phù hợp CI.

End-to-end test có thể dùng 2 trang Nam Phong làm fixture, ghi expected `.md` output để diff. Smoke test này là regression guard tốt nhất cho prompt changes.
