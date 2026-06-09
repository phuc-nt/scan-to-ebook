"""Tests cho archive_extract: CBZ/ZIP giải nén → scans/page_NNN.<ext>.

CBZ/ZIP path test đầy đủ (stdlib zipfile, không cần tool ngoài). CBR path chỉ
test probe/install-hint khi không có unar/unrar (không dựng RAR thật vì cần tool).
Kiểm: natural-sort, cruft filter (__MACOSX/dotfile/non-image), zip-slip guard,
zero-image raise.
"""

from __future__ import annotations

import pytest

from scan_to_ebook import archive_extract

from conftest import make_jpeg, make_png


# ------------------------------------------------------------------- _is_image_name

@pytest.mark.parametrize("name,expected", [
    ("001.jpg", True),
    ("page.PNG", True),
    ("sub/010.jpeg", True),
    ("cover.gif", True),
    ("__MACOSX/._001.jpg", False),
    (".DS_Store", False),
    ("sub/.hidden.png", False),
    ("ComicInfo.xml", False),
    ("readme.txt", False),
])
def test_is_image_name(name, expected):
    assert archive_extract._is_image_name(name) is expected


# ------------------------------------------------------------------------ extract zip

def test_extract_cbz_natural_sort(make_cbz, tmp_path):
    """1.jpg, 2.jpg, 10.jpg → page_001..003 theo natural order (không lexical)."""
    entries = {
        "1.jpg": make_jpeg(800, 1200),
        "2.jpg": make_jpeg(800, 1200),
        "10.jpg": make_jpeg(800, 1200),
    }
    archive = make_cbz(entries)
    scans = tmp_path / "scans"
    n = archive_extract.extract(archive, scans)
    assert n == 3
    # natural order: 1, 2, 10 → page_001, page_002, page_003
    assert sorted(p.name for p in scans.glob("page_*")) == [
        "page_001.jpg", "page_002.jpg", "page_003.jpg",
    ]


def test_extract_mixed_extensions(make_cbz, tmp_path):
    entries = {
        "001.jpg": make_jpeg(800, 1200),
        "002.png": make_png(800, 1200),
    }
    archive = make_cbz(entries)
    scans = tmp_path / "scans"
    n = archive_extract.extract(archive, scans)
    assert n == 2
    assert sorted(p.name for p in scans.glob("page_*")) == [
        "page_001.jpg", "page_002.png",
    ]


def test_extract_skips_cruft(make_cbz, tmp_path):
    """__MACOSX/, dotfile, ComicInfo.xml bị bỏ — chỉ ảnh thật được carve."""
    entries = {
        "001.jpg": make_jpeg(800, 1200),
        "__MACOSX/._001.jpg": b"junk",
        ".DS_Store": b"junk",
        "ComicInfo.xml": b"<xml/>",
        "002.jpg": make_jpeg(800, 1200),
    }
    archive = make_cbz(entries)
    scans = tmp_path / "scans"
    assert archive_extract.extract(archive, scans) == 2


def test_extract_nested_folder(make_cbz, tmp_path):
    """Ảnh trong folder con (CBZ đôi khi nest) vẫn được carve (rglob recursive)."""
    entries = {
        "chapter1/001.jpg": make_jpeg(800, 1200),
        "chapter1/002.jpg": make_jpeg(800, 1200),
    }
    archive = make_cbz(entries)
    scans = tmp_path / "scans"
    assert archive_extract.extract(archive, scans) == 2


def test_extract_zero_images_raises(make_cbz, tmp_path):
    entries = {"ComicInfo.xml": b"<xml/>", "readme.txt": b"hi"}
    archive = make_cbz(entries)
    with pytest.raises(ValueError, match="0 ảnh"):
        archive_extract.extract(archive, tmp_path / "scans")


# ------------------------------------------------------------------------ zip-slip

def test_zip_slip_blocked(tmp_path):
    """Member với path traversal (../) bị chặn trước khi ghi ra ngoài tmp."""
    import zipfile

    archive = tmp_path / "evil.cbz"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../../escape.jpg", make_jpeg(800, 1200))
    with pytest.raises(ValueError, match="zip-slip"):
        archive_extract.extract(archive, tmp_path / "scans")


# ---------------------------------------------------------------- CBR backend probe

def test_cbr_no_backend_raises(tmp_path, monkeypatch):
    """CBR mà không có unar/unrar → RuntimeError kèm install hint, không crash."""
    monkeypatch.setattr(archive_extract, "available_rar_backends", lambda: [])
    cbr = tmp_path / "test.cbr"
    cbr.write_bytes(b"Rar!\x1a\x07\x00fake")
    with pytest.raises(RuntimeError, match="unar"):
        archive_extract.extract(cbr, tmp_path / "scans")


def test_install_hint_nonempty():
    assert archive_extract._install_hint()


def test_available_rar_backends_returns_list(monkeypatch):
    monkeypatch.setattr(archive_extract, "_has", lambda b: b == "unar")
    assert archive_extract.available_rar_backends() == ["unar"]
    monkeypatch.setattr(archive_extract, "_has", lambda b: True)
    assert archive_extract.available_rar_backends() == ["unar", "unrar"]
