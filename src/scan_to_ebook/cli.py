"""CLI entry point: `scan2ebook <subcommand>`.

Subcommands:
    init <slug> --from <dir|book.pdf>       # register a book, copy scans / render PDF in
    all <slug>                              # Stage 1+2+3 (4 optional via --upload)
    ocr <input-dir> <output-dir>           # Stage 1 only
    post <ocr-dir> <book.md> --title ...    # Stage 2 only
    epub <book.md> <book.epub>             # Stage 3 only
    upload <book.epub>                      # Stage 4 (rclone gdrive)

Storage layout (created by `init`, lives under the data-root):
    <home>/<slug>/                          # home = $SCAN2EBOOK_HOME or ~/scan2ebook
        scans/                              # source images (never auto-deleted)
            page_001.png, page_002.png, ...
            metadata.json                   # optional: {title, author, lang, year}
            cover.jpg                       # optional
        work/                               # cache: context, OCR text, book.md
        dist/<slug>.epub                    # final output

`all` takes a SLUG (joined to the data-root) or a PATH to a book-home.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import doctor, drive_download, drive_upload, epub_build, json_output, manga_pipeline, ocr, pipeline, post_process

# Re-export pipeline symbols dưới namespace `cli` để giữ tương thích test/cmd handlers
# (vd test_cli_ux_helpers.py dùng `cli._slugify`/`cli._import_images`/`cli.EST_COST_PER_PAGE`).
EST_COST_PER_PAGE = pipeline.EST_COST_PER_PAGE
IMAGE_PATTERNS = pipeline.IMAGE_PATTERNS
_slugify = pipeline._slugify
_import_images = pipeline._import_images
_import_pdf = pipeline._import_pdf


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


def _print_ocr_event(kind: str, payload: dict) -> None:
    if kind == "start":
        print(f"Total found: {payload['total']} | resumed (skipped): {payload['skipped']} | todo: {payload['todo']}")
    elif kind == "page_ok":
        print(f"  - {payload['page']}: ok latency={payload['latency_s']}s in={payload['in']} out={payload['out']} -> {payload['dst']}")
    elif kind == "page_blank":
        print(f"  - {payload['page']}: blank → placeholder {payload['dst']}")
    elif kind == "page_fail":
        print(f"  - {payload['page']}: FAIL {payload['error']}", file=sys.stderr)
    elif kind == "context_ok":
        cached = " (cached)" if payload.get("from_cache") else ""
        print(
            f"Context: {payload.get('title')} | {payload.get('pages_per_image')}p/ảnh | "
            f"{payload.get('toc_entries')} mục lục | {payload.get('proper_names')} tên riêng "
            f"| ~${payload.get('cost_usd')}{cached}"
        )
    elif kind == "context_fail":
        print(f"Context pre-pass FAIL: {payload.get('error')}", file=sys.stderr)
    elif kind == "done":
        print(f"\nDone. ok={payload['ok']} blank={payload['blank']} fail={payload['fail']} cost~${payload['cost_usd']}")


def cmd_init(args: argparse.Namespace) -> int:
    """Tạo skeleton book theo layout mới: <data-root>/<slug>/{scans,work,dist}/.

    Ảnh + metadata.json vào scans/ (zone nguồn). work/ + dist/ tạo lazy lúc `all`.
    """
    data_root = pipeline._resolve_data_root(args)
    bp = pipeline._book_paths_from_home(data_root / args.slug)
    bp.scans_dir.mkdir(parents=True, exist_ok=True)
    print(f"==> book: {bp.book_home}")
    print(f"==> scans: {bp.scans_dir}")

    if args.from_dir:
        # Guard re-import TRƯỚC khi tải/đọc nguồn: nếu scans/ đã có page_* thì import
        # mới để lại file thừa (vd cũ 3 page, import 2 → page_003 mồ côi) → OCR nhầm
        # page rác, tốn tiền. Đặt trước Drive-download để fail nhanh, khỏi tải ~100MB
        # rồi mới bị chặn.
        existing = list(bp.scans_dir.glob("page_*"))
        if existing:
            print(
                f"scans/ đã có {len(existing)} file page_* — xoá chúng trước rồi "
                f"chạy lại init --from (tránh page rác): {bp.scans_dir}",
                file=sys.stderr,
            )
            return 2
        # Link Drive file → tải PDF về temp trong book-home (cùng volume với output
        # render), rồi đi tiếp nhánh PDF như local. Cleanup temp ở finally.
        # `_drive_download.pdf` là tên dành riêng: ghi đè rồi xoá nếu trùng.
        tmp_pdf = None
        if drive_download.is_drive_url(args.from_dir):
            tmp_pdf = bp.book_home / "_drive_download.pdf"
            print("==> tải PDF từ Google Drive…")
            drive_download.download_drive_file(args.from_dir, tmp_pdf)
            src = tmp_pdf
        else:
            src = Path(args.from_dir).expanduser()
        try:
            is_pdf = src.is_file() and src.suffix.lower() in pipeline._PDF_SUFFIXES
            if not src.is_dir() and not is_pdf:
                print(f"--from không phải thư mục ảnh hay file PDF: {src}", file=sys.stderr)
                return 2
            if is_pdf:
                n = _import_pdf(src, bp.scans_dir)
                print(f"Rendered {n} trang PDF → scans/page_NNN.jpg")
            else:
                n = _import_images(src, bp.scans_dir)
                print(f"Imported {n} ảnh → scans/page_NNN.<ext>")
        finally:
            if tmp_pdf is not None:
                tmp_pdf.unlink(missing_ok=True)

    meta_file = bp.scans_dir / "metadata.json"
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

    print(f"\nTiếp theo: bỏ ảnh vào scans/ (nếu chưa) rồi chạy:\n  scan2ebook all {args.slug}")
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
        return pipeline._print_dry_run(input_dir, output_dir, args.pattern, args.limit, mode)

    # F3: in đường dẫn output tuyệt đối ngay đầu run (stderr ở json mode).
    out = sys.stderr if mode != "human" else sys.stdout
    print(f"==> output: {output_dir.resolve()}", file=out)

    api_key = pipeline._require_api_key_or_json(mode, stage="ocr")
    if api_key is None:
        return 2

    on_event, collector, _ = pipeline._make_ocr_emitter(mode)
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
        lang=args.lang,
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


def _resolve_scans_dir(bp: pipeline.BookPaths) -> Path:
    """Chọn thư mục đọc ảnh: ưu tiên scans/ (layout mới); fallback book_home phẳng
    (legacy: page_* nằm trực tiếp trong inbox cũ) nếu scans/ chưa có nhưng home có ảnh.

    Shim 1 release cho user còn inbox cũ — KHÔNG ép migrate ngay."""
    if bp.scans_dir.is_dir():
        return bp.scans_dir
    # Legacy flat: page_* trực tiếp trong book_home (inbox cũ trước restructure).
    if bp.book_home.is_dir() and any(bp.book_home.glob("page_*")):
        print(
            f"WARN: layout cũ (ảnh nằm trực tiếp trong {bp.book_home}). Khuyến nghị "
            f"chuyển ảnh vào {bp.scans_dir}/ (layout mới: scans/work/dist).",
            file=sys.stderr,
        )
        return bp.book_home
    return bp.scans_dir  # mặc định mới (caller báo not-found nếu vắng)


def cmd_all(args: argparse.Namespace) -> int:
    """Thin handler: resolve zones/mode, xử lý not-found + dry-run, rồi delegate
    smoke/full sang `pipeline` (logic chạy + gate an toàn nằm ở đó).

    Positional `inbox` nhận SLUG (ghép vào data-root) HOẶC PATH tới book-home."""
    mode = json_output.mode_from_args(args)
    human_out = sys.stderr if mode != "human" else sys.stdout

    bp = pipeline._resolve_book_paths(args, args.inbox)
    scans_dir = _resolve_scans_dir(bp)
    # Đọc ảnh từ scans_dir thật (có thể là legacy flat) → thay vào bp cho downstream.
    bp = bp._replace(scans_dir=scans_dir)

    if not scans_dir.is_dir():
        msg = f"book không tìm thấy ảnh: {scans_dir} (chạy `scan2ebook init <slug> --from <ảnh>` trước)"
        if mode in ("json", "json-lines"):
            json_output.print_summary(json_output.build_summary(
                stage="all", status="error", pages={}, cost_usd=0.0, paths={},
                extra={"error": msg}))
        else:
            print(msg, file=sys.stderr)
        return 2

    slug = bp.book_home.name

    if args.dry_run:
        return pipeline._print_dry_run(scans_dir, bp.ocr_dir, IMAGE_PATTERNS, None, mode)

    bp.work_dir.mkdir(parents=True, exist_ok=True)
    bp.dist_dir.mkdir(parents=True, exist_ok=True)
    print(f"==> book: {bp.book_home.resolve()}", file=human_out)
    print(f"==> output: {bp.dist_dir.resolve()}", file=human_out)  # F3: abs path

    meta = pipeline._load_metadata(scans_dir, slug)
    print(f"==> {slug} | title={meta['title']!r}", file=human_out)

    api_key = pipeline._require_api_key_or_json(mode, stage="all")
    if api_key is None:
        return 2

    # --smoke: OCR ≤10 trang → mini epub → ước cost full → gate xác nhận.
    # run_smoke_gate trả int (gate dừng → return luôn) hoặc float (đã duyệt → chạy
    # full, float = prepass cost đã tiêu ở smoke để fold vào tổng cost cuối).
    carried_cost = 0.0
    if args.smoke:
        gated = pipeline.run_smoke_gate(args, bp, meta, mode, human_out, api_key)
        if isinstance(gated, int):
            return gated  # gate dừng (chưa duyệt) → trả luôn, KHÔNG chạy full.
        carried_cost = gated  # float: prepass cost đã tiêu ở smoke (one-off).

    return pipeline.run_full_pipeline(
        args, bp, meta, mode, human_out, api_key, carried_cost=carried_cost,
    )


def cmd_manga(args: argparse.Namespace) -> int:
    """Manga: nguồn ảnh trang → EPUB3 fixed-layout RTL. KHÔNG OCR, KHÔNG pandoc.

    Positional `slug` nhận SLUG (ghép data-root) HOẶC PATH (dùng lại
    _resolve_book_paths). `--from` = thư mục ảnh | .mobi/.azw3 | .cbz/.cbr/.zip |
    link Drive (file/folder). Build → dist/<slug>.epub + validate cấu trúc."""
    bp = pipeline._resolve_book_paths(args, args.slug)
    slug = bp.book_home.name

    if args.from_src:
        # Guard re-import: scans/ đã có page_* → import mới để file mồ côi (mirror
        # cmd_init). Fail nhanh trước khi tải/giải nén.
        bp.scans_dir.mkdir(parents=True, exist_ok=True)
        existing = list(bp.scans_dir.glob("page_*"))
        if existing:
            print(
                f"scans/ đã có {len(existing)} file page_* — xoá chúng trước rồi "
                f"chạy lại manga --from (tránh page rác): {bp.scans_dir}",
                file=sys.stderr,
            )
            return 2
        manga_pipeline.write_manga_metadata(bp.scans_dir, slug, args)
        n = manga_pipeline.normalize_input(args.from_src, bp.scans_dir)
        print(f"Imported {n} trang → scans/page_NNN.<ext>")

    if not bp.scans_dir.is_dir() or not any(bp.scans_dir.glob("page_*")):
        print(
            f"book không có ảnh: {bp.scans_dir} "
            f"(chạy `scan2ebook manga {slug} --from <nguồn>` trước)",
            file=sys.stderr,
        )
        return 2

    meta = manga_pipeline.load_manga_metadata(bp.scans_dir, slug)
    print(f"==> {slug} | title={meta['title']!r} | lang={meta['lang']} rtl={meta['rtl']}")
    spread_reset = manga_pipeline.parse_spread_reset(args.spread_reset)

    cover_index = args.cover_index
    if args.auto_cover:
        if cover_index != 1:
            # --cover-index tay (khác mặc định) thắng --auto-cover: tôn trọng chỉ
            # định người dùng, KHÔNG gọi LLM (khỏi cần key, khỏi tốn cost).
            print(
                f"WARN --cover-index {cover_index} đè --auto-cover (dùng index tay, bỏ qua dò LLM)",
                file=sys.stderr,
            )
        else:
            from . import manga_cover_detect

            api_key = ocr.require_api_key()  # SystemExit sạch nếu thiếu key
            try:
                cover_index, info = manga_cover_detect.detect_cover_index(
                    api_key, args.model, bp.scans_dir, args.min_px
                )
                src = "LLM" if info["from_model"] else "fallback (model không thấy bìa)"
                print(
                    f"auto-cover: trang {cover_index} [{src}] "
                    f"(${info['cost_usd']}) — {info['reason']}",
                    file=sys.stderr,
                )
            except RuntimeError as exc:
                # auto-cover là tiện ích, KHÔNG load-bearing như OCR prepass: lỗi
                # mạng/parse không nên huỷ cả build (manga build vốn $0/offline) →
                # fallback bìa trang 1, build tiếp, báo to.
                cover_index = 1
                print(
                    f"WARN auto-cover thất bại ({exc}) → dùng bìa trang 1; "
                    "chỉ định tay bằng --cover-index N nếu cần",
                    file=sys.stderr,
                )

    return manga_pipeline.build_manga(
        bp, slug, meta, spread_reset, args.min_px, cover_index
    )


def _add_json_flags(parser: argparse.ArgumentParser) -> None:
    """Thêm cặp cờ output loại trừ nhau cho agent/script."""
    grp = parser.add_mutually_exclusive_group()
    grp.add_argument("--json", action="store_true", help="stdout = 1 JSON summary cuối, log người ra stderr")
    grp.add_argument("--json-lines", dest="json_lines", action="store_true", help="stream NDJSON mỗi event ra stdout")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="scan2ebook", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    # init
    p_init = sub.add_parser("init", help="Tạo skeleton book (<home>/<slug>/{scans,work,dist} + import ảnh + metadata mẫu)")
    p_init.add_argument("slug", help="tên sách (folder name), vd namphong-q01")
    p_init.add_argument("--home", type=Path, default=None, help="data-root chứa mọi sách (default $SCAN2EBOOK_HOME hoặc ~/scan2ebook)")
    p_init.add_argument("--from", dest="from_dir", default=None, help="thư mục ảnh, file .pdf, HOẶC link Google Drive file → render/copy vào scans/page_NNN")
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
    p_ocr.add_argument("input", type=Path, help="dir chứa page images (PNG/JPG/HEIC/HEIF)")
    p_ocr.add_argument("output", type=Path, help="output dir cho .md")
    p_ocr.add_argument("--model", default=os.environ.get("OCR_MODEL", ocr.DEFAULT_MODEL))
    p_ocr.add_argument("--workers", type=int, default=12)
    p_ocr.add_argument("--pattern", default=IMAGE_PATTERNS, help="glob ảnh, phân tách dấu phẩy (default PNG+JPG)")
    p_ocr.add_argument("--limit", type=int, default=None, help="OCR tối đa N page đầu (smoke test)")
    p_ocr.add_argument("--max-tokens", type=int, default=12000, help="max output tokens / page")
    p_ocr.add_argument("--lang", default="vi", help="ngôn ngữ sách → chọn prompt OCR (vi mặc định | ja cho sách Nhật dọc RTL)")
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
    p_all.add_argument("inbox", type=Path, metavar="book", help="slug (vd namphong-q01) HOẶC path tới book-home chứa scans/")
    p_all.add_argument("--home", type=Path, default=None, help="data-root khi `book` là slug (default $SCAN2EBOOK_HOME hoặc ~/scan2ebook)")
    p_all.add_argument("--output", type=Path, default=None, help="(deprecated) override book-home (X/scans,work,dist)")
    p_all.add_argument("--model", default=os.environ.get("OCR_MODEL", ocr.DEFAULT_MODEL))
    p_all.add_argument("--workers", type=int, default=12)
    p_all.add_argument("--max-tokens", type=int, default=12000, help="max output tokens / page")
    p_all.add_argument("--dry-run", action="store_true", help="đếm page + ước lượng chi phí, không gọi API")
    p_all.add_argument("--smoke", action="store_true", help="OCR ≤10 trang + mini epub + ước cost full rồi STOP (gate xác nhận)")
    p_all.add_argument("--yes", "-y", action="store_true", help="bỏ qua prompt smoke gate, chạy full luôn (agent/CI)")
    p_all.add_argument("--upload", action="store_true", help="upload epub lên Drive sau khi build")
    p_all.add_argument("--remote", default=drive_upload.DEFAULT_REMOTE)
    p_all.add_argument("--folder", default=drive_upload.DEFAULT_FOLDER)
    _add_json_flags(p_all)
    p_all.set_defaults(func=cmd_all)

    # manga
    p_manga = sub.add_parser("manga", help="Ảnh trang manga → EPUB3 fixed-layout RTL (không OCR)")
    p_manga.add_argument("slug", type=Path, help="tên sách (folder) HOẶC path tới book-home")
    p_manga.add_argument("--home", type=Path, default=None, help="data-root (default $SCAN2EBOOK_HOME hoặc ~/scan2ebook)")
    p_manga.add_argument("--from", dest="from_src", default=None, help="thư mục ảnh | .mobi/.azw3 | .cbz/.cbr/.zip | link Drive (file/folder)")
    p_manga.add_argument("--title", default=None, help="title metadata (default = slug)")
    p_manga.add_argument("--author", default=None)
    p_manga.add_argument("--series", default=None, help="tên bộ (belongs-to-collection)")
    p_manga.add_argument("--series-index", dest="series_index", type=int, default=None, help="số tập trong bộ")
    p_manga.add_argument("--lang", default="ja", help="ngôn ngữ (default ja)")
    p_manga.add_argument("--year", default=None, help="năm xuất bản (dc:date)")
    p_manga.add_argument("--subject", default="Manga")
    p_manga.add_argument("--publisher", default=None)
    p_manga.add_argument("--description", default=None)
    p_manga.add_argument("--no-rtl", dest="rtl", action="store_false", help="đọc trái→phải (mặc định RTL kiểu manga Nhật)")
    p_manga.add_argument("--spread-reset", dest="spread_reset", default=None, help="số trang tái neo nhịp ghép đôi, vd 5,12")
    p_manga.add_argument("--min-px", dest="min_px", type=int, default=400, help="bỏ ảnh nhỏ hơn N px (lọc thumbnail)")
    p_manga.add_argument("--cover-index", dest="cover_index", type=int, default=1, help="trang (1-based, sau lọc) dùng làm bìa; mặc định 1 (bản scan chèn banner → trỏ tới bìa thật, vd 3)")
    p_manga.add_argument("--auto-cover", dest="auto_cover", action="store_true", help="dò bìa bằng vision LLM (cần OPENROUTER_API_KEY); --cover-index tay đè được")
    p_manga.add_argument("--model", default=os.environ.get("OCR_MODEL", ocr.DEFAULT_MODEL), help="model vision cho --auto-cover (mặc định = model OCR)")
    p_manga.set_defaults(func=cmd_manga, rtl=True)

    return p


def main(argv: list[str] | None = None) -> int:
    _load_dotenv()  # nạp .env trước khi subcommand cần OPENROUTER_API_KEY
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
