"""Manga pipeline: nguồn ảnh trang (4 dạng input) → EPUB3 fixed-layout RTL.

Khác pipeline chữ (`all`): KHÔNG OCR, KHÔNG pandoc, KHÔNG pre-pass. Chỉ:
  normalize_input(src) → scans/page_NNN.<ext>  (giao diện chung mọi adapter)
  → epub3_fixed_layout.build(scans) → dist/<slug>.epub.

Dispatch theo loại `--from`:
  Drive URL      → drive_download (file/folder, Phase 03)
  .mobi/.azw3    → mobi_extract  (carve ảnh từ PDB record, Phase 02)
  .cbz/.cbr/.zip → archive_extract (giải nén, Phase 02)
  thư mục        → pipeline._import_images  (dùng lại nguyên, Phase 01)

Module này KHÔNG import cli (1 chiều: cli → manga_pipeline → adapters/builder).
Adapter import lazy trong dispatch để Phase 02/03 chỉ cần THÊM file module, không
sửa lại hình dạng dispatch ở đây.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from . import drive_download, pipeline

# Đuôi nhận diện nguồn — dùng lại hằng từ các module chuyên trách khi có.
_MOBI_SUFFIXES = {".mobi", ".azw3"}
_ARCHIVE_SUFFIXES = {".cbz", ".cbr", ".zip"}

# Schema metadata manga = schema chữ {title,author,lang,year} + field manga.
# lang mặc định "ja" (manga gốc Nhật, RTL); subject "Manga"; rtl bật.
_MANGA_DEFAULTS = {
    "title": None,  # điền slug ở load
    "author": None,
    "lang": "ja",
    "year": None,
    "series": None,
    "series_index": None,
    "subject": "Manga",
    "publisher": None,
    "description": None,
    "rtl": True,
}


def _is_mobi(p: Path) -> bool:
    return p.suffix.lower() in _MOBI_SUFFIXES


def _is_archive(p: Path) -> bool:
    return p.suffix.lower() in _ARCHIVE_SUFFIXES


def normalize_input(src: str, scans_dir: Path) -> int:
    """Đưa BẤT KỲ nguồn nào về scans/page_NNN.<ext>. Trả số trang.

    Đây là giao diện chung: builder chỉ đọc scans/, không quan tâm nguồn gì.
    Mỗi nhánh adapter import lazy để tránh phụ thuộc cứng lúc load module (Phase
    02/03 thêm module tương ứng, dispatch ở đây giữ nguyên).
    """
    if drive_download.is_drive_url(src):
        # Phase 03: file Drive non-PDF + folder listing (SSRF-safe).
        from . import drive_input

        return drive_input.fetch_to_scans(src, scans_dir)

    p = Path(src).expanduser()
    if p.is_dir():
        return pipeline._import_images(p, scans_dir)
    if _is_mobi(p):
        from . import mobi_extract

        return mobi_extract.extract(p, scans_dir)
    if _is_archive(p):
        from . import archive_extract

        return archive_extract.extract(p, scans_dir)
    raise SystemExit(
        f"--from không nhận dạng được: {src} "
        "(cần: thư mục ảnh | .mobi/.azw3 | .cbz/.cbr/.zip | link Google Drive)"
    )


def load_manga_metadata(scans_dir: Path, slug: str) -> dict:
    """Đọc metadata.json (schema manga). Thiếu/file hỏng → mặc định.

    Loader RIÊNG, KHÔNG đụng pipeline._load_metadata (chữ default lang="vi", manga
    "ja"). Field thiếu trong file cũ → default None/giá trị manga (tương thích lùi).
    """
    meta = dict(_MANGA_DEFAULTS)
    meta["title"] = slug
    meta_file = scans_dir / "metadata.json"
    if not meta_file.exists():
        return meta
    try:
        with meta_file.open(encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"WARN metadata.json invalid: {exc} — dùng mặc định", file=sys.stderr)
        return meta
    for k in _MANGA_DEFAULTS:
        if d.get(k) is not None:
            meta[k] = d[k]
    meta["title"] = d.get("title") or slug
    return meta


def write_manga_metadata(scans_dir: Path, slug: str, args) -> None:
    """Ghi metadata.json từ cờ CLI nếu CHƯA có file. Có rồi → giữ nguyên (user-edit).

    Mirror cli.cmd_init: init tạo metadata trước, user có thể sửa tay sau.
    """
    meta_file = scans_dir / "metadata.json"
    if meta_file.exists():
        print(f"metadata.json đã tồn tại, giữ nguyên: {meta_file}")
        return
    meta = {
        "title": getattr(args, "title", None) or slug,
        "author": getattr(args, "author", None),
        "lang": getattr(args, "lang", None) or "ja",
        "year": getattr(args, "year", None),
        "series": getattr(args, "series", None),
        "series_index": getattr(args, "series_index", None),
        "subject": getattr(args, "subject", None) or "Manga",
        "publisher": getattr(args, "publisher", None),
        "description": getattr(args, "description", None),
        "rtl": getattr(args, "rtl", True),
    }
    meta_file.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Tạo metadata.json: {meta_file}")


def parse_spread_reset(raw: str | None) -> set[int]:
    """'5,12' → {5,12}. None/'' → set rỗng. Bỏ token rỗng/không phải số.

    spread_reset = số trang nơi tái neo nhịp ghép đôi RTL (Phase 04 dùng).
    """
    if not raw:
        return set()
    out: set[int] = set()
    dropped: list[str] = []
    for tok in raw.split(","):
        tok = tok.strip()
        if tok.isdigit():
            out.add(int(tok))
        elif tok:
            dropped.append(tok)
    if dropped:
        # Cảnh báo to: user gõ sai separator (5;12) → set rỗng, spread sai âm thầm.
        print(
            f"WARN --spread-reset bỏ qua token không phải số: {', '.join(dropped)} "
            "(định dạng đúng: số trang phân tách bằng dấu phẩy, vd 5,12)",
            file=sys.stderr,
        )
    return out


def build_manga(bp: pipeline.BookPaths, slug: str, meta: dict, spread_reset: set[int],
                min_px: int) -> int:
    """Gọi builder EPUB3 fixed-layout → dist/<slug>.epub. Trả exit code.

    Builder import lazy (Phase 04 thêm module). Builder trả stats gồm `valid`
    (validator cấu trúc stdlib) → exit !=0 nếu epub hỏng cấu trúc.
    """
    from . import epub3_fixed_layout

    bp.dist_dir.mkdir(parents=True, exist_ok=True)
    out_epub = bp.dist_dir / f"{slug}.epub"
    stats = epub3_fixed_layout.build(
        img_dir=bp.scans_dir,
        out_epub=out_epub,
        slug=slug,
        title=meta["title"],
        author=meta["author"],
        lang=meta["lang"],
        rtl=meta["rtl"],
        min_px=min_px,
        series=meta["series"],
        series_index=meta["series_index"],
        publisher=meta["publisher"],
        date=meta["year"],
        subject=meta["subject"],
        description=meta["description"],
        spread_reset=spread_reset,
    )
    valid = stats.get("valid", True)
    glyph = "✓" if valid else "WARN cấu trúc EPUB lỗi"
    print(
        f"{glyph} {out_epub} ({stats.get('size_bytes', 0) // 1024}KB) | "
        f"trang={stats.get('pages')} đôi={stats.get('double_pages')} "
        f"ppd={stats.get('ppd')}"
    )
    if not valid:
        for e in stats.get("errors", []):
            print(f"  - {e}", file=sys.stderr)
    return 0 if valid else 1
