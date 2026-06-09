"""Tests cho epub3_fixed_layout.build + epub3_validate.

Dựng EPUB từ ảnh tổng hợp (conftest) rồi mở zip kiểm OPF/spine/nav/mimetype.
Kiểm: dims parser jpg/png/gif, spread cadence RTL, landscape center+reset,
manual spread-reset override, stable uuid5, media-type per ext, validator 7 check.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile

import pytest

from scan_to_ebook import epub3_fixed_layout as efl
from scan_to_ebook import epub3_validate

from conftest import make_gif, make_jpeg, make_png

_OPF_NS = "http://www.idpf.org/2007/opf"


# ------------------------------------------------------------------------- dims parsers

def test_jpeg_dims(tmp_path):
    p = tmp_path / "a.jpg"
    p.write_bytes(make_jpeg(640, 960))
    assert efl.jpeg_dims(p) == (640, 960)


def test_png_dims(tmp_path):
    p = tmp_path / "a.png"
    p.write_bytes(make_png(500, 700))
    assert efl.png_dims(p) == (500, 700)


def test_gif_dims(tmp_path):
    p = tmp_path / "a.gif"
    p.write_bytes(make_gif(300, 450))
    assert efl.gif_dims(p) == (300, 450)


def test_image_dims_dispatch(tmp_path):
    j = tmp_path / "a.jpg"
    j.write_bytes(make_jpeg(10, 20))
    n = tmp_path / "b.png"
    n.write_bytes(make_png(30, 40))
    g = tmp_path / "c.gif"
    g.write_bytes(make_gif(50, 60))
    assert efl._image_dims(j) == (10, 20)
    assert efl._image_dims(n) == (30, 40)
    assert efl._image_dims(g) == (50, 60)


def test_image_dims_unreadable_returns_none(tmp_path):
    p = tmp_path / "x.jpg"
    p.write_bytes(b"not an image")
    assert efl._image_dims(p) is None


# ------------------------------------------------------------------------- build basic

def _opf_root(epub_path):
    with zipfile.ZipFile(epub_path) as z:
        return ET.fromstring(z.read("OEBPS/content.opf").decode("utf-8"))


def test_build_produces_valid_epub(image_scans_dir, tmp_path):
    out = tmp_path / "out.epub"
    stats = efl.build(
        img_dir=image_scans_dir, out_epub=out, slug="test-manga",
        title="Test Manga", author="Someone",
    )
    assert out.exists()
    assert stats["valid"] is True
    assert stats["errors"] == []
    assert stats["pages"] == 5
    assert stats["ppd"] == "rtl"


def test_mimetype_first_and_stored(image_scans_dir, tmp_path):
    out = tmp_path / "out.epub"
    efl.build(img_dir=image_scans_dir, out_epub=out, slug="s", title="T", author=None)
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        assert names[0] == "mimetype"
        info = z.getinfo("mimetype")
        assert info.compress_type == zipfile.ZIP_STORED
        assert z.read("mimetype") == b"application/epub+zip"


def test_spread_cadence_rtl(image_scans_dir, tmp_path):
    """page1=center(cover), page3=center(landscape), portraits alternate right/left."""
    out = tmp_path / "out.epub"
    efl.build(img_dir=image_scans_dir, out_epub=out, slug="s", title="T", author=None)
    root = _opf_root(out)
    spine = root.find(f"{{{_OPF_NS}}}spine")
    props = [ir.get("properties") for ir in spine.findall(f"{{{_OPF_NS}}}itemref")]
    # 5 pages: cover, p2, landscape p3, p4, p5
    assert props[0] == "page-spread-center"   # cover
    assert props[1] == "page-spread-right"    # first interior, RTL starts right
    assert props[2] == "page-spread-center"   # landscape resets
    assert props[3] == "page-spread-right"    # reset → right again
    assert props[4] == "page-spread-left"     # alternate


def test_ltr_page_progression(image_scans_dir, tmp_path):
    out = tmp_path / "out.epub"
    efl.build(img_dir=image_scans_dir, out_epub=out, slug="s", title="T", author=None, rtl=False)
    root = _opf_root(out)
    spine = root.find(f"{{{_OPF_NS}}}spine")
    assert spine.get("page-progression-direction") == "ltr"


def test_spread_reset_override(tmp_path):
    """--spread-reset N tái neo về first_side (right RTL) tại trang N."""
    scans = tmp_path / "scans"
    scans.mkdir()
    for i in range(1, 6):
        (scans / f"page_{i:03d}.jpg").write_bytes(make_jpeg(800, 1200))  # all portrait
    out = tmp_path / "out.epub"
    # Không reset: 1=center,2=right,3=left,4=right,5=left
    # Reset tại page 4 → page4 = right (thay vì right tự nhiên — verify page5 thành left)
    efl.build(img_dir=scans, out_epub=out, slug="s", title="T", author=None,
              spread_reset={3})
    root = _opf_root(out)
    spine = root.find(f"{{{_OPF_NS}}}spine")
    props = [ir.get("properties") for ir in spine.findall(f"{{{_OPF_NS}}}itemref")]
    # page3 forced right (would have been left without reset)
    assert props[2] == "page-spread-right"
    assert props[3] == "page-spread-left"


def test_stable_uuid_across_builds(image_scans_dir, tmp_path):
    """Cùng slug → cùng dc:identifier (rebuild giữ identity reader-library)."""
    out1 = tmp_path / "a.epub"
    out2 = tmp_path / "b.epub"
    efl.build(img_dir=image_scans_dir, out_epub=out1, slug="same-slug", title="T", author=None)
    efl.build(img_dir=image_scans_dir, out_epub=out2, slug="same-slug", title="T", author=None)
    id1 = _opf_root(out1).find(".//{http://purl.org/dc/elements/1.1/}identifier").text
    id2 = _opf_root(out2).find(".//{http://purl.org/dc/elements/1.1/}identifier").text
    assert id1 == id2
    assert id1.startswith("urn:uuid:")


def test_different_slug_different_uuid(image_scans_dir, tmp_path):
    out1 = tmp_path / "a.epub"
    out2 = tmp_path / "b.epub"
    efl.build(img_dir=image_scans_dir, out_epub=out1, slug="slug-a", title="T", author=None)
    efl.build(img_dir=image_scans_dir, out_epub=out2, slug="slug-b", title="T", author=None)
    dc = ".//{http://purl.org/dc/elements/1.1/}identifier"
    assert _opf_root(out1).find(dc).text != _opf_root(out2).find(dc).text


def test_media_types_per_ext(tmp_path):
    scans = tmp_path / "scans"
    scans.mkdir()
    (scans / "page_001.jpg").write_bytes(make_jpeg(800, 1200))
    (scans / "page_002.png").write_bytes(make_png(800, 1200))
    (scans / "page_003.gif").write_bytes(make_gif(800, 1200))
    out = tmp_path / "out.epub"
    efl.build(img_dir=scans, out_epub=out, slug="s", title="T", author=None)
    root = _opf_root(out)
    manifest = root.find(f"{{{_OPF_NS}}}manifest")
    mts = {it.get("href"): it.get("media-type")
           for it in manifest.findall(f"{{{_OPF_NS}}}item") if "img/" in (it.get("href") or "")}
    assert mts["img/page_0001.jpg"] == "image/jpeg"
    assert mts["img/page_0002.png"] == "image/png"
    assert mts["img/page_0003.gif"] == "image/gif"


def test_series_metadata(image_scans_dir, tmp_path):
    out = tmp_path / "out.epub"
    efl.build(img_dir=image_scans_dir, out_epub=out, slug="s", title="T", author=None,
              series="My Series", series_index=3)
    with zipfile.ZipFile(out) as z:
        opf = z.read("OEBPS/content.opf").decode("utf-8")
    assert "belongs-to-collection" in opf
    assert "My Series" in opf
    assert "group-position" in opf


def test_min_px_filters_small_images(tmp_path):
    """Ảnh nhỏ hơn min_px (thumbnail) bị loại."""
    scans = tmp_path / "scans"
    scans.mkdir()
    (scans / "page_001.jpg").write_bytes(make_jpeg(800, 1200))  # giữ
    (scans / "page_002.jpg").write_bytes(make_jpeg(100, 150))   # < 400 → drop
    out = tmp_path / "out.epub"
    stats = efl.build(img_dir=scans, out_epub=out, slug="s", title="T", author=None, min_px=400)
    assert stats["pages"] == 1


def test_no_valid_pages_raises(tmp_path):
    scans = tmp_path / "scans"
    scans.mkdir()
    (scans / "page_001.txt").write_text("not an image")
    out = tmp_path / "out.epub"
    with pytest.raises(SystemExit, match="không có trang"):
        efl.build(img_dir=scans, out_epub=out, slug="s", title="T", author=None)


# ------------------------------------------------------------------------- cover_index

def _cover_refs(epub_path):
    """Trả (manifest_cover_href, opf_meta_cover_content, nav_cover_href)."""
    with zipfile.ZipFile(epub_path) as z:
        opf = z.read("OEBPS/content.opf").decode("utf-8")
        nav = z.read("OEBPS/nav.xhtml").decode("utf-8")
    root = ET.fromstring(opf)
    manifest = root.find(f"{{{_OPF_NS}}}manifest")
    cover_item = next(
        it for it in manifest.findall(f"{{{_OPF_NS}}}item")
        if (it.get("properties") or "") and "cover-image" in it.get("properties")
    )
    meta_cover = next(
        m for m in root.iter(f"{{{_OPF_NS}}}meta") if m.get("name") == "cover"
    )
    # nav landmark <a epub:type="cover" href="...">
    nav_root = ET.fromstring(nav)
    cover_a = next(
        a for a in nav_root.iter("{http://www.w3.org/1999/xhtml}a")
        if a.get("{http://www.idpf.org/2007/ops}type") == "cover"
    )
    return cover_item.get("href"), meta_cover.get("content"), cover_a.get("href")


def test_cover_index_marks_chosen_page(tmp_path):
    """REGRESSION (Pluto): bản scan chèn banner trước bìa thật. cover_index=3 →
    cover-image + OPF meta + nav landmark đều trỏ trang 3, KHÔNG phải trang 1."""
    scans = tmp_path / "scans"
    scans.mkdir()
    for i in range(1, 6):
        (scans / f"page_{i:03d}.jpg").write_bytes(make_jpeg(800, 1200))
    out = tmp_path / "out.epub"
    efl.build(img_dir=scans, out_epub=out, slug="s", title="T", author=None,
              cover_index=3)
    href, meta_content, nav_href = _cover_refs(out)
    assert href == "img/page_0003.jpg"
    assert meta_content == "img3"
    assert nav_href == "xhtml/page_0003.xhtml"


def test_cover_index_default_is_first_page(image_scans_dir, tmp_path):
    """Mặc định cover_index=1 → trang 1 là bìa (hành vi cũ không đổi)."""
    out = tmp_path / "out.epub"
    efl.build(img_dir=image_scans_dir, out_epub=out, slug="s", title="T", author=None)
    href, meta_content, nav_href = _cover_refs(out)
    assert href == "img/page_0001.jpg"
    assert meta_content == "img1"
    assert nav_href == "xhtml/page_0001.xhtml"


def test_cover_index_out_of_range_clamps_to_first(tmp_path, capsys):
    """cover_index ngoài [1,len] → fallback trang 1 + WARN (không để sách thiếu bìa)."""
    scans = tmp_path / "scans"
    scans.mkdir()
    for i in range(1, 4):
        (scans / f"page_{i:03d}.jpg").write_bytes(make_jpeg(800, 1200))
    out = tmp_path / "out.epub"
    efl.build(img_dir=scans, out_epub=out, slug="s", title="T", author=None,
              cover_index=99)
    href, meta_content, _ = _cover_refs(out)
    assert href == "img/page_0001.jpg"
    assert meta_content == "img1"
    assert "cover_index=99 ngoài" in capsys.readouterr().err


# ------------------------------------------------------------------------- validator

def test_validate_good_epub(image_scans_dir, tmp_path):
    out = tmp_path / "out.epub"
    efl.build(img_dir=image_scans_dir, out_epub=out, slug="s", title="T", author=None)
    result = epub3_validate.validate_epub3(out)
    assert result["valid"] is True
    assert result["errors"] == []


def test_validate_bad_zip(tmp_path):
    bad = tmp_path / "bad.epub"
    bad.write_bytes(b"this is not a zip file")
    result = epub3_validate.validate_epub3(bad)
    assert result["valid"] is False
    assert any("zip" in e.lower() for e in result["errors"])


def test_validate_mimetype_not_first(tmp_path):
    """mimetype không phải entry đầu → invalid."""
    epub = tmp_path / "x.epub"
    with zipfile.ZipFile(epub, "w") as z:
        z.writestr("other.txt", "x")
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
    result = epub3_validate.validate_epub3(epub)
    assert result["valid"] is False
    assert any("mimetype" in e for e in result["errors"])


def test_validate_missing_container(tmp_path):
    epub = tmp_path / "x.epub"
    with zipfile.ZipFile(epub, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
    result = epub3_validate.validate_epub3(epub)
    assert result["valid"] is False
    assert any("container.xml" in e for e in result["errors"])
