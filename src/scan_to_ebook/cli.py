"""CLI entry point: `scan2ebook <subcommand>`.

Subcommands:
    ocr <inbox-dir> <output-dir>            # Stage 1
    post <ocr-dir> <book.md> --title ...    # Stage 2
    epub <book.md> <book.epub>              # Stage 3
    upload <book.epub>                      # Stage 4 (rclone gdrive)
    all <inbox-dir>                         # 1+2+3 (4 optional)

Inbox convention:
    inbox/<slug>/
        page_001.png, page_002.png, ...
        metadata.json   # optional: {title, author, lang, year}
        cover.jpg       # optional
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

from . import doctor, drive_upload, epub_build, json_output, ocr, post_process

# Giá ước lượng Gemini 3.1 Pro Preview ~$0.05/page (đo ở Phase 0, 1 ảnh A4).
EST_COST_PER_PAGE = 0.05

# Multi-ext glob mặc định cho stage `all` (vFlat=PNG, Adobe Scan=JPG).
IMAGE_PATTERNS = "*.png,*.jpg,*.jpeg,*.PNG,*.JPG,*.JPEG"


def _load_dotenv() -> None:
    """Nạp KEY=VALUE từ .env (CWD rồi repo root) vào os.environ.

    KHÔNG ghi đè biến đã có sẵn (source .env / export vẫn thắng). Stdlib thuần,
    parse đơn giản: bỏ dòng trống / comment (#), tách theo dấu = đầu tiên, strip
    quote bao quanh value. Xoá nghi thức phải `source .env` mỗi shell.
    """
    repo_root = Path(__file__).resolve().parent.parent.parent
    candidates = [Path.cwd() / ".env", repo_root / ".env"]
    seen: set[Path] = set()
    for env_file in candidates:
        if env_file in seen or not env_file.is_file():
            continue
        seen.add(env_file)
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val


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


def _print_ocr_event(kind: str, payload: dict) -> None:
    if kind == "start":
        print(f"Total found: {payload['total']} | resumed (skipped): {payload['skipped']} | todo: {payload['todo']}")
    elif kind == "page_ok":
        print(f"  - {payload['page']}: ok latency={payload['latency_s']}s in={payload['in']} out={payload['out']} -> {payload['dst']}")
    elif kind == "page_blank":
        print(f"  - {payload['page']}: blank → placeholder {payload['dst']}")
    elif kind == "page_fail":
        print(f"  - {payload['page']}: FAIL {payload['error']}", file=sys.stderr)
    elif kind == "done":
        print(f"\nDone. ok={payload['ok']} blank={payload['blank']} fail={payload['fail']} cost~${payload['cost_usd']}")


def _make_ocr_emitter(mode: str):
    """Chọn callback on_event + collector + đích in log người theo output mode.

    Trả `(on_event, collector, human_out)`:
    - human: emitter người mặc định, log ra stdout.
    - json-lines: mỗi event ra stdout NDJSON; log người (nếu cli in thêm) ra stderr.
    - json: collector gom số liệu (KHÔNG in lúc chạy) + mirror tiến trình ra
      stderr; log thông tin khác của cli cũng ra stderr → stdout chỉ còn summary.
    """
    if mode == "human":
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


def cmd_init(args: argparse.Namespace) -> int:
    """Tạo skeleton inbox: mkdir + (option) import ảnh + metadata.json mẫu."""
    base = args.base.expanduser()
    inbox = base / args.slug
    inbox.mkdir(parents=True, exist_ok=True)
    print(f"==> inbox: {inbox}")

    if args.from_dir:
        src = args.from_dir.expanduser()
        if not src.is_dir():
            print(f"--from dir not found: {src}", file=sys.stderr)
            return 2
        # Guard re-import: nếu inbox đã có page_* thì copy mới sẽ để lại file thừa
        # (vd cũ 3 page, import 2 → page_003 mồ côi) → OCR nhầm page rác, tốn tiền.
        # Bắt user dọn trước thay vì âm thầm xoá/ghi đè.
        existing = list(inbox.glob("page_*"))
        if existing:
            print(
                f"inbox đã có {len(existing)} file page_* — xoá chúng trước rồi "
                f"chạy lại init --from (tránh page rác): {inbox}",
                file=sys.stderr,
            )
            return 2
        n = _import_images(src, inbox)
        print(f"Imported {n} ảnh → page_NNN.<ext>")

    meta_file = inbox / "metadata.json"
    if meta_file.exists():
        print(f"metadata.json đã tồn tại, giữ nguyên: {meta_file}")
    else:
        meta = {
            "title": args.title or args.slug,
            "author": args.author,
            "lang": args.lang,
            "year": args.year,
        }
        meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Tạo metadata.json mẫu: {meta_file}")

    print(f"\nTiếp theo: bỏ ảnh vào (nếu chưa) rồi chạy:\n  scan2ebook all {inbox}")
    return 0


_DOCTOR_GLYPH = {True: "✓", False: "✗"}


def cmd_doctor(args: argparse.Namespace) -> int:
    """Self-check môi trường: python/pandoc/key/rclone. Exit 0 iff essential pass."""
    results = doctor.run_checks()
    ok = doctor.all_essential_ok(results)
    if getattr(args, "json", False):
        json_output.print_summary({
            "status": "ok" if ok else "fail",
            "checks": results,
        })
        return 0 if ok else 1
    for c in results:
        if c["ok"]:
            glyph = _DOCTOR_GLYPH[True]
        elif c["essential"]:
            glyph = _DOCTOR_GLYPH[False]
        else:
            glyph = "⚠"  # non-essential vắng = cảnh báo, không fail
        print(f"{glyph} {c['name']}: {c['detail']}")
    print(f"\n{'OK — sẵn sàng chạy' if ok else 'FAIL — cài thiếu phần essential ở trên rồi chạy lại'}")
    return 0 if ok else 1


def cmd_ocr(args: argparse.Namespace) -> int:
    input_dir = args.input.expanduser()
    output_dir = args.output.expanduser()
    mode = json_output.mode_from_args(args)
    if args.dry_run:
        return _print_dry_run(input_dir, output_dir, args.pattern, args.limit, mode)

    # F3: in đường dẫn output tuyệt đối ngay đầu run (stderr ở json mode).
    out = sys.stderr if mode != "human" else sys.stdout
    print(f"==> output: {output_dir.resolve()}", file=out)

    api_key = _require_api_key_or_json(mode, stage="ocr")
    if api_key is None:
        return 2

    on_event, collector, _ = _make_ocr_emitter(mode)
    summary = ocr.run_batch(
        api_key=api_key,
        input_dir=input_dir,
        output_dir=output_dir,
        model=args.model,
        workers=args.workers,
        pattern=args.pattern,
        limit=args.limit,
        max_tokens=args.max_tokens,
        on_event=on_event,
    )
    rc = 0 if summary["fail"] == 0 else 1
    if mode == "json":
        status = "ok" if summary["fail"] == 0 else "partial"
        json_output.print_summary(
            json_output.build_summary(
                stage="ocr", status=status, pages=collector.pages(),
                cost_usd=collector.cost_usd(),
                paths={"ocr_dir": str(output_dir.resolve())},
            )
        )
    return rc


def cmd_post(args: argparse.Namespace) -> int:
    stats = post_process.merge_pages(
        input_dir=args.input.expanduser(),
        output_path=args.output.expanduser(),
        title=args.title,
        author=args.author,
        lang=args.lang,
        year=args.year,
        pattern=args.pattern,
    )
    print("Post-process done:")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    return 0


def cmd_epub(args: argparse.Namespace) -> int:
    result = epub_build.build_epub(
        input_md=args.input.expanduser(),
        output_epub=args.output.expanduser(),
        cover=args.cover.expanduser() if args.cover else None,
    )
    size_kb = result["size_bytes"] // 1024
    magic = "✓" if result["magic_ok"] else "WARN: not EPUB magic"
    print(f"{magic} {result['output']} ({size_kb}KB)")
    for w in result["pandoc_warnings"]:
        print(f"  {w}", file=sys.stderr)
    return 0 if result["magic_ok"] else 1


def cmd_upload(args: argparse.Namespace) -> int:
    result = drive_upload.upload(
        local_path=args.path.expanduser(),
        remote=args.remote,
        folder=args.folder,
        rename=args.rename,
    )
    print(f"Uploaded {result['local']} → {result['remote']}" + (f"/{result['rename']}" if result['rename'] else ""))
    return 0


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


def cmd_all(args: argparse.Namespace) -> int:
    inbox_dir: Path = args.inbox.expanduser()
    mode = json_output.mode_from_args(args)
    human_out = sys.stderr if mode != "human" else sys.stdout

    if not inbox_dir.is_dir():
        if mode in ("json", "json-lines"):
            json_output.print_summary(json_output.build_summary(
                stage="all", status="error", pages={}, cost_usd=0.0, paths={},
                extra={"error": f"inbox dir not found: {inbox_dir}"}))
        else:
            print(f"inbox dir not found: {inbox_dir}", file=sys.stderr)
        return 2

    slug = inbox_dir.name
    output_root = _resolve_output_root(args, inbox_dir, slug)
    ocr_dir = output_root / "ocr"

    if args.dry_run:
        return _print_dry_run(inbox_dir, ocr_dir, IMAGE_PATTERNS, None, mode)

    output_root.mkdir(parents=True, exist_ok=True)
    print(f"==> output: {output_root.resolve()}", file=human_out)  # F3: abs path

    meta = _load_metadata(inbox_dir, slug)
    print(f"==> {slug} | title={meta['title']!r}", file=human_out)

    api_key = _require_api_key_or_json(mode, stage="all")
    if api_key is None:
        return 2

    # --smoke: OCR ≤10 trang → mini epub → ước cost full → gate xác nhận.
    if args.smoke:
        gated = _run_smoke_gate(args, inbox_dir, output_root, ocr_dir, meta, mode, human_out, api_key)
        if gated is not None:
            return gated  # gate dừng (chưa duyệt) → trả luôn, KHÔNG chạy full.

    return _run_full_pipeline(args, inbox_dir, output_root, ocr_dir, meta, mode, human_out, api_key)


def _run_full_pipeline(args, inbox_dir, output_root, ocr_dir, meta, mode, human_out, api_key) -> int:
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


def _run_smoke_gate(args, inbox_dir, output_root, ocr_dir, meta, mode, human_out, api_key):
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


def _add_json_flags(parser: argparse.ArgumentParser) -> None:
    """Thêm cặp cờ output loại trừ nhau cho agent/script."""
    grp = parser.add_mutually_exclusive_group()
    grp.add_argument("--json", action="store_true", help="stdout = 1 JSON summary cuối, log người ra stderr")
    grp.add_argument("--json-lines", dest="json_lines", action="store_true", help="stream NDJSON mỗi event ra stdout")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="scan2ebook", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    # init
    p_init = sub.add_parser("init", help="Tạo skeleton inbox (folder + import ảnh + metadata mẫu)")
    p_init.add_argument("slug", help="tên sách (folder name), vd namphong-q01")
    p_init.add_argument("--base", type=Path, default=Path("~/Books-inbox"), help="thư mục gốc chứa inbox (default ~/Books-inbox)")
    p_init.add_argument("--from", dest="from_dir", type=Path, default=None, help="copy ảnh từ thư mục này + rename page_NNN")
    p_init.add_argument("--title", default=None, help="title cho metadata.json (default = slug)")
    p_init.add_argument("--author", default=None)
    p_init.add_argument("--lang", default="vi")
    p_init.add_argument("--year", default=None)
    p_init.set_defaults(func=cmd_init)

    # doctor
    p_doctor = sub.add_parser("doctor", help="Self-check môi trường (python/pandoc/key/rclone)")
    p_doctor.add_argument("--json", action="store_true", help="in 1 JSON object thay vì checklist người")
    p_doctor.set_defaults(func=cmd_doctor)

    # ocr
    p_ocr = sub.add_parser("ocr", help="Stage 1: page images → per-page markdown")
    p_ocr.add_argument("input", type=Path, help="inbox dir chứa PNG/JPG")
    p_ocr.add_argument("output", type=Path, help="output dir cho .md")
    p_ocr.add_argument("--model", default=os.environ.get("OCR_MODEL", ocr.DEFAULT_MODEL))
    p_ocr.add_argument("--workers", type=int, default=4)
    p_ocr.add_argument("--pattern", default=IMAGE_PATTERNS, help="glob ảnh, phân tách dấu phẩy (default PNG+JPG)")
    p_ocr.add_argument("--limit", type=int, default=None, help="OCR tối đa N page đầu (smoke test)")
    p_ocr.add_argument("--max-tokens", type=int, default=12000, help="max output tokens / page")
    p_ocr.add_argument("--dry-run", action="store_true", help="đếm page + ước lượng chi phí, không gọi API")
    _add_json_flags(p_ocr)
    p_ocr.set_defaults(func=cmd_ocr)

    # post
    p_post = sub.add_parser("post", help="Stage 2: merge per-page md → book.md")
    p_post.add_argument("input", type=Path, help="dir chứa page_*.md")
    p_post.add_argument("output", type=Path, help="output book.md")
    p_post.add_argument("--title", required=True)
    p_post.add_argument("--author", default=None)
    p_post.add_argument("--lang", default="vi")
    p_post.add_argument("--year", default=None)
    p_post.add_argument("--pattern", default="page_*.md")
    p_post.set_defaults(func=cmd_post)

    # epub
    p_epub = sub.add_parser("epub", help="Stage 3: book.md → book.epub (pandoc)")
    p_epub.add_argument("input", type=Path, help="book.md")
    p_epub.add_argument("output", type=Path, help="book.epub")
    p_epub.add_argument("--cover", type=Path, default=None)
    p_epub.set_defaults(func=cmd_epub)

    # upload
    p_up = sub.add_parser("upload", help="Stage 4: epub → Google Drive (rclone)")
    p_up.add_argument("path", type=Path, help="local epub")
    p_up.add_argument("--remote", default=drive_upload.DEFAULT_REMOTE)
    p_up.add_argument("--folder", default=drive_upload.DEFAULT_FOLDER)
    p_up.add_argument("--rename", default=None)
    p_up.set_defaults(func=cmd_upload)

    # all
    p_all = sub.add_parser("all", help="Stage 1+2+3 chain (4 optional via --upload)")
    p_all.add_argument("inbox", type=Path, help="inbox/<slug>/ dir chứa PNG + metadata.json")
    p_all.add_argument("--output", type=Path, default=None, help="output root (default: $SCAN2EBOOK_OUTPUT_ROOT/<slug> nếu set, else <inbox-parent>/../output/<slug>)")
    p_all.add_argument("--model", default=os.environ.get("OCR_MODEL", ocr.DEFAULT_MODEL))
    p_all.add_argument("--workers", type=int, default=4)
    p_all.add_argument("--max-tokens", type=int, default=12000, help="max output tokens / page")
    p_all.add_argument("--dry-run", action="store_true", help="đếm page + ước lượng chi phí, không gọi API")
    p_all.add_argument("--smoke", action="store_true", help="OCR ≤10 trang + mini epub + ước cost full rồi STOP (gate xác nhận)")
    p_all.add_argument("--yes", "-y", action="store_true", help="bỏ qua prompt smoke gate, chạy full luôn (agent/CI)")
    p_all.add_argument("--upload", action="store_true", help="upload epub lên Drive sau khi build")
    p_all.add_argument("--remote", default=drive_upload.DEFAULT_REMOTE)
    p_all.add_argument("--folder", default=drive_upload.DEFAULT_FOLDER)
    _add_json_flags(p_all)
    p_all.set_defaults(func=cmd_all)

    return p


def main(argv: list[str] | None = None) -> int:
    _load_dotenv()  # nạp .env trước khi subcommand cần OPENROUTER_API_KEY
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
