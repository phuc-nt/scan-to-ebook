"""EPUB3 fixed-layout builder cho manga RTL.

Đọc scans/page_NNN.{jpg,png,gif} → emit dist/<slug>.epub chuẩn EPUB3 fixed-layout.
Hỗ trợ: nhịp ghép đôi spread RTL, landscape double-page, manual spread-reset override,
uuid5 bookid ổn định (rebuild cùng slug → cùng dc:identifier), validator cấu trúc stdlib.

Không dùng thư viện ngoài — chỉ stdlib.
"""

from __future__ import annotations

import datetime
import html
import struct
import sys
import uuid
import zipfile
from pathlib import Path

from . import ocr
from .epub3_validate import validate_epub3

_IMG_EXTS = {".jpg", ".jpeg", ".png", ".gif"}
_MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
}


def jpeg_dims(p: Path) -> tuple[int, int] | None:
    """Parse JPEG SOF marker → (width, height). Trả None nếu không đọc được."""
    try:
        with open(p, "rb") as f:
            f.read(2)
            while True:
                b = f.read(2)
                if len(b) < 2:
                    return None
                (m,) = struct.unpack(">H", b)
                if 0xFFC0 <= m <= 0xFFCF and m not in (0xFFC4, 0xFFC8, 0xFFCC):
                    f.read(3)
                    h, w = struct.unpack(">HH", f.read(4))
                    return (w, h)
                (ln,) = struct.unpack(">H", f.read(2))
                f.seek(ln - 2, 1)
    except Exception:  # noqa: BLE001
        return None


def png_dims(p: Path) -> tuple[int, int] | None:
    """Parse PNG IHDR → (width, height). Width/height là big-endian uint32 ở offset 16."""
    try:
        with open(p, "rb") as f:
            f.seek(16)
            w, h = struct.unpack(">II", f.read(8))
            return (w, h)
    except Exception:  # noqa: BLE001
        return None


def gif_dims(p: Path) -> tuple[int, int] | None:
    """Parse GIF logical screen descriptor → (width, height). Little-endian uint16 ở byte 6."""
    try:
        with open(p, "rb") as f:
            f.seek(6)
            w, h = struct.unpack("<HH", f.read(4))
            return (w, h)
    except Exception:  # noqa: BLE001
        return None


def _image_dims(p: Path) -> tuple[int, int] | None:
    """Dispatch đọc dims theo magic bytes. Unknown/unreadable → None (caller skip+warn)."""
    try:
        with open(p, "rb") as f:
            magic = f.read(8)
    except OSError:
        return None
    if magic[:2] == b"\xff\xd8":
        return jpeg_dims(p)
    if magic[:8] == b"\x89PNG\r\n\x1a\n":
        return png_dims(p)
    if magic[:6] in (b"GIF87a", b"GIF89a"):
        return gif_dims(p)
    return None


def build(
    img_dir: Path | str,
    out_epub: Path | str,
    slug: str,
    title: str,
    author: str | None,
    lang: str = "ja",
    rtl: bool = True,
    min_px: int = 400,
    series: str | None = None,
    series_index: int | None = None,
    publisher: str | None = None,
    date: str | None = None,
    subject: str = "Manga",
    description: str | None = None,
    spread_reset: set[int] | None = None,
    cover_index: int = 1,
    modified: str | None = None,
) -> dict:
    """Build EPUB3 fixed-layout. Trả stats: pages, double_pages, ppd, size_bytes, valid, errors.

    cover_index (1-based, sau khi lọc min_px): trang dùng làm cover-image trong
    thư viện reader. Mặc định 1 (trang đầu). Bản scan đôi khi chèn banner nhóm
    dịch / bìa-sau trước bìa thật → chỉ cover_index tới trang bìa thật. Chỉ đổi
    cover-image (manifest property + OPF meta + nav landmark), KHÔNG đổi thứ tự
    trang hay nhịp ghép đôi spine — banner vẫn nằm trong sách như bản gốc scan.
    """
    img_dir = Path(img_dir)
    out_epub = Path(out_epub)

    # Tập hợp ảnh: jpg + png + gif, natural-sort (page_9 < page_10).
    imgs = sorted(
        [p for p in img_dir.iterdir() if p.suffix.lower() in _IMG_EXTS],
        key=ocr.natural_sort_key,
    )
    pages: list[tuple[Path, tuple[int, int]]] = []
    dropped_small = 0
    for p in imgs:
        dims = _image_dims(p)
        if dims is None:
            print(f"WARN bỏ qua {p.name}: không đọc được kích thước", file=sys.stderr)
            continue
        if max(dims) < min_px:
            dropped_small += 1
            continue
        pages.append((p, dims))

    if not pages:
        raise SystemExit(f"epub3_fixed_layout: không có trang nào hợp lệ trong {img_dir}")

    if dropped_small:
        # Manga: mỗi trang quan trọng — báo to khi lọc bớt (banner/thumbnail nhỏ),
        # phòng khi vô tình mất trang thật vì min_px quá cao.
        print(
            f"WARN bỏ {dropped_small} ảnh nhỏ hơn min_px={min_px} "
            "(banner/thumbnail?); tăng --min-px nếu mất trang thật",
            file=sys.stderr,
        )

    # Stable bookid: uuid5 từ slug → rebuild cùng slug → cùng dc:identifier.
    book_uuid = uuid.uuid5(uuid.NAMESPACE_URL, "scan2ebook:manga:" + slug)
    bookid = "urn:uuid:" + str(book_uuid)
    ppd = "rtl" if rtl else "ltr"
    first_side = "right" if rtl else "left"
    other_side = "left" if rtl else "right"

    if modified is None:
        modified = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    z = zipfile.ZipFile(out_epub, "w", zipfile.ZIP_DEFLATED)
    z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
    z.writestr(
        "META-INF/container.xml",
        '<?xml version="1.0"?>\n<container version="1.0" '
        'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        "<rootfiles><rootfile full-path=\"OEBPS/content.opf\" "
        'media-type="application/oebps-package+xml"/></rootfiles></container>',
    )

    # cover_index 1-based trên list pages ĐÃ lọc min_px. Clamp vào [1, len] —
    # ngoài khoảng → fallback trang 1 + cảnh báo (tránh sách không có cover-image).
    if cover_index < 1 or cover_index > len(pages):
        print(
            f"WARN cover_index={cover_index} ngoài [1,{len(pages)}] → dùng trang 1",
            file=sys.stderr,
        )
        cover_index = 1
    cover_xhtml = f"xhtml/page_{cover_index:04d}.xhtml"

    manifest, spine, pagelist = [], [], []
    side = first_side
    for i, (p, (w, h)) in enumerate(pages, 1):
        ext = p.suffix.lower()
        media_type = _MEDIA_TYPES.get(ext, "image/jpeg")
        imgname = f"img/page_{i:04d}{ext}"
        xname = f"xhtml/page_{i:04d}.xhtml"
        z.write(str(p), f"OEBPS/{imgname}")
        xhtml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE html>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml" '
            'xmlns:epub="http://www.idpf.org/2007/ops">\n'
            f'<head><meta charset="utf-8"/><title>{i}</title>\n'
            f'<meta name="viewport" content="width={w}, height={h}"/>\n'
            "<style>html,body{margin:0;padding:0;}"
            "img{width:100%;height:100%;}</style></head>\n"
            f'<body><img src="../{imgname}" alt="page {i}"/></body></html>'
        )
        z.writestr(f"OEBPS/{xname}", xhtml)

        cover = ' properties="cover-image"' if i == cover_index else ""
        manifest.append(
            f'<item id="img{i}" href="{imgname}" media-type="{media_type}"{cover}/>'
        )
        manifest.append(
            f'<item id="pg{i}" href="{xname}" media-type="application/xhtml+xml"/>'
        )

        # Nhịp ghép đôi spread — giữ nguyên thuật toán từ prototype (verified 24/24).
        # spread_reset: neo lại về first_side tại trang chỉ định (không ghi đè
        # cover/landscape vì chúng đã tự reset).
        if i == 1:
            sp = "page-spread-center"   # cover đứng một mình
            side = first_side           # trang nội thất tiếp theo bắt đầu cadence
        elif w > h:
            sp = "page-spread-center"   # landscape = double-page spread
            side = first_side           # reset cadence sau trang full-width
        else:
            if spread_reset and i in spread_reset:
                side = first_side       # tái neo về first_side (không ghi đè center)
            sp = f"page-spread-{side}"
            side = other_side if side == first_side else first_side
        spine.append(f'<itemref idref="pg{i}" properties="{sp}"/>')
        pagelist.append(f'<li><a href="{xname}">{i}</a></li>')

    # nav.xhtml: toc + page-list + landmarks.
    # "Start"/"Begin Reading" trỏ page_0002 — chỉ thêm khi CÓ trang 2 (sách 1 trang
    # = chỉ bìa → href page_0002 sẽ là nav treo, dù validator không bắt nav-internal).
    has_start = len(pages) >= 2
    toc_start = '<li><a href="xhtml/page_0002.xhtml">Start</a></li>' if has_start else ""
    landmark_start = (
        '<li><a epub:type="bodymatter" href="xhtml/page_0002.xhtml">Begin Reading</a></li>'
        if has_start else ""
    )
    nav = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops">\n'
        '<head><meta charset="utf-8"/><title>Navigation</title></head>\n<body>\n'
        '<nav epub:type="toc" id="toc"><h1>Contents</h1><ol>'
        f'<li><a href="{cover_xhtml}">Cover</a></li>'
        + toc_start
        + "</ol></nav>\n"
        '<nav epub:type="page-list" id="page-list" hidden=""><h2>Pages</h2><ol>'
        + "".join(pagelist)
        + "</ol></nav>\n"
        '<nav epub:type="landmarks" id="landmarks" hidden=""><h2>Landmarks</h2><ol>'
        f'<li><a epub:type="cover" href="{cover_xhtml}">Cover</a></li>'
        + landmark_start
        + "</ol></nav>\n</body></html>"
    )
    z.writestr("OEBPS/nav.xhtml", nav)
    manifest.append(
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
    )

    # Series metadata
    series_meta = ""
    if series:
        series_meta = (
            f'<meta property="belongs-to-collection" id="coll">{html.escape(series)}</meta>\n'
            '<meta refines="#coll" property="collection-type">series</meta>\n'
        )
        if series_index:
            series_meta += f'<meta refines="#coll" property="group-position">{series_index}</meta>\n'

    extra = ""
    if publisher:
        extra += f"<dc:publisher>{html.escape(publisher)}</dc:publisher>\n"
    if date:
        extra += f"<dc:date>{date}</dc:date>\n"
    if subject:
        extra += f"<dc:subject>{html.escape(subject)}</dc:subject>\n"
    if description:
        extra += f"<dc:description>{html.escape(description)}</dc:description>\n"

    # OPF — metadata block verified 24/24 trên Apple Books (prototype). Giữ nguyên.
    opf = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
        'unique-identifier="bookid" '
        'prefix="rendition: http://www.idpf.org/vocab/rendition/# '
        'schema: http://schema.org/">\n'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
        f"<dc:identifier id=\"bookid\">{bookid}</dc:identifier>\n"
        f"<dc:title>{html.escape(title)}</dc:title>\n"
        f'<dc:creator id="cr">{html.escape(author or "")}</dc:creator>\n'
        '<meta refines="#cr" property="role" scheme="marc:relators">art</meta>\n'
        f"<dc:language>{lang}</dc:language>\n"
        f"<meta property=\"dcterms:modified\">{modified}</meta>\n"
        + extra
        + series_meta
        + '<meta property="rendition:layout">pre-paginated</meta>\n'
        '<meta property="rendition:spread">landscape</meta>\n'
        '<meta property="rendition:orientation">auto</meta>\n'
        '<meta property="schema:accessMode">visual</meta>\n'
        '<meta property="schema:accessModeSufficient">visual</meta>\n'
        '<meta property="schema:accessibilityFeature">none</meta>\n'
        '<meta property="schema:accessibilityHazard">none</meta>\n'
        "<meta property=\"schema:accessibilitySummary\">"
        "Image-only manga; pages are scanned bitmaps with no text layer.</meta>\n"
        f'<meta name="cover" content="img{cover_index}"/>\n'
        "</metadata>\n<manifest>\n"
        + "\n".join(manifest)
        + "\n</manifest>\n"
        f'<spine page-progression-direction="{ppd}">\n'
        + "\n".join(spine)
        + "\n</spine>\n</package>"
    )
    z.writestr("OEBPS/content.opf", opf)
    z.close()

    # Stats
    double_pages = sum(1 for s in spine if "center" in s) - 1  # trừ cover
    size_bytes = out_epub.stat().st_size

    # Validate cấu trúc — WARN không raise (epub vẫn được tạo để inspect).
    result = validate_epub3(out_epub)
    if not result["valid"]:
        print(f"WARN cấu trúc EPUB không hợp lệ: {out_epub}", file=sys.stderr)

    return {
        "pages": len(pages),
        "double_pages": max(double_pages, 0),
        "ppd": ppd,
        "size_bytes": size_bytes,
        "valid": result["valid"],
        "errors": result["errors"],
    }
