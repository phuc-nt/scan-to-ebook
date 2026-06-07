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

from . import drive_upload, epub_build, ocr, post_process

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


def _print_dry_run(input_dir: Path, output_dir: Path, pattern: str, limit: int | None) -> int:
    """Đếm page todo (chưa OCR) + ước lượng chi phí, KHÔNG gọi API."""
    if not input_dir.is_dir():
        print(f"input dir not found: {input_dir}", file=sys.stderr)
        return 2
    todo, total = ocr.collect_pending_pages(input_dir, pattern, output_dir, limit)
    skipped = total - len(todo)
    est = len(todo) * EST_COST_PER_PAGE
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


def cmd_ocr(args: argparse.Namespace) -> int:
    input_dir = args.input.expanduser()
    output_dir = args.output.expanduser()
    if args.dry_run:
        return _print_dry_run(input_dir, output_dir, args.pattern, args.limit)
    api_key = ocr.require_api_key()
    summary = ocr.run_batch(
        api_key=api_key,
        input_dir=input_dir,
        output_dir=output_dir,
        model=args.model,
        workers=args.workers,
        pattern=args.pattern,
        limit=args.limit,
        max_tokens=args.max_tokens,
        on_event=_print_ocr_event,
    )
    return 0 if summary["fail"] == 0 else 1


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
    inbox_dir: Path = args.inbox.expanduser()
    if not inbox_dir.is_dir():
        print(f"inbox dir not found: {inbox_dir}", file=sys.stderr)
        return 2
    slug = inbox_dir.name
    output_root = args.output.expanduser() if args.output else inbox_dir.parent.parent / "output" / slug
    ocr_dir = output_root / "ocr"

    if args.dry_run:
        return _print_dry_run(inbox_dir, ocr_dir, IMAGE_PATTERNS, None)

    output_root.mkdir(parents=True, exist_ok=True)

    meta = _load_metadata(inbox_dir, slug)
    print(f"==> {slug} | title={meta['title']!r}")

    # Stage 1: OCR
    api_key = ocr.require_api_key()
    summary = ocr.run_batch(
        api_key=api_key,
        input_dir=inbox_dir,
        output_dir=ocr_dir,
        model=args.model,
        workers=args.workers,
        pattern=IMAGE_PATTERNS,
        max_tokens=args.max_tokens,
        on_event=_print_ocr_event,
    )
    # Blank page đã auto-placeholder (không tính fail). Chỉ abort khi còn fail thật.
    if summary["fail"] > 0:
        print(f"\nOCR có {summary['fail']} page fail. Inspect rồi rerun `scan2ebook ocr` để retry.", file=sys.stderr)
        return 1
    if summary["blank"] > 0:
        print(f"OCR: {summary['blank']} blank page → placeholder, tiếp tục build.")

    # Stage 2: post-process
    book_md = output_root / "book.md"
    stats = post_process.merge_pages(
        input_dir=ocr_dir,
        output_path=book_md,
        title=meta["title"],
        author=meta["author"],
        lang=meta["lang"],
        year=meta["year"],
    )
    print(f"Merged: {stats['pages_merged']} pages, {stats['chars']} chars, h1={stats['h1']} h2={stats['h2']} footnotes={stats['footnotes']}")

    # Stage 3: epub
    book_epub = output_root / "book.epub"
    cover = inbox_dir / "cover.jpg"
    epub_result = epub_build.build_epub(
        input_md=book_md,
        output_epub=book_epub,
        cover=cover if cover.exists() else None,
    )
    size_kb = epub_result["size_bytes"] // 1024
    print(f"✓ {book_epub} ({size_kb}KB)")

    # Stage 4: upload (optional)
    if args.upload:
        rename = f"{_slugify(meta['title'])}.epub"
        drive_upload.upload(
            local_path=book_epub,
            remote=args.remote,
            folder=args.folder,
            rename=rename,
        )
        print(f"Uploaded to {args.remote}:{args.folder}/{rename}")

    return 0


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
    p_all.add_argument("--output", type=Path, default=None, help="output root (default: <inbox-parent>/../output/<slug>)")
    p_all.add_argument("--model", default=os.environ.get("OCR_MODEL", ocr.DEFAULT_MODEL))
    p_all.add_argument("--workers", type=int, default=4)
    p_all.add_argument("--max-tokens", type=int, default=12000, help="max output tokens / page")
    p_all.add_argument("--dry-run", action="store_true", help="đếm page + ước lượng chi phí, không gọi API")
    p_all.add_argument("--upload", action="store_true", help="upload epub lên Drive sau khi build")
    p_all.add_argument("--remote", default=drive_upload.DEFAULT_REMOTE)
    p_all.add_argument("--folder", default=drive_upload.DEFAULT_FOLDER)
    p_all.set_defaults(func=cmd_all)

    return p


def main(argv: list[str] | None = None) -> int:
    _load_dotenv()  # nạp .env trước khi subcommand cần OPENROUTER_API_KEY
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
