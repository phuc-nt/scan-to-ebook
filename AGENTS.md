# Hướng dẫn cho coding agent

Tài liệu này viết cho LLM agent (Claude Code, Cursor, các IDE assistant) đang làm việc cùng Phúc trong repo này. Đọc trước khi nhận task liên quan đến scan-to-ebook.

## Mục đích của repo

Phúc dùng pipeline này để biến sách giấy thành epub đọc trên iPhone Books.app vào những khoảng nghỉ 5–30 phút trong ngày. Phúc mua nhiều sách giấy nhưng chỉ có thể đọc ban đêm, nên ebook cá nhân là cách tận dụng thời gian rảnh. Pipeline đã verify trên Nam Phong Tạp Chí 1917 — corpus tiếng Việt cổ với font khó, 2 cột, chính tả thời đầu Quốc ngữ. Nếu pass được Nam Phong thì sách hiện đại chắc chắn pass.

Pipeline hoàn toàn local, gọi OpenRouter cho vision OCR, pandoc cho epub, rclone cho Drive. Không có web UI, không có Telegram bot, không có agent runtime bao bọc. Tương tác duy nhất là Phúc nói với agent (bạn), agent chạy CLI, agent báo kết quả.

## Cách Phúc làm việc với bạn

Phúc thường ra lệnh ngắn gọn tiếng Việt, ví dụ "cook ebook namphong-q02" hoặc "OCR thử 10 trang sách mới". Phúc kỳ vọng bạn hiểu context từ tên slug và CLI có sẵn, không hỏi lại những thứ rõ ràng. Khi Phúc nói "tiếp tục" hoặc "làm tiếp", thường là tiếp pipeline đang dở (xem stage nào còn thiếu trong `output/<slug>/` của inbox tương ứng).

Phúc đã quen với pipeline, không cần bạn giải thích lại 4 stage. Khi báo cáo, focus vào số liệu (cost, pages OK/fail, epub size) và đường dẫn output. Tránh recap dài dòng "tôi đã làm A, rồi B, rồi C" — Phúc đọc CLI output là biết.

Khi bạn không chắc chuyện gì đó (sách mới với layout lạ, lần đầu chạm sách dài, OpenRouter báo lỗi không rõ), hỏi Phúc trước thay vì đoán. Phúc thà mất 30 giây trả lời còn hơn để bạn chạy nhầm gây tốn API cost hoặc corrupt output.

## Workflow agent-friendly (RECOMMENDED)

Pipeline đã support workflow dành cho agent: cost-gate, JSON output, non-interactive mode. **Đây là cách Phúc muốn agent hoạt động.**

### Bước 1: Verify môi trường

Trước khi chạy bất kỳ task, khởi động bằng `doctor --json`:

```bash
scan2ebook doctor --json
```

Output:
```json
{
  "status": "ok",
  "checks": [
    {"name": "python", "ok": true, "essential": true, "detail": "Python 3.14.3"},
    {"name": "pandoc", "ok": true, "essential": true, "detail": "pandoc 3.9.0.2"},
    {"name": "openrouter_key", "ok": true, "essential": true, "detail": "present"},
    {"name": "rclone", "ok": false, "essential": false, "detail": "not installed (upload disabled, optional)"}
  ]
}
```

`status` là `"ok"` hoặc `"fail"`. Exit 0 iff essential checks (`python`, `pandoc`, `openrouter_key`) pass. Rclone absent là warning chứ không fail. **NEVER in giá trị key vào log** — `openrouter_key.detail` chỉ là `"present"`/`"missing ..."`. Agent kiểm tra `status == "ok"` rồi tiếp tục, nếu `fail` báo Phúc.

### Bước 2: Smoke test sách mới (cost-gate)

**MANDATORY: Agent MUST NOT tự động pass `--yes` lần chạy đầu của sách mới.** Smoke gate là cách để surface cost cho Phúc trước khi chi tiền.

Chạy smoke với `--json` (không `--yes`):

```bash
scan2ebook all ~/Books-inbox/<slug> --smoke --json
```

Output: Đúng 1 JSON object (human logs sang stderr):

```json
{
  "status": "smoke",
  "stage": "smoke",
  "pages": {"ok": 10, "blank": 0, "fail": 0, "skipped": 0, "total": 75},
  "cost_usd": 0.517,
  "paths": {
    "ocr_dir": "/Users/phucnt/output/namphong-q01/ocr",
    "smoke_epub": "/Users/phucnt/output/namphong-q01/book.smoke.epub"
  },
  "est_full_cost_usd": 3.3605,
  "remaining_pages": 65,
  "total_pages": 75,
  "message": "pass --yes to run full"
}
```

Exit code: 0 (đây là gate an toàn, KHÔNG phải lỗi).

**Agent action**: Đọc `est_full_cost_usd` từ JSON → báo Phúc chi phí dự tính → **đợi Phúc confirm rõ ràng trước khi tiếp**.

### Bước 3: Full run (sau khi Phúc approve)

Chỉ khi Phúc approved, chạy với `--yes --json`:

```bash
scan2ebook all ~/Books-inbox/<slug> --yes --json
# hoặc thêm --upload nếu Phúc cho phép:
scan2ebook all ~/Books-inbox/<slug> --yes --json --upload
```

Smoke OCR (10 trang) resume-safe: lần full chạy không re-OCR cái 10 trang đó (xem logs, sẽ báo `skipped=10`). Không double-spend API cost.

Output JSON: cùng schema nhưng `status: ok|partial|error` (không còn `smoke`).

### Bước 4: Exit code handling

| Exit code | Ý nghĩa | Action |
|-----------|---------|--------|
| 0 | ok (hoặc deliberate smoke/dry-run gate) | done |
| 1 | partial (vài trang fail) hoặc build fail | rerun `scan2ebook ocr <inbox> <ocr_dir>` để retry trang fail |
| 2 | input/usage error | fix args rồi thử lại |

### JSON output modes

- `--json`: Một JSON object duy nhất ở cuối stdout; human logs sang stderr. Agent dùng mode này để parse kết quả.
- `--json-lines`: NDJSON stream (mỗi dòng 1 event JSON), human logs sang stderr. Dùng để streaming progress.

### Output path resolution

Pipeline in absolute path output ở đầu (stderr nếu `--json`). Precedence:
1. `--output <path>` (nếu có) → output tại `<path>/<slug>/`
2. Env `$SCAN2EBOOK_OUTPUT_ROOT` → output tại `$ROOT/<slug>/`
3. Default → output tại `<inbox-parent>/../output/<slug>/`

Agent parse path từ log, không hardcode assume default.

---

## Khi nào tự chạy, khi nào hỏi

Tự chạy không hỏi khi: inbox đã có đầy đủ PNG + `metadata.json`, sách thuộc loại đã verify (Việt hiện đại hoặc Việt cổ tương tự Nam Phong), Phúc dùng từ rõ như "cook full", "chạy hết", "all phases" **VÀ sách đó đã từng verify trước**. Trong trường hợp này, mặc định là `scan2ebook all <inbox> --yes --upload`. Cost dưới $15 cho sách 200 trang là expected, không cần xin phép.

Hỏi/smoke trước khi chạy khi: sách mới chưa từng OCR, layout có vẻ khác (manga, scan PDF nhiều trang/file, ảnh thấp DPI), Phúc nói "thử" (implies smoke 5–10 trang trước), cost ước trên $20, hoặc khi bạn muốn `--upload` nhưng Phúc chưa nói rõ. **Use workflow ở mục trên:** `doctor --json` → `all <inbox> --smoke --json` → surface `est_full_cost_usd` → chờ Phúc → `all <inbox> --yes --json`.

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
- [.claude/skills/scan-to-ebook/SKILL.md](.claude/skills/scan-to-ebook/SKILL.md) — skill definition cho Claude Code/Cursor (references back to AGENTS.md for interaction protocol)
