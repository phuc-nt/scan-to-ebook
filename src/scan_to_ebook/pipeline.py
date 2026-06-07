"""Pipeline orchestration for the `all` stage + shared run helpers.

Tách khỏi `cli.py` để giữ file mỏng (argparse/dispatch ở cli, logic chạy ở đây).
Phụ thuộc một chiều: `cli` → `pipeline`. Module này KHÔNG import `cli` (tránh cycle).

Chứa:
    - hằng số ước cost + glob ảnh
    - helper: metadata, emitter chọn theo mode, api-key guard, dry-run, import ảnh, slugify
    - orchestration: resolve output root, build book, smoke gate, full pipeline
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import unicodedata
from pathlib import Path

from . import drive_upload, epub_build, json_output, ocr, post_process

# Giá ước lượng Gemini 3.1 Pro Preview ~$0.05/page (đo ở Phase 0, 1 ảnh A4).
EST_COST_PER_PAGE = 0.05

# Multi-ext glob mặc định cho stage `all` (vFlat=PNG, Adobe Scan=JPG).
IMAGE_PATTERNS = "*.png,*.jpg,*.jpeg,*.PNG,*.JPG,*.JPEG"


def _slugify(text: str) -> str:
    """Title → ascii kebab-case an toàn cho filename (bỏ dấu tiếng Việt).

    "Nam Phong Tạp Chí Q01 (1917)" → "nam-phong-tap-chi-q01-1917". Tránh ký tự
    cấm trên filesystem (/ : * ? …) và space khi đặt tên epub upload.
    """
    # đ/Đ không tách dấu qua NFKD → map tay trước khi bỏ ký tự non-ascii.
    text = text.replace("đ", "d").replace("Đ", "D")
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_text = nfkd.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_text).strip("-").lower()
    return slug or "book"


def _load_metadata(book_dir: Path, slug: str) -> dict:
    meta_file = book_dir / "metadata.json"
    defaults = {"title": slug, "author": None, "lang": "vi", "year": None}
    if not meta_file.exists():
        return defaults
    try:
        with meta_file.open(encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"WARN metadata.json invalid: {exc} — using defaults", file=sys.stderr)
        return defaults
    return {
        "title": d.get("title") or slug,
        "author": d.get("author"),
        "lang": d.get("lang") or "vi",
        "year": d.get("year"),
    }


def _make_ocr_emitter(mode: str):
    """Chọn callback on_event + collector + đích in log người theo output mode.

    Trả `(on_event, collector, human_out)`:
    - human: emitter người mặc định, log ra stdout.
    - json-lines: mỗi event ra stdout NDJSON; log người (nếu cli in thêm) ra stderr.
    - json: collector gom số liệu (KHÔNG in lúc chạy) + mirror tiến trình ra
      stderr; log thông tin khác của cli cũng ra stderr → stdout chỉ còn summary.
    """
    if mode == "human":
        # Lazy import: _print_ocr_event là emitter người, ở lại cli.py (lớp trình bày).
        from .cli import _print_ocr_event

        return _print_ocr_event, None, sys.stdout
    if mode == "json-lines":
        emit, _ = json_output.make_emitter("json-lines")
        return emit, None, sys.stderr
    # json: collector buffer + human mirror sang stderr trong cùng 1 callback.
    collector = json_output.SummaryCollector()

    def _on_event(kind: str, payload: dict) -> None:
        collector(kind, payload)
        json_output.human_stream(kind, payload)

    return _on_event, collector, sys.stderr


def _require_api_key_or_json(mode: str, stage: str) -> str | None:
    """Lấy API key; nếu thiếu, ở json mode in error object + trả None thay vì
    để `require_api_key` raise SystemExit (làm bẩn/đứt contract json stdout)."""
    if mode == "human":
        return ocr.require_api_key()  # raise SystemExit như cũ — giữ hành vi
    try:
        return ocr.require_api_key()
    except SystemExit:
        json_output.print_summary(
            json_output.build_summary(
                stage=stage, status="error", pages={}, cost_usd=0.0, paths={},
                extra={"error": "OPENROUTER_API_KEY missing (set in .env or env)"},
            )
        )
        return None


def _print_dry_run(
    input_dir: Path, output_dir: Path, pattern: str, limit: int | None, mode: str = "human"
) -> int:
    """Đếm page todo (chưa OCR) + ước lượng chi phí, KHÔNG gọi API.

    `mode` json/json-lines → in 1 summary object thay vì log người."""
    if not input_dir.is_dir():
        if mode in ("json", "json-lines"):
            json_output.print_summary(
                json_output.build_summary(
                    stage="ocr", status="error", pages={}, cost_usd=0.0,
                    paths={}, extra={"error": f"input dir not found: {input_dir}"},
                )
            )
        else:
            print(f"input dir not found: {input_dir}", file=sys.stderr)
        return 2
    todo, total = ocr.collect_pending_pages(input_dir, pattern, output_dir, limit)
    skipped = total - len(todo)
    est = len(todo) * EST_COST_PER_PAGE
    if mode in ("json", "json-lines"):
        json_output.print_summary(
            json_output.build_summary(
                stage="ocr", status="dry-run",
                pages={"todo": len(todo), "skipped": skipped, "total": total},
                cost_usd=est, paths={"ocr_dir": str(output_dir.resolve())},
            )
        )
        return 0
    print(f"[dry-run] pattern={pattern!r} total={total} resumed(skipped)={skipped} todo={len(todo)}")
    print(f"[dry-run] ước lượng chi phí: {len(todo)} page × ${EST_COST_PER_PAGE:.2f} ≈ ${est:.2f}")
    return 0


def _import_images(src: Path, dst: Path) -> int:
    """Copy ảnh từ src vào dst, natural-sort rename page_NNN.<ext>. Returns count.

    Gộp bước copy + rename thủ công. Giữ extension gốc (png/jpg). Zero-pad 3
    chữ số cho gọn (natural-sort vẫn chạy nếu không pad, nhưng pad đẹp hơn)."""
    # suffix là tie-break: cùng stem khác ext (scan_1.jpg/scan_1.png) cho thứ tự
    # ổn định thay vì phụ thuộc insertion order của glob.
    imgs = sorted(
        ocr._glob_patterns(src, IMAGE_PATTERNS),
        key=lambda p: (ocr.natural_sort_key(p), p.suffix.lower()),
    )
    for i, img in enumerate(imgs, start=1):
        shutil.copy2(img, dst / f"page_{i:03d}{img.suffix.lower()}")
    return len(imgs)


def _resolve_output_root(args: argparse.Namespace, inbox_dir: Path, slug: str) -> Path:
    """Quyết định output root (F3). Ưu tiên: --output > SCAN2EBOOK_OUTPUT_ROOT/<slug>
    > mặc định <inbox-parent>/../output/<slug>."""
    if args.output:
        return args.output.expanduser()
    env_root = os.environ.get("SCAN2EBOOK_OUTPUT_ROOT")
    if env_root:
        return Path(env_root).expanduser() / slug
    return inbox_dir.parent.parent / "output" / slug


def _build_book(ocr_dir: Path, output_root: Path, inbox_dir: Path, meta: dict, *, suffix: str = "") -> dict:
    """Merge ocr_dir → book{suffix}.md → build book{suffix}.epub. Trả epub_result + paths.

    `suffix=".smoke"` dùng cho mini epub (không ghi đè book.epub thật)."""
    book_md = output_root / f"book{suffix}.md"
    stats = post_process.merge_pages(
        input_dir=ocr_dir, output_path=book_md,
        title=meta["title"], author=meta["author"], lang=meta["lang"], year=meta["year"],
    )
    book_epub = output_root / f"book{suffix}.epub"
    cover = inbox_dir / "cover.jpg"
    epub_result = epub_build.build_epub(
        input_md=book_md, output_epub=book_epub, cover=cover if cover.exists() else None,
    )
    return {"stats": stats, "epub_result": epub_result, "book_md": book_md, "book_epub": book_epub}


def run_full_pipeline(args, inbox_dir, output_root, ocr_dir, meta, mode, human_out, api_key) -> int:
    """OCR toàn bộ → post → epub → upload. Resume-safe (smoke đã OCR sẽ skip)."""
    on_event, collector, _ = _make_ocr_emitter(mode)
    summary = ocr.run_batch(
        api_key=api_key, input_dir=inbox_dir, output_dir=ocr_dir,
        model=args.model, workers=args.workers, pattern=IMAGE_PATTERNS,
        max_tokens=args.max_tokens, on_event=on_event,
    )
    pages = collector.pages() if collector else {
        "ok": summary["ok"], "blank": summary["blank"], "fail": summary["fail"],
        "skipped": summary["skipped"], "total": summary["total"],
    }
    cost = collector.cost_usd() if collector else summary["cost_usd"]
    paths = {"ocr_dir": str(ocr_dir.resolve())}

    # Blank đã auto-placeholder (không tính fail). Chỉ abort khi còn fail thật.
    if summary["fail"] > 0:
        if mode == "json":
            json_output.print_summary(json_output.build_summary(
                stage="all", status="partial", pages=pages, cost_usd=cost, paths=paths,
                extra={"error": f"{summary['fail']} page fail; rerun `scan2ebook ocr` to retry"}))
        else:
            print(f"\nOCR có {summary['fail']} page fail. Inspect rồi rerun `scan2ebook ocr` để retry.", file=sys.stderr)
        return 1
    if summary["blank"] > 0:
        print(f"OCR: {summary['blank']} blank page → placeholder, tiếp tục build.", file=human_out)

    built = _build_book(ocr_dir, output_root, inbox_dir, meta)
    stats = built["stats"]
    print(f"Merged: {stats['pages_merged']} pages, {stats['chars']} chars, h1={stats['h1']} h2={stats['h2']} footnotes={stats['footnotes']}", file=human_out)
    size_kb = built["epub_result"]["size_bytes"] // 1024
    print(f"✓ {built['book_epub']} ({size_kb}KB)", file=human_out)
    paths["book_md"] = str(built["book_md"].resolve())
    paths["epub_path"] = str(built["book_epub"].resolve())

    if args.upload:
        rename = f"{_slugify(meta['title'])}.epub"
        drive_upload.upload(local_path=built["book_epub"], remote=args.remote, folder=args.folder, rename=rename)
        print(f"Uploaded to {args.remote}:{args.folder}/{rename}", file=human_out)
        paths["uploaded"] = f"{args.remote}:{args.folder}/{rename}"

    if mode == "json":
        json_output.print_summary(json_output.build_summary(
            stage="all", status="ok", pages=pages, cost_usd=cost, paths=paths))
    return 0


def run_smoke_gate(args, inbox_dir, output_root, ocr_dir, meta, mode, human_out, api_key):
    """Smoke: OCR ≤10 trang vào ocr_dir → mini epub → ước cost full → gate.

    Trả về:
      - None  → đã duyệt, caller chạy full pipeline (resume-safe, skip 10 trang đã OCR).
      - int   → gate dừng tại đây (chưa duyệt / json gate / non-tty), caller return luôn.

    Invariant an toàn: KHÔNG bao giờ tiêu cost full khi chưa có `--yes`,
    interactive 'y', và non-tty thì abort (không treo `input()`)."""
    on_event, collector, _ = _make_ocr_emitter(mode)
    summary = ocr.run_batch(
        api_key=api_key, input_dir=inbox_dir, output_dir=ocr_dir,
        model=args.model, workers=args.workers, pattern=IMAGE_PATTERNS,
        limit=10, max_tokens=args.max_tokens, on_event=on_event,
    )
    if summary["fail"] > 0:
        # Smoke fail ngay → đừng ước cost / gate, báo lỗi để user sửa input/key.
        if mode == "json":
            json_output.print_summary(json_output.build_summary(
                stage="smoke", status="partial",
                pages=(collector.pages() if collector else {}),
                cost_usd=(collector.cost_usd() if collector else summary["cost_usd"]),
                paths={"ocr_dir": str(ocr_dir.resolve())},
                extra={"error": f"{summary['fail']} smoke page fail; kiểm tra ảnh/key trước khi chạy full"}))
        else:
            print(f"\nSmoke có {summary['fail']} page fail. Sửa rồi chạy lại.", file=sys.stderr)
        return 1

    # Mini epub từ ≤10 trang đã OCR (file riêng book.smoke.* — không đụng book.epub).
    built = _build_book(ocr_dir, output_root, inbox_dir, meta, suffix=".smoke")
    smoke_epub = built["book_epub"]
    size_kb = built["epub_result"]["size_bytes"] // 1024
    print(f"✓ smoke epub: {smoke_epub} ({size_kb}KB)", file=human_out)

    # Ước cost FULL cho số trang còn lại (chưa OCR). Tính SAU smoke nên phản ánh
    # đúng phần còn lại (10 trang smoke đã loại khỏi pending).
    remaining, total = ocr.collect_pending_pages(inbox_dir, IMAGE_PATTERNS, ocr_dir, None)
    # Giá/trang đo THẬT từ smoke (cost token-based) chính xác hơn flat $0.05; chỉ
    # dùng khi có ≥1 trang OCR ok, else fallback hằng số (tránh chia 0).
    smoke_cost = collector.cost_usd() if collector else summary["cost_usd"]
    per_page = (smoke_cost / summary["ok"]) if summary["ok"] > 0 else EST_COST_PER_PAGE
    est_full = len(remaining) * per_page

    # --yes: agent/CI cố ý bỏ qua prompt → chạy full luôn.
    if args.yes:
        print(f"--yes: tiếp tục full (~${est_full:.2f} cho {len(remaining)} trang còn lại).", file=human_out)
        return None

    # json/json-lines mode: không prompt được → in summary gate, exit 0 (gate có
    # chủ đích, KHÔNG phải lỗi). Agent đọc est_full_cost_usd, hỏi user, re-invoke --yes.
    if mode in ("json", "json-lines"):
        json_output.print_summary(json_output.build_summary(
            stage="smoke", status="smoke",
            pages=(collector.pages() if collector else {}),
            cost_usd=(collector.cost_usd() if collector else summary["cost_usd"]),
            paths={"ocr_dir": str(ocr_dir.resolve()),
                   "smoke_epub": str(smoke_epub.resolve())},
            extra={"est_full_cost_usd": round(est_full, 4),
                   "remaining_pages": len(remaining),
                   "total_pages": total,
                   "message": "pass --yes to run full"}))
        return 0

    # Human non-tty (pipe/CI không truyền --yes): không treo input() → abort an toàn.
    if not sys.stdin.isatty():
        print(
            f"\nFull run ≈ ${est_full:.2f} cho {len(remaining)} trang. stdin không phải tty — "
            f"abort (truyền --yes để chạy full). Smoke epub giữ ở {smoke_epub}.",
            file=sys.stderr,
        )
        return 0

    # Human interactive: prompt y/N. Chỉ 'y'/'Y' mới chạy full.
    answer = input(f"\nFull run ≈ ${est_full:.2f} cho {len(remaining)} trang còn lại. Continue? [y/N] ").strip().lower()
    if answer == "y":
        return None
    print(f"Aborted. Smoke epub giữ ở {smoke_epub}.", file=human_out)
    return 0
