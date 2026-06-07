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
import sys
from pathlib import Path

from . import doctor, drive_upload, epub_build, json_output, ocr, pipeline, post_process

# Re-export pipeline symbols dưới namespace `cli` để giữ tương thích test/cmd handlers
# (vd test_cli_ux_helpers.py dùng `cli._slugify`/`cli._import_images`/`cli.EST_COST_PER_PAGE`).
EST_COST_PER_PAGE = pipeline.EST_COST_PER_PAGE
IMAGE_PATTERNS = pipeline.IMAGE_PATTERNS
_slugify = pipeline._slugify
_import_images = pipeline._import_images


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


def cmd_all(args: argparse.Namespace) -> int:
    """Thin handler: resolve paths/mode, xử lý not-found + dry-run, rồi delegate
    smoke/full sang `pipeline` (logic chạy + gate an toàn nằm ở đó)."""
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
    output_root = pipeline._resolve_output_root(args, inbox_dir, slug)
    ocr_dir = output_root / "ocr"

    if args.dry_run:
        return pipeline._print_dry_run(inbox_dir, ocr_dir, IMAGE_PATTERNS, None, mode)

    output_root.mkdir(parents=True, exist_ok=True)
    print(f"==> output: {output_root.resolve()}", file=human_out)  # F3: abs path

    meta = pipeline._load_metadata(inbox_dir, slug)
    print(f"==> {slug} | title={meta['title']!r}", file=human_out)

    api_key = pipeline._require_api_key_or_json(mode, stage="all")
    if api_key is None:
        return 2

    # --smoke: OCR ≤10 trang → mini epub → ước cost full → gate xác nhận.
    # run_smoke_gate trả int (gate dừng → return luôn) hoặc float (đã duyệt → chạy
    # full, float = prepass cost đã tiêu ở smoke để fold vào tổng cost cuối).
    carried_cost = 0.0
    if args.smoke:
        gated = pipeline.run_smoke_gate(args, inbox_dir, output_root, ocr_dir, meta, mode, human_out, api_key)
        if isinstance(gated, int):
            return gated  # gate dừng (chưa duyệt) → trả luôn, KHÔNG chạy full.
        carried_cost = gated  # float: prepass cost đã tiêu ở smoke (one-off).

    return pipeline.run_full_pipeline(
        args, inbox_dir, output_root, ocr_dir, meta, mode, human_out, api_key,
        carried_cost=carried_cost,
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
