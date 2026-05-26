# Vận hành

## Cost management

OpenRouter charge theo token in/out. Gemini 3.1 Pro Preview (5/2026) là $2.5/M input token và $10/M output token. Một trang A4 tiếng Việt bình quân 1421 input token (prompt + ảnh base64) và 4000–6000 output token (markdown trang). Cost trung bình $0.05/trang.

Cost per book ước tính theo số trang. Sách 100 trang khoảng $5, 200 trang $10, 500 trang $25. Tăng đột biến (output token gấp 2–3) gặp ở trang nhiều text dày, footnote nhiều, hoặc trang chứa table phức tạp.

Pipeline in cost ước tính cuối stage 1, dòng cuối log. Số này không bao gồm các page bị fail (retry không charge khi error response, nhưng có thể charge khi response thành công về client với empty content).

Để giảm cost, có thể.

Dùng model rẻ hơn cho sách hiện đại layout đơn giản. `--model openai/gpt-4o-mini` cost ~$0.01/page nhưng dấu Việt thường kém hơn — verify trước. Sách cổ phức tạp vẫn nên dùng Gemini 3.1 Pro.

Giảm `--workers` xuống 2 hoặc 1 nếu OpenRouter rate limit hit gây retry tốn cost. Mặc định 4 đủ cho paid tier.

Crop ảnh PNG trước khi OCR. Giảm pixel = giảm token input. ImageMagick `mogrify -trim` cắt viền trắng tự động. Có thể tiết kiệm 10–20% input cost.

Dùng JPG thay PNG cho ảnh chụp (vFlat output PNG mặc định). JPG quality 85 nhỏ hơn PNG ~50% nhưng vision model không phân biệt được. Lưu ý: chỉ apply cho ảnh chụp, không cho scan flat-bed.

## OpenRouter credit và key cap

OpenRouter có 2 loại limit dễ confuse.

Credit balance là tiền còn lại trong account, tính bằng USD. Pipeline charge dần khi gọi API. Khi balance = 0, request fail với HTTP 402. Cần nạp thêm credit trên dashboard https://openrouter.ai/credits.

Key cap là limit per-API-key, đặt khi tạo key. Default mới tạo có thể là $5–20. Khi cumulative cost qua key đạt cap, request fail với HTTP 403 "Key limit exceeded (total limit)". Sửa cap tại https://openrouter.ai/keys, click key, raise limit.

Pipeline phân biệt 2 lỗi trong log. HTTP 402 nghĩa là nạp credit. HTTP 403 nghĩa là raise key cap. Cả hai đều cần action manual trên dashboard rồi rerun (resumable picks up đúng chỗ).

Tip thực tế: tạo riêng 1 OpenRouter key cho pipeline với cap $50–100, không dùng chung key research/dev khác. Phân biệt cost rõ trong analytics dashboard.

## Blank page

Một số trang sách thực sự blank: cover sau, divider giữa các chương, separator giữa các phần. Gemini correctly trả về `empty content (finish_reason=stop)` cho những trang này — không có text để OCR. Pipeline detect empty và raise RuntimeError, count vào failures.

Sau khi pipeline báo failure cho blank page, mở ảnh xem có thực sự blank không.

```bash
open ~/Books-inbox/<slug>/page_065.png
```

Nếu thực sự blank, tạo placeholder thủ công để pipeline skip ở lần rerun.

```bash
echo '<!-- blank page -->' > ~/output/<slug>/ocr/page_065.md
```

Sau đó rerun để stage 1 skip trang đã có placeholder, và stage 2+3 chạy bình thường.

```bash
scan2ebook all ~/Books-inbox/<slug>
```

Nếu trang KHÔNG blank nhưng pipeline vẫn báo empty content, có 2 khả năng. Một là vision model gặp safety filter (rare cho text Việt nhưng có thể gặp với sách political/religious). Hai là ảnh quá tối/quá mờ, model không đọc được. Thử rescan với DPI cao hơn, hoặc đổi model qua `--model anthropic/claude-opus-4`.

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

Default model `google/gemini-3.1-pro-preview` ổn nhất hiện tại cho corpus Việt. Khi cần override, có 2 cách.

Override per-run qua CLI flag.

```bash
scan2ebook ocr <inbox> <out> --model anthropic/claude-opus-4
```

Override permanent qua env (chưa support trong code hiện tại, cần patch nhỏ trong `ocr.py` nếu muốn).

Danh sách model đã test trên corpus Nam Phong 1917 (5 trang spike Phase 0).

`google/gemini-3.1-pro-preview` — zero error, $0.05/page. Default, recommended.

`google/gemini-2.5-pro` — minor error, $0.03/page. Backup khi Gemini 3.1 down.

`anthropic/claude-opus-4` — zero error, $0.20/page. Đắt 4x, dùng khi sách cực khó (corrupt scan, calligraphy).

`anthropic/claude-sonnet-4` — minor error, $0.05/page. Tương đương Gemini 3.1 cost nhưng error rate cao hơn nhẹ.

`openai/gpt-4o` — moderate error, $0.04/page. Bỏ dấu thỉnh thoảng. Không recommended cho Việt.

`openai/gpt-4o-mini` — many errors, $0.01/page. Rẻ nhưng không acceptable cho Việt cổ. OK cho sách hiện đại nếu accept lỗi.

`qwen/qwen3-vl-72b` — heavy modernize bias. Tự sửa "chánh" thành "chính", drop hyphen từ ghép. Không dùng cho Việt cổ.

`zhipu/glm-4.5v` — heavy modernize bias. Tương tự Qwen.

Khi đổi model, smoke test 10 trang trước khi commit full pipeline. Output style mỗi model khác — Claude verbose hơn, GPT terse hơn, có thể cần chỉnh prompt nếu chuyển hẳn.

## Prompt tuning

Prompt OCR ở `src/scan_to_ebook/ocr.py`, biến `PROMPT`. Đã verified zero error trên Nam Phong 1917 với Gemini 3.1 Pro. Đừng đổi nếu không có lý do rõ.

Lý do hợp lệ để tune prompt: ngôn ngữ khác (English, Japanese), genre rất khác (math heavy với LaTeX, music score), layout đặc biệt (newspaper 4 cột).

Quy trình tune. Một là branch riêng. Hai là edit `PROMPT`. Ba là smoke test 20 trang Nam Phong từ `~/.hermes/profiles/scan-to-ebook/inbox/namphong-q01-full/` (đã có ground truth). Bốn là so diff với version cũ qua `git diff` hoặc dùng tool diff trực quan. Năm là chỉ merge khi diff acceptable (không corrupt chữ nào, không drop dấu).

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

Output folder không có backup tự động. User tự backup.

Recommended: rclone sync entire output folder lên Drive định kỳ.

```bash
rclone sync ~/output/ gdrive:Backup/scan-to-ebook-output/ --progress
```

Inbox PNG có thể backup bằng Time Machine (macOS) hoặc rclone tương tự. Inbox quan trọng hơn output vì chỉ inbox là không reproducible — output có thể rebuild từ inbox nếu còn OpenRouter credit.

Loại trừ `.env` khỏi backup public.

```bash
rclone sync ~/workspace/scan-to-ebook/ gdrive:Backup/repo/ --exclude .env --exclude .venv/ --progress
```

## Limits đã biết

Pipeline không xử lý tốt: sách có ảnh minh họa nhiều (model mô tả ảnh thay vì OCR, output rác), sách formula toán/khoa học (LaTeX rendering cần prompt riêng), sách nhạc với khuông nhạc (vision model không transcribe sheet music chính xác).

Pipeline xử lý OK nhưng cần manual review: sách có table phức tạp (column alignment đôi lúc lệch trong markdown), sách footnote dày (numbering có thể duplicate giữa chapter).

Pipeline xử lý tốt: sách prose tiếng Việt hiện đại, sách prose tiếng Việt cổ (1900-1950), sách tiểu thuyết, sách non-fiction text-heavy, tạp chí 2 cột.
