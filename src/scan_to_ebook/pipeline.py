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
from typing import NamedTuple

from . import context_prepass, drive_upload, epub_build, image_ops, json_output, ocr, pdf_render, post_process

# Giá ước lượng Gemini 3.1 Pro Preview ~$0.05/page (đo ở Phase 0, 1 ảnh A4).
EST_COST_PER_PAGE = 0.05

# Multi-ext glob cho OCR stage (`all`): chỉ png/jpg/jpeg — định dạng vision API +
# pandoc đọc trực tiếp. Sau khi import, mọi ảnh ĐÃ là jpg/png nên đây là đủ.
IMAGE_PATTERNS = "*.png,*.jpg,*.jpeg,*.PNG,*.JPG,*.JPEG"

# Glob cho IMPORT stage (`init --from`): thêm HEIC/HEIF (iPhone mặc định chụp HEIC).
# HEIC sẽ được convert→JPG lúc import nên OCR stage không bao giờ thấy HEIC thô.
IMPORT_PATTERNS = IMAGE_PATTERNS + ",*.heic,*.heif,*.HEIC,*.HEIF"

# Đuôi cần convert sang JPG trước khi OCR (vision API + pandoc không đọc HEIC/HEIF).
# Nguồn sự thật ở image_ops; alias giữ cho code/test cũ tham chiếu.
_HEIC_SUFFIXES = image_ops.HEIC_SUFFIXES

# Đuôi nhận diện PDF input (`init --from book.pdf`) → render từng trang → page_NNN.jpg.
_PDF_SUFFIXES = pdf_render.PDF_SUFFIXES


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


def _backfill_metadata_from_context(scans_dir: Path, slug: str, meta: dict, ctx: dict) -> None:
    """Backfill title/author/year/translator từ pre-pass context vào metadata.json.

    CHỈ chạy khi metadata còn mặc định (title == slug) → user chưa tự đặt title.
    Mục đích: trang init tạo metadata.json TRƯỚC pre-pass nên title=slug; nếu không
    backfill, TOC/title-page hiện slug thay vì tên sách thật (vd `book-04`).

    Mutate `meta` in-place (title/author/year) để run hiện tại dùng giá trị thật, và
    ghi đè metadata.json (thêm `translator`, tuy build chưa render — giữ làm nguồn cho
    pipeline-log/catalog). KHÔNG đụng `lang` (user/init chọn, pre-pass không suy ra).
    User đã đặt title thật (title != slug) → no-op, tôn trọng lựa chọn của user.

    BẤT BIẾN: `slug` ở đây PHẢI = slug đã dùng khi `_load_metadata` tạo `meta`
    (cả hai = `bp.book_home.name`). Nếu lệch → default-detection sai (hoặc không bao
    giờ backfill, hoặc đè title thật của user)."""
    if meta.get("title") != slug:
        return  # user đã đặt title thật → không ghi đè
    ctx_title = ctx.get("title")
    if not ctx_title or not isinstance(ctx_title, str):
        return  # pre-pass không bắt được title → giữ slug, không đoán
    ctx_author = ctx.get("author") if isinstance(ctx.get("author"), str) else None
    ctx_year = ctx.get("year") if isinstance(ctx.get("year"), (str, int)) else None
    ctx_translator = ctx.get("translator") if isinstance(ctx.get("translator"), str) else None

    meta["title"] = ctx_title
    if ctx_author:
        meta["author"] = ctx_author
    if ctx_year is not None:
        meta["year"] = str(ctx_year)

    meta_file = scans_dir / "metadata.json"
    out = {
        "title": meta["title"],
        "author": meta.get("author"),
        "translator": ctx_translator,
        "lang": meta.get("lang") or "vi",
        "year": meta.get("year"),
    }
    try:
        meta_file.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"metadata.json backfill từ pre-pass: title={meta['title']!r}", file=sys.stderr)
    except OSError as exc:
        print(f"WARN không ghi được metadata.json backfill: {exc}", file=sys.stderr)


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


def _convert_heic(src: Path, dst: Path) -> None:
    """Convert HEIC/HEIF → JPG cross-platform. dst phải có đuôi .jpg.

    iPhone mặc định chụp HEIC — định dạng vision API + pandoc KHÔNG đọc được.
    Convert lúc import (1 lần, không lặp mỗi OCR retry). Delegate sang image_ops:
    dò backend (sips/magick/heif-convert/pillow-heif) → chạy cái đầu khả dụng.
    Thiếu hết → raise với hướng dẫn cài theo OS (KHÔNG silent-skip: mất trang = sách hỏng).
    """
    image_ops.convert_heic(src, dst)


def _import_images(src: Path, dst: Path) -> int:
    """Copy ảnh từ src vào dst, natural-sort rename page_NNN.<ext>. Returns count.

    Gộp bước copy + rename thủ công. Giữ extension gốc (png/jpg); HEIC/HEIF được
    convert→jpg lúc này (xem _convert_heic) nên output dir chỉ có png/jpg — vision
    API + pandoc đọc trực tiếp, OCR stage không bao giờ thấy HEIC thô. Zero-pad 3
    chữ số cho gọn (natural-sort vẫn chạy nếu không pad, nhưng pad đẹp hơn)."""
    # IMPORT_PATTERNS gồm cả HEIC để glob thấy đủ ảnh nguồn (iPhone = HEIC mặc định).
    # KHÔNG-trùng page_NNN dựa vào _glob_patterns dedupe theo Path (quan trọng trên
    # FS case-insensitive: *.heic + *.HEIC khớp cùng 1 file) → giữ dedupe đó khi sửa.
    # suffix là tie-break: cùng stem khác ext (scan_1.jpg/scan_1.png) cho thứ tự
    # ổn định thay vì phụ thuộc insertion order của glob.
    imgs = sorted(
        ocr._glob_patterns(src, IMPORT_PATTERNS),
        key=lambda p: (ocr.natural_sort_key(p), p.suffix.lower()),
    )
    # 1 enumerate trên toàn list đã sort → page_NNN tuần tự bất kể nguồn jpg hay heic.
    for i, img in enumerate(imgs, start=1):
        if img.suffix.lower() in _HEIC_SUFFIXES:
            _convert_heic(img, dst / f"page_{i:03d}.jpg")
        else:
            shutil.copy2(img, dst / f"page_{i:03d}{img.suffix.lower()}")
    return len(imgs)


def _import_pdf(pdf: Path, dst: Path, dpi: int = pdf_render.DEFAULT_DPI) -> int:
    """Render mọi trang PDF → dst/page_NNN.jpg (giống _import_images nhưng nguồn PDF).

    Render vào thư mục tạm trong dst (prefix _pdfpage) qua pdf_render backend-chain
    rồi rename tuần tự page_NNN.jpg + dọn file render thô. Output dir sau cùng chỉ
    có page_NNN.jpg → OCR + pandoc đọc trực tiếp, không bao giờ thấy PDF. Returns
    số trang đã render."""
    pages = pdf_render.render_pdf_to_images(pdf, dst, dpi=dpi)
    for i, rendered in enumerate(pages, start=1):
        rendered.rename(dst / f"page_{i:03d}.jpg")
    return len(pages)


# Tên thư mục gốc chứa toàn bộ sách (1 sách = 1 thư mục con). Visible trong Finder/
# Explorer = "thuận tiện"; user document data nên KHÔNG ẩn (~/.local/share).
DATA_ROOT_DIRNAME = "scan2ebook"

# Ba zone trong mỗi book-home (tách quyền sở hữu/vòng đời — xem architecture doc).
ZONE_SCANS = "scans"  # nguồn (user-owned, pipeline KHÔNG xoá)
ZONE_WORK = "work"    # cache/trung gian (xoá an toàn = clean-room)
ZONE_DIST = "dist"    # deliverable (epub cuối)


class BookPaths(NamedTuple):
    """Các đường dẫn zone của một sách. Nguồn DRY cho cmd_all/smoke/full."""

    book_home: Path
    scans_dir: Path
    work_dir: Path
    ocr_dir: Path
    dist_dir: Path


def _resolve_data_root(args: argparse.Namespace) -> Path:
    """Quyết định data-root (gốc chứa mọi sách). Ưu tiên:
    1. --home flag       → thắng
    2. $SCAN2EBOOK_HOME  → kế
    3. $SCAN2EBOOK_OUTPUT_ROOT (deprecated alias, warn stderr)
    4. ~/scan2ebook      → mặc định
    Tất cả .expanduser() (Path.home() tự đúng trên Windows)."""
    home = getattr(args, "home", None)
    if home:
        return Path(home).expanduser()
    env_home = os.environ.get("SCAN2EBOOK_HOME")
    if env_home:
        return Path(env_home).expanduser()
    legacy = os.environ.get("SCAN2EBOOK_OUTPUT_ROOT")
    if legacy:
        print(
            "WARN: SCAN2EBOOK_OUTPUT_ROOT deprecated — dùng SCAN2EBOOK_HOME "
            "(layout mới: <home>/<slug>/{scans,work,dist}/).",
            file=sys.stderr,
        )
        return Path(legacy).expanduser()
    return Path.home() / DATA_ROOT_DIRNAME


def _book_paths_from_home(book_home: Path) -> BookPaths:
    """Dẫn xuất 3 zone từ một book-home đã biết."""
    book_home = book_home.expanduser()
    work = book_home / ZONE_WORK
    return BookPaths(
        book_home=book_home,
        scans_dir=book_home / ZONE_SCANS,
        work_dir=work,
        ocr_dir=work / "ocr",
        dist_dir=book_home / ZONE_DIST,
    )


def _resolve_book_paths(args: argparse.Namespace, arg: Path) -> BookPaths:
    """Resolve book-home + zones từ positional `arg` của `all` (slug HOẶC path).

    Rule (R2): `arg` là PATH nếu chứa separator (`/` hoặc `\\`); else là SLUG
    (ghép vào data-root). Quyết định CHỈ dựa separator — KHÔNG dò is_dir(): slug
    trùng tên thư mục trong CWD vẫn phải resolve về data-root, không bị CWD nuốt.
    Lưu ý: `Path("./x")` bị Python normalize thành `Path("x")` (mất separator) nên
    KHÔNG ép được path mode — muốn trỏ path thật, dùng path tuyệt đối hoặc có thư
    mục cha (vd `sub/x`, `/abs/x`). `--output X` (deprecated
    semantics) override book-home=X. Legacy flat inbox (page_* trực tiếp, không có
    scans/) được caller xử lý shim."""
    if getattr(args, "output", None):
        print(
            "WARN: --output deprecated — bỏ flag và dùng slug/path positional "
            "(layout mới: <home>/<slug>/{scans,work,dist}/).",
            file=sys.stderr,
        )
        return _book_paths_from_home(args.output.expanduser())
    arg_str = str(arg)
    looks_like_path = ("/" in arg_str) or ("\\" in arg_str)
    if looks_like_path:
        book_home = arg.expanduser()
    else:
        book_home = _resolve_data_root(args) / arg_str
    return _book_paths_from_home(book_home)


def _resolve_output_root(args: argparse.Namespace, inbox_dir: Path, slug: str) -> Path:
    """DEPRECATED shim — trả `work/` zone (cache root) cho code gọi cũ.

    Giữ tên+chữ ký để không vỡ caller cũ; semantics đổi: giờ trả book_home/work
    (nơi chứa cache OCR + book.md), KHÔNG còn là thư mục output phẳng. epub cuối
    nằm ở dist/ (xem _build_book). Ưu tiên giống _resolve_book_paths."""
    if getattr(args, "output", None):
        return args.output.expanduser() / ZONE_WORK
    return _resolve_data_root(args) / slug / ZONE_WORK


def _resolve_cover(scans_dir: Path, work_dir: Path) -> Path | None:
    """Chọn ảnh bìa cho epub. Thứ tự ưu tiên (cao→thấp):

      1. scans/cover.jpg — user TỰ đặt → override rõ ràng, thắng tuyệt đối.
      2. context.json[cover_page] — pre-pass dò ảnh bìa màu → trỏ scans/<cover_page>.
      3. None — không có bìa (sách scan trắng đen, hoặc pre-pass trả null).

    Trả Path tồn tại hoặc None. cover_page (từ context.json hand-editable) phải trỏ
    một file THỰC nằm TRONG scans_dir — realpath-containment chặn path traversal
    (../../etc) lẫn symlink trỏ ra ngoài; cover chỉ feed `pandoc --epub-cover-image`."""
    user_cover = scans_dir / "cover.jpg"
    if user_cover.exists():
        return user_cover

    ctx = context_prepass.load_context(work_dir)
    if not ctx:
        return None
    name = ctx.get("cover_page")
    if not name or not isinstance(name, str):
        return None
    # Resolve thật rồi kiểm parent == scans_dir đã resolve: subsume mọi `/`, `\\`,
    # `..`, và symlink-escape trong 1 check (defense-in-depth, dù threat model local).
    candidate = (scans_dir / name).resolve()
    if not candidate.is_file() or scans_dir.resolve() not in candidate.parents:
        return None
    return candidate


def _build_book(bp: BookPaths, scans_dir: Path, meta: dict, *, suffix: str = "") -> dict:
    """Merge bp.ocr_dir → book.md (work/) → build epub. Trả epub_result + paths.

    Zone routing:
      - book.md / book.smoke.md  → work/ (cache, regenerable)
      - epub cuối (suffix="")     → dist/<slug>.epub (deliverable)
      - smoke epub (suffix=".smoke") → work/book.smoke.epub (không là deliverable)
      - cover                     → xem _resolve_cover (user cover.jpg > pre-pass cover_page)
    """
    book_md = bp.work_dir / f"book{suffix}.md"
    stats = post_process.merge_pages(
        input_dir=bp.ocr_dir, output_path=book_md,
        title=meta["title"], author=meta["author"], lang=meta["lang"], year=meta["year"],
    )
    if suffix:
        # Smoke epub là sản phẩm phụ kiểm thử → giữ trong work/, không vào dist/.
        book_epub = bp.work_dir / f"book{suffix}.epub"
    else:
        # epub cuối: dist/<slug>.epub (tên slug = đồng nhất với upload rename).
        book_epub = bp.dist_dir / f"{bp.book_home.name}.epub"
    book_epub.parent.mkdir(parents=True, exist_ok=True)
    cover = _resolve_cover(scans_dir, bp.work_dir)
    epub_result = epub_build.build_epub(
        input_md=book_md, output_epub=book_epub, cover=cover,
    )
    return {"stats": stats, "epub_result": epub_result, "book_md": book_md, "book_epub": book_epub}


def _run_prepass_or_abort(
    *, api_key, model, scans_dir, work_dir, max_tokens, on_event,
    meta=None, slug=None,
) -> tuple[str, float] | None:
    """Chạy context pre-pass TRƯỚC OCR loop. Trả (block, cost_usd) hoặc None (abort).

    `scans_dir`: đọc ảnh mẫu. `work_dir`: ghi/đọc cache context.{json,md}.
    Resume-aware: nếu context.json đã tồn tại ở work_dir → cache hit (cost 0, không
    gọi API), nên gọi ở cả smoke lẫn full đều an toàn (full sau smoke = cache hit).
    FAIL (API/parse) → emit context_fail + trả None để caller abort (exit != 0).

    `meta`+`slug`: nếu truyền → backfill title/author/year/translator từ context vào
    metadata.json khi title còn = slug mặc định (mutate `meta` in-place). Gọi ở smoke
    lẫn full đều idempotent (cache hit → ctx như nhau; title đã thật → no-op)."""
    try:
        res = context_prepass.run_prepass(
            api_key, model, scans_dir, IMAGE_PATTERNS, max_tokens=max_tokens,
            out_dir=work_dir,
        )
    except RuntimeError as exc:
        if on_event:
            on_event("context_fail", {"error": str(exc)})
        return None
    ctx = res["context"]
    if meta is not None and slug is not None:
        _backfill_metadata_from_context(scans_dir, slug, meta, ctx)
    if on_event:
        on_event(
            "context_ok",
            {
                "title": ctx.get("title"),
                "translator": ctx.get("translator"),
                "pages_per_image": ctx.get("pages_per_image"),
                "toc_entries": len(ctx.get("table_of_contents") or []),
                "proper_names": len(ctx.get("proper_names") or []),
                "cost_usd": res["cost_usd"],
                "from_cache": res["from_cache"],
            },
        )
    return res["block"], res["cost_usd"]


def _prepass_fail_summary(mode: str, stage: str, ocr_dir: Path) -> int:
    """In summary/err khi pre-pass fail. Trả 1 (abort, không tiêu cost OCR)."""
    if mode == "json":
        json_output.print_summary(json_output.build_summary(
            stage=stage, status="error", pages={}, cost_usd=0.0,
            paths={"ocr_dir": str(ocr_dir.resolve())},
            extra={"error": "context pre-pass failed; xem log stderr, sửa rồi chạy lại"}))
    else:
        print("\nContext pre-pass thất bại — abort (không tiêu cost OCR). Xem log trên.", file=sys.stderr)
    return 1


def run_full_pipeline(
    args, bp: BookPaths, meta, mode, human_out, api_key,
    carried_cost: float = 0.0,
) -> int:
    """OCR toàn bộ → post → epub → upload. Resume-safe (smoke đã OCR sẽ skip).

    Đọc ảnh từ bp.scans_dir, cache vào bp.work_dir/bp.ocr_dir, epub → bp.dist_dir.
    `carried_cost`: cost đã tiêu ở smoke phase (prepass one-off) khi full chạy SAU
    smoke. Full prepass = cache hit (cost 0) nên phải fold carried_cost vào tổng để
    summary không under-count spend."""
    on_event, collector, _ = _make_ocr_emitter(mode)
    prepass = _run_prepass_or_abort(
        api_key=api_key, model=args.model, scans_dir=bp.scans_dir, work_dir=bp.work_dir,
        max_tokens=context_prepass.CONTEXT_MAX_TOKENS, on_event=on_event,
        meta=meta, slug=bp.book_home.name,
    )
    if prepass is None:
        return _prepass_fail_summary(mode, "all", bp.ocr_dir)
    block, prepass_cost = prepass
    summary = ocr.run_batch(
        api_key=api_key, input_dir=bp.scans_dir, output_dir=bp.ocr_dir,
        model=args.model, workers=args.workers, pattern=IMAGE_PATTERNS,
        max_tokens=args.max_tokens, on_event=on_event, prompt_context=block,
    )
    pages = collector.pages() if collector else {
        "ok": summary["ok"], "blank": summary["blank"], "fail": summary["fail"],
        "skipped": summary["skipped"], "total": summary["total"],
    }
    # prepass_cost: full thường = cache hit (0). carried_cost: prepass đã tiêu ở smoke.
    total_prepass_cost = prepass_cost + carried_cost
    cost = (collector.cost_usd() if collector else summary["cost_usd"]) + total_prepass_cost
    paths = {"ocr_dir": str(bp.ocr_dir.resolve())}

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

    built = _build_book(bp, bp.scans_dir, meta)
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
            stage="all", status="ok", pages=pages, cost_usd=cost, paths=paths,
            extra={"prepass_cost_usd": round(total_prepass_cost, 4)}))
    return 0


def run_smoke_gate(args, bp: BookPaths, meta, mode, human_out, api_key):
    """Smoke: OCR ≤10 trang vào bp.ocr_dir → mini epub → ước cost full → gate.

    Trả về:
      - float → đã duyệt, caller chạy full pipeline; giá trị = prepass cost đã tiêu
        ở smoke (one-off) để full fold vào tổng cost cuối (full = cache hit, cost 0,
        nên nếu không carry sẽ under-count spend ~$0.09). Resume-safe (skip 10 trang đã OCR).
      - int   → gate dừng tại đây (chưa duyệt / json gate / non-tty), caller return luôn.

    Invariant an toàn: KHÔNG bao giờ tiêu cost full khi chưa có `--yes`,
    interactive 'y', và non-tty thì abort (không treo `input()`)."""
    on_event, collector, _ = _make_ocr_emitter(mode)
    prepass = _run_prepass_or_abort(
        api_key=api_key, model=args.model, scans_dir=bp.scans_dir, work_dir=bp.work_dir,
        max_tokens=context_prepass.CONTEXT_MAX_TOKENS, on_event=on_event,
        meta=meta, slug=bp.book_home.name,
    )
    if prepass is None:
        return _prepass_fail_summary(mode, "smoke", bp.ocr_dir)
    block, prepass_cost = prepass
    summary = ocr.run_batch(
        api_key=api_key, input_dir=bp.scans_dir, output_dir=bp.ocr_dir,
        model=args.model, workers=args.workers, pattern=IMAGE_PATTERNS,
        limit=10, max_tokens=args.max_tokens, on_event=on_event, prompt_context=block,
    )
    if summary["fail"] > 0:
        # Smoke fail ngay → đừng ước cost / gate, báo lỗi để user sửa input/key.
        if mode == "json":
            json_output.print_summary(json_output.build_summary(
                stage="smoke", status="partial",
                pages=(collector.pages() if collector else {}),
                cost_usd=(collector.cost_usd() if collector else summary["cost_usd"]),
                paths={"ocr_dir": str(bp.ocr_dir.resolve())},
                extra={"error": f"{summary['fail']} smoke page fail; kiểm tra ảnh/key trước khi chạy full"}))
        else:
            print(f"\nSmoke có {summary['fail']} page fail. Sửa rồi chạy lại.", file=sys.stderr)
        return 1

    # Mini epub từ ≤10 trang đã OCR (file riêng book.smoke.* — không đụng epub thật).
    built = _build_book(bp, bp.scans_dir, meta, suffix=".smoke")
    smoke_epub = built["book_epub"]
    size_kb = built["epub_result"]["size_bytes"] // 1024
    print(f"✓ smoke epub: {smoke_epub} ({size_kb}KB)", file=human_out)

    # Ước cost FULL cho số trang còn lại (chưa OCR). Tính SAU smoke nên phản ánh
    # đúng phần còn lại (10 trang smoke đã loại khỏi pending).
    remaining, total = ocr.collect_pending_pages(bp.scans_dir, IMAGE_PATTERNS, bp.ocr_dir, None)
    # Giá/trang đo THẬT từ smoke (cost token-based) chính xác hơn flat $0.05; chỉ
    # dùng khi có ≥1 trang OCR ok, else fallback hằng số (tránh chia 0).
    smoke_ocr_cost = collector.cost_usd() if collector else summary["cost_usd"]
    # per_page CHỈ tính từ OCR cost (không gồm prepass) → ước full chính xác cho phần
    # còn lại. prepass là one-off, full sau smoke = cache hit (cost 0) nên không cộng vào est_full.
    per_page = (smoke_ocr_cost / summary["ok"]) if summary["ok"] > 0 else EST_COST_PER_PAGE
    est_full = len(remaining) * per_page
    smoke_cost = smoke_ocr_cost + prepass_cost  # tổng đã tiêu ở smoke (OCR + prepass one-off)

    # --yes: agent/CI cố ý bỏ qua prompt → chạy full luôn. Trả prepass_cost (float)
    # để full fold vào tổng cost cuối (full = cache hit nên không tự tính lại).
    if args.yes:
        print(f"--yes: tiếp tục full (~${est_full:.2f} cho {len(remaining)} trang còn lại).", file=human_out)
        return prepass_cost

    # json/json-lines mode: không prompt được → in summary gate, exit 0 (gate có
    # chủ đích, KHÔNG phải lỗi). Agent đọc est_full_cost_usd, hỏi user, re-invoke --yes.
    if mode in ("json", "json-lines"):
        json_output.print_summary(json_output.build_summary(
            stage="smoke", status="smoke",
            pages=(collector.pages() if collector else {}),
            cost_usd=smoke_cost,
            paths={"ocr_dir": str(bp.ocr_dir.resolve()),
                   "smoke_epub": str(smoke_epub.resolve())},
            extra={"est_full_cost_usd": round(est_full, 4),
                   "remaining_pages": len(remaining),
                   "total_pages": total,
                   "prepass_cost_usd": round(prepass_cost, 4),
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
        return prepass_cost  # float → caller chạy full, carry prepass cost vào tổng
    print(f"Aborted. Smoke epub giữ ở {smoke_epub}.", file=human_out)
    return 0
