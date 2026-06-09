"""Tests cho mobi_extract: carve ảnh từ PDB record table (.mobi/.azw3).

Dùng PDB tổng hợp (conftest.make_pdb) — KHÔNG file thật. Kiểm:
- pdb_records: parse bảng offset đúng, bounds guard (file ngắn / offset lậu).
- img_ext: magic-byte sniff jpg/png/gif/None.
- extract: lọc MIN_BYTES + non-image, đánh số page_NNN, raise nếu 0 ảnh.
"""

from __future__ import annotations

import pytest

from scan_to_ebook import mobi_extract

from conftest import make_gif, make_jpeg, make_png, pad_to


# -------------------------------------------------------------------- pdb_records

def test_pdb_records_parses_offsets(make_pdb):
    recs = [b"AAAA", b"BBBBBB", b"C"]
    p = make_pdb(recs)
    parsed = mobi_extract.pdb_records(p.read_bytes())
    assert parsed == recs


def test_pdb_records_too_short_raises():
    with pytest.raises(ValueError, match="too short"):
        mobi_extract.pdb_records(b"\x00" * 10)


def test_pdb_records_offset_table_truncated_raises():
    # numRecords=5 nhưng file không đủ chỗ cho bảng offset
    import struct

    data = bytearray(78)
    struct.pack_into(">H", data, 76, 5)  # khai 5 record nhưng không có bảng
    with pytest.raises(ValueError, match="offset table"):
        mobi_extract.pdb_records(bytes(data))


def test_pdb_records_corrupt_offset_raises():
    import struct

    data = bytearray(78 + 8)
    struct.pack_into(">H", data, 76, 1)
    struct.pack_into(">I", data, 78, 999999)  # offset > file length
    with pytest.raises(ValueError, match="corrupt"):
        mobi_extract.pdb_records(bytes(data))


# ------------------------------------------------------------------------ img_ext

def test_img_ext_jpg():
    assert mobi_extract.img_ext(make_jpeg(10, 10)) == "jpg"


def test_img_ext_png():
    assert mobi_extract.img_ext(make_png(10, 10)) == "png"


def test_img_ext_gif():
    assert mobi_extract.img_ext(make_gif(10, 10)) == "gif"


def test_img_ext_non_image():
    assert mobi_extract.img_ext(b"not an image at all") is None


# ------------------------------------------------------------------------ extract

def test_extract_carves_images_in_order(make_pdb, tmp_path):
    records = [
        pad_to(make_jpeg(800, 1200), 2000),
        pad_to(make_png(800, 1200), 2000),
        pad_to(make_gif(800, 1200), 2000),
    ]
    pdb = make_pdb(records)
    scans = tmp_path / "scans"
    n = mobi_extract.extract(pdb, scans)
    assert n == 3
    names = sorted(p.name for p in scans.glob("page_*"))
    assert names == ["page_001.jpg", "page_002.png", "page_003.gif"]


def test_extract_drops_tiny_records(make_pdb, tmp_path):
    """Record < MIN_BYTES (thumbnail/cover nhỏ) bị bỏ qua."""
    records = [
        pad_to(make_jpeg(800, 1200), 2000),  # giữ
        make_jpeg(8, 8),                      # < 1000 bytes → drop
    ]
    pdb = make_pdb(records)
    scans = tmp_path / "scans"
    n = mobi_extract.extract(pdb, scans)
    assert n == 1
    assert [p.name for p in scans.glob("page_*")] == ["page_001.jpg"]


def test_extract_skips_non_image_records(make_pdb, tmp_path):
    records = [
        pad_to(make_jpeg(800, 1200), 2000),
        pad_to(b"TEXT metadata record not an image", 2000),
    ]
    pdb = make_pdb(records)
    scans = tmp_path / "scans"
    assert mobi_extract.extract(pdb, scans) == 1


def test_extract_zero_images_raises(make_pdb, tmp_path):
    """File không có ảnh hợp lệ (DRM / không phải manga MOBI) → ValueError."""
    records = [pad_to(b"only text records here", 2000)]
    pdb = make_pdb(records)
    with pytest.raises(ValueError, match="0 ảnh"):
        mobi_extract.extract(pdb, tmp_path / "scans")
