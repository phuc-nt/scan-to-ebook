# Kiến trúc

## Pipeline 5 stage

Pipeline chia thành 5 stage tuần tự, mỗi stage là một module Python độc lập trong `src/scan_to_ebook/`. Mỗi stage đọc filesystem state của stage trước và ghi output xuống filesystem cho stage sau. Không có in-memory queue, không có database, không có process daemon.

**Import (prep stage)**: Lệnh `init --from` tự động xử lý ba loại input:
- **Thư mục ảnh** → copy + rename thành `page_NNN.<ext>`.
- **File PDF local** → gọi `pdf_render.py` để render từng trang thành JPG qua backend-chain (pdftoppm → magick → sips).
- **Google Drive file link** → gọi `drive_download.py` để tải PDF temp, validate magic bytes `%PDF`, xử lý interstitial confirm-token tự động, sau đó render như PDF local.
- **HEIC/HEIF** → tự convert→JPG.

Kết quả: `scans/` chỉ chứa `page_NNN.jpg/png`, sẵn sàng cho OCR — không file PDF/HEIC thô nào được truyền qua stage 1+. **PDF render strategy**: Luôn render→OCR (không trích text layer) vì PDF born-digital thường có ToUnicode CMap hỏng. Render→OCR xử lý nhất quán cả PDF scan lẫn text-layer-broken. **Drive link validation**: Phải public, FILE link (không folder), bắt buộc validate PDF content.

**Stage 0: Context Pre-Pass** (mới). Trước khi OCR từng trang, pipeline gọi `context_prepass.py` để trích bối cảnh sách từ 15 sample ảnh (7 trang đầu + 4 giữa + 4 cuối, natural-sort). Một multi-image call duy nhất tới OpenRouter để detect title, author, translator, publisher, year, `pages_per_image` (LLM tự phát hiện xem ảnh có 1 hay 2 trang), mục lục, tên riêng + chính tả chuẩn, thuật ngữ, layout, footnote convention, OCR pitfalls, **và `cover_page` (ảnh bìa màu nếu thấy)** — persist vào `work/context.json` (source-of-truth, hand-editable) + `work/context.md` (mirror render, có note "edit .json thay vì file này). Read sample từ `scans/`, write cache vào `work/` — giữ `scans/` nguyên vẹn, clean-room resume = `rm -rf work/`. **Ngoại lệ duy nhất ghi vào `scans/`**: nếu `metadata.json` còn mặc định (title == slug, do `init` tạo TRƯỚC prepass), prepass backfill title/author/year/translator thật (dò được) ngược vào `scans/metadata.json` để TOC/title-page hiện tên sách thật thay vì slug. Guard `title == slug` đảm bảo KHÔNG đè title user tự đặt; ctx không có title → no-op (giữ slug). Prepass **cache-aware**: nếu `work/context.json` đã tồn tại hợp lệ → skip API, cost 0, re-derive block từ JSON. Prepass **abort-on-fail**: API error hay JSON parse fail → emit `context_fail` event + abort pipeline (exit != 0, không tiêu OCR cost). Mẫu ảnh downscale ~1200px (cross-platform: sips macOS → magick ImageMagick → heif-convert → pillow-heif, first available) để tránh HTTP 413; ảnh gốc full-res giữ nguyên cho OCR thực. Spread guidance ("ẢNH TRANG ĐÔI") được render vào block CHỈ khi `pages_per_image >= 2` (conditional per-book, không hardcode base PROMPT). Smoke + full share một prepass (cost counted once, full = cache hit). **Cover/colophon rule**: Block luôn cấm OCR dùng `## `/`### ` heading cho trang bìa/tựa đề (đầu) và trang thông tin xuất bản/colophon (cuối: tên sách, tác giả, NXB, giấy phép, giá bán) — để dạng đoạn thường tránh pandoc `--toc` nhặt chữ trang trí vào mục lục.

**Stage 1: OCR** (trước gọi là Stage 1). Input là một thư mục PNG/JPG (HEIC/HEIF đã convert→JPG tại import stage), mỗi file là một trang trong `scans/`. Output là một thư mục `.md` trong `work/ocr/`, mỗi trang một file `page_NNN.md`. Module này gọi OpenRouter API qua urllib stdlib (không có httpx/requests dependency), encode ảnh base64, gửi prompt verified cho Gemini 3.1 Pro Preview + context block từ Stage 0 append vào (base PROMPT giữ byte-for-byte unchanged), nhận markdown thuần về. ThreadPoolExecutor chạy 4 worker song song (configurable `--workers`) để tận dụng concurrent HTTP. Resumable: trước khi gọi API cho mỗi trang, check xem `work/ocr/page_NNN.md` đã có và non-empty chưa, nếu có thì skip. File `.md` ghi atomic (tmp + `os.replace`) để write bị ngắt giữa chừng không để lại file nửa-ghi đánh lừa resume check. Retry 2 lần với exponential backoff (1s, 2.5s, 5s) trên transient errors (HTTP 429, HTTP 5xx, timeout, empty content, malformed JSON). Trang trống thật (response rỗng + `finish_reason=stop`) KHÔNG retry mà tự ghi placeholder `<!-- blank page -->`, không tính fail. CLI hỗ trợ `--dry-run` (đếm trang todo + ước lượng chi phí, không gọi API), `--max-tokens` (default 12000), và env `OCR_MODEL` override model mặc định.

Stage 2 là post-process. Input là thư mục `.md` từ stage 1 (ở `work/ocr/`). Output là một file `book.md` đơn lẻ với YAML front matter, ghi vào `work/`. Module này merge tất cả `page_*.md` theo thứ tự filename (natural-sort số học), strip `` ```markdown `` wrapper nếu model lỡ thêm dù prompt cấm, renumber footnote cross-page (mỗi trang OCR đánh `[^1]` độc lập → shift theo counter chạy để `book.md` không đụng số, tránh pandoc "Duplicate note reference"; bỏ qua `[^N]` trong fenced code block), detect chapter heading bằng regex (CHƯƠNG / Chương / PHẦN / Phần / HỒI / QUYỂN / THIÊN + số La Mã, decimal, hoặc số viết chữ như "thứ nhất", "mười") rồi promote thành h1, và inject YAML metadata (title, author, lang, year) để pandoc dùng. Cross-page hyphen-fix intentionally dropped — sẽ giải thích ở phần Design decisions.

Stage 3 là epub build. Input là `work/book.md`. Output là `dist/<slug>.epub` (tên theo thư mục book-home). Module này subprocess pandoc với flags `--toc --toc-depth=2 --split-level=1`. Pandoc đọc YAML front matter làm metadata, chia spine theo `# ` heading, sinh TOC tự động. Embed cover (nếu có) qua `--epub-cover-image` theo thứ tự ưu tiên: (1) `scans/cover.jpg` — manual override nếu user tự đặt; (2) `context.json` → `cover_page` — auto-detect từ stage 0 (tên file ảnh bìa màu, vd `page_001.jpg`); (3) không có cover nếu cả hai đều missing (sách scan trắng đen). Cover page (nếu là page của sách) vẫn được OCR vào body, tránh mất text — bìa chỉ là ảnh minh hoạ, text bìa cùng appear dạng đoạn thường ở đầu EPUB (không dùng heading tránh TOC). Sau khi build, kiểm tra file magic bằng `file` command để đảm bảo output thực sự là EPUB chứ không phải file rỗng do pandoc lỗi.

Stage 4 là upload (optional). Input là `dist/<slug>.epub`. Output là file trên Google Drive. Module này subprocess `rclone copy` (hoặc `copyto` nếu cần rename) tới remote đã cấu hình. Mặc định remote name là `gdrive`, folder `Ebooks`, có thể override qua CLI flag. Module này tách riêng và optional — pipeline core dừng ở stage 3, stage 4 chỉ chạy khi user explicit qua `--upload`.

## Data flow

```
~/scan2ebook/<slug>/scans/             (user-managed source, never auto-deleted)
├── page_001.png
├── page_002.png
├── metadata.json
└── cover.jpg (optional; manual override)
            │
            ▼  Stage 0: context_prepass.py (auto-detects cover_page + other context)
~/scan2ebook/<slug>/work/              (cache zone, rm -rf work/ = clean-room reset)
├── context.json (source-of-truth, hand-editable; includes cover_page)
└── context.md (render, mirror)
            │ (cached for resume + full: no API cost)
            ▼  Stage 1: ocr.py (context block appended to PROMPT)
~/scan2ebook/<slug>/work/ocr/          (auto-created)
├── page_001.md
├── page_002.md
└── ...
            │
            ▼  Stage 2: post_process.py
~/scan2ebook/<slug>/work/
└── book.md
            │
            ▼  Stage 3: epub_build.py
~/scan2ebook/<slug>/dist/
└── <slug>.epub (named after book-home directory)
            │
            ▼  Stage 4: drive_upload.py (optional)
gdrive:Ebooks/<slug>.epub
```

Đường dẫn book-home (data root) xác định từ precedence: `--home <path>` hoặc `--output <path>` > env `$SCAN2EBOOK_HOME` > env `$SCAN2EBOOK_OUTPUT_ROOT` (DEPRECATED, emits stderr warning "deprecated") > `~/scan2ebook` (default). Mỗi sách là một thư mục `~/scan2ebook/<slug>/` với ba zone riêng: `scans/` (source images + metadata, never auto-deleted), `work/` (cache + temp, `rm -rf work/` reset cache để chạy clean-room lần mới), `dist/` (final deliverable `.epub`). Lợi ích: source vĩnh viễn, cache có thể xoá, deliverable tập trung.

## File layout repo

```
scan-to-ebook/
├── README.md              # landing, minimal
├── LICENSE                # MIT
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
    ├── pipeline.py        # Orchestration + shared helpers (import, metadata, path resolve)
    ├── pdf_render.py      # PDF → page-image render (pdftoppm/magick/sips backend-chain)
    ├── image_ops.py       # HEIC→JPG cross-platform convert + downscale
    ├── drive_download.py  # Google Drive file link → temp PDF (stdlib urllib, public link only)
    ├── context_prepass.py # Stage 0
    ├── ocr.py             # Stage 1
    ├── post_process.py    # Stage 2
    ├── epub_build.py      # Stage 3
    ├── drive_upload.py    # Stage 4
    └── cli.py             # argparse + cmd_ocr/post/epub/upload/all
```

Mỗi stage là một module độc lập, có thể import và gọi trực tiếp từ Python script khác nếu cần. CLI là một layer mỏng dùng argparse, không có business logic ngoài việc parse args và gọi function của stage tương ứng.

## Thiết kế quyết định

Vì sao Gemini 3.1 Pro Preview làm default model? Phase 0 spike đã so 6 vision model (Gemini 2.5 Pro, Claude Opus 4.6, Qwen3 VL, GLM 4.5V, Gemini 3.1 Pro Preview, GPT-4o) trên 5 trang Nam Phong 1917. Gemini 3.1 Pro Preview cho zero lỗi với chính tả Việt cổ và giá $0.05/page — rẻ hơn Claude Opus 4x, không modernize bias như Qwen3/GLM (sửa "chính" sang "chánh"). Phase 3 verified zero regression trên 75 trang full Nam Phong Q01. Default ổn định ít nhất tới khi OpenRouter cập nhật giá hoặc Gemini 4 ra mắt.

Vì sao render PDF→page-image thay vì trích text layer? PDF born-digital (ví dụ Calibre, Quartz export) thường có ToUnicode CMap hỏng — pdftotext yield ký tự rác, trong khi ảnh render qua Gemini OCR đọc sạch. Render→OCR là đường duy nhất nhất quán xử lý được cả PDF scan lẫn PDF text-layer-broken. Backend-chain (pdftoppm → magick → sips) mirror pattern image_ops.py, cross-platform, lên lỗi thì báo hướng dẫn cài theo OS. Không có text-layer extraction path.

Vì sao stdlib urllib thay vì httpx/requests? OpenRouter API đơn giản, một POST endpoint, không cần streaming, không cần auth library phức tạp. Stdlib có sẵn, không cần `pip install`, không có version conflict. Token cost lớn nhất là OCR cost chứ không phải HTTP overhead.

Vì sao ThreadPoolExecutor chứ không async? OpenRouter rate limit khoảng 60 req/min cho free tier, 600 req/min cho paid. Với latency 30–60s/page, parallel 4 worker đã đủ saturate. Async sẽ phức tạp code mà không cải thiện throughput. Worker count có thể tăng lên 8 cho sách dài nếu API allow.

Vì sao resumable qua filesystem state thay vì SQLite/Redis? Một file `.md` per page là source of truth tự nhiên cho stage 2 đọc anyway. Check `.exists()` + `.size > 0` là idempotency check đủ tốt. Không có database = không có migration, không có corrupt state, không có lock contention.

Vì sao natural-sort thay vì `sorted()` mặc định? Filename scan thường không zero-pad (`page_5`..`page_80`). `sorted()` so sánh string nên xếp `page_10` trước `page_5`, đẩy trang đầu sách (bìa, mục lục) xuống cuối `book.md` — sai thứ tự đọc, silent. `natural_sort_key()` (trong `ocr.py`) tách cụm số trong filename thành int rồi sort số học. Dùng chung cho cả `collect_pending_pages` (OCR order) và `merge_pages` (book order). Stem không có số sort trước (tie-break bằng stem) — an toàn cho cover/intro page.

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

Stage 1 retry tự động 2 lần trên transient (429, 5xx, timeout, empty content, malformed JSON response). Sau retry vẫn fail thì raise exception, count vào failures. Non-transient (403, 400, 401) fail luôn lần đầu — không retry vì là config/auth/quota error, retry vô ích.

Malformed JSON response (body bị cắt/đứt giữa chừng do provider stream lỗi) tính là transient: trang text dày response lớn dễ bị truncate, retry thường thành công. `_post_once` wrap `json.loads` trong try/except, re-raise với marker `malformed response` để `ocr_page` nhận diện. `max_tokens` default 12000 (tăng từ 8000) để trang dày không chạm trần token gây cắt output.

## Module boundaries

`ocr.py` xuất `run_batch()`, `ocr_page()`, `require_api_key()`. Function `run_batch` nhận `on_event` callback cho progress logging — cho phép CLI in real-time mà module không bind vào print.

`post_process.py` xuất `merge_pages()`. Function thuần, không I/O ngoài file đọc/ghi rõ ràng.

`epub_build.py` xuất `build_epub()`. Subprocess pandoc, return dict với output path, size, warnings.

`drive_upload.py` xuất `upload()`. Subprocess rclone, return dict với local path, remote path.

`cli.py` import từng module, define subcommand handler. Không có shared state global. Mỗi subcommand độc lập, có thể test riêng.

## Testing strategy

Có `tests/test_page_order_and_retry.py` (pytest) cover 2 regression: natural-sort page order (sort key, merge order, collect order) và retry classification (transient incl. malformed JSON retry tới 3 lần; non-transient 4xx fail ngay). Chạy `python -m pytest`. Test thủ công bằng pilot Nam Phong 1917 (75 trang, có ở `tests/input/Nam Phong Tap Chi Q01_QN_001-006_T001/`). Future test suite nên thêm:

Unit test cho `post_process.py` (chapter detection, code fence strip, YAML build) — pure function, không cần fixture lớn.

Unit test cho `epub_build.py` dùng fixture markdown 2 trang đơn giản, verify pandoc output có EPUB magic và unzip được.

Integration test cho `ocr.py` cần mock OpenRouter response (urllib `Request` patching) hoặc dùng VCR cassette. Real API call tốn cost, không phù hợp CI.

End-to-end test có thể dùng 2 trang Nam Phong làm fixture, ghi expected `.md` output để diff. Smoke test này là regression guard tốt nhất cho prompt changes.
