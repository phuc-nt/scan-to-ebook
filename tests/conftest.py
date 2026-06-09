"""Shared synthetic-image + container fixtures cho test manga pipeline.

Tất cả tạo bằng bytes thủ công (stdlib only) — KHÔNG tải file thật, KHÔNG dùng
Pillow. Mục tiêu: builder/carver/extractor đọc được magic-byte + parse được dims
(JPEG SOF, PNG IHDR, GIF logical screen descriptor) đúng như production code.
"""

from __future__ import annotations

import struct
import zipfile
from pathlib import Path

import pytest


def make_jpeg(w: int, h: int) -> bytes:
    """JPEG tối thiểu: SOI + SOF0 marker mang đúng (w,h) + EOI.

    Đủ để jpeg_dims (SOF0 0xFFC0) đọc ra (w,h) và img_ext sniff \\xff\\xd8\\xff.
    Không phải JPEG decode-được nhưng đủ cho parse dims structural.
    """
    soi = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    # SOF0: marker(2) len(2)=17 precision(1)=8 height(2) width(2) comps(1)=3 + 3*3
    sof = b"\xff\xc0" + struct.pack(">H", 17) + b"\x08" + struct.pack(">HH", h, w)
    sof += b"\x03" + b"\x01\x11\x00\x02\x11\x01\x03\x11\x01"
    eoi = b"\xff\xd9"
    return soi + sof + eoi


def make_png(w: int, h: int) -> bytes:
    """PNG tối thiểu: signature + IHDR chunk mang (w,h) big-endian ở offset 16."""
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(">II", w, h) + b"\x08\x02\x00\x00\x00"
    # chunk: len(4) + "IHDR" + data + crc(4, giả 0)
    ihdr = struct.pack(">I", len(ihdr_data)) + b"IHDR" + ihdr_data + b"\x00\x00\x00\x00"
    iend = struct.pack(">I", 0) + b"IEND" + b"\x00\x00\x00\x00"
    return sig + ihdr + iend


def make_gif(w: int, h: int) -> bytes:
    """GIF tối thiểu: GIF89a header + logical screen descriptor (w,h) little-endian byte 6."""
    header = b"GIF89a"
    lsd = struct.pack("<HH", w, h) + b"\x00\x00\x00"  # packed/bg/aspect
    trailer = b"\x3b"
    return header + lsd + trailer


def pad_to(data: bytes, n: int) -> bytes:
    """Pad data lên >= n bytes (carver lọc MIN_BYTES=1000 — cần ảnh đủ lớn)."""
    if len(data) >= n:
        return data
    return data + b"\x00" * (n - len(data))


@pytest.fixture
def make_jpeg_fn():
    return make_jpeg


@pytest.fixture
def make_png_fn():
    return make_png


@pytest.fixture
def make_gif_fn():
    return make_gif


@pytest.fixture
def image_scans_dir(tmp_path: Path):
    """Thư mục scans/ với 5 trang (4 portrait + 1 landscape), kiểm cadence RTL.

    page 1 = cover portrait, page 3 = landscape (w>h), còn lại portrait.
    Tất cả >= 400px để qua min_px filter của builder.
    """
    d = tmp_path / "scans"
    d.mkdir()
    dims = [(800, 1200), (800, 1200), (1600, 800), (800, 1200), (800, 1200)]
    for i, (w, h) in enumerate(dims, 1):
        (d / f"page_{i:03d}.jpg").write_bytes(make_jpeg(w, h))
    return d


@pytest.fixture
def make_cbz(tmp_path: Path):
    """Factory: dựng CBZ/ZIP từ dict {arcname: bytes}. Trả Path tới file."""
    def _make(entries: dict[str, bytes], name: str = "test.cbz") -> Path:
        archive = tmp_path / name
        with zipfile.ZipFile(archive, "w") as zf:
            for arcname, data in entries.items():
                zf.writestr(arcname, data)
        return archive
    return _make


@pytest.fixture
def make_pdb(tmp_path: Path):
    """Factory: dựng PDB (MOBI-like) với list record bytes. Trả Path.

    Header layout production parse: numRecords ở byte 76 (>H), offset table 78+i*8
    (>I 4 byte đầu mỗi entry). Ghi record liên tiếp sau bảng offset.
    """
    def _make(records: list[bytes], name: str = "test.mobi") -> Path:
        n = len(records)
        header = bytearray(78 + n * 8)
        struct.pack_into(">H", header, 76, n)
        # data bắt đầu sau bảng offset
        cursor = 78 + n * 8
        offsets = []
        for rec in records:
            offsets.append(cursor)
            cursor += len(rec)
        for i, off in enumerate(offsets):
            struct.pack_into(">I", header, 78 + i * 8, off)
        blob = bytes(header) + b"".join(records)
        p = tmp_path / name
        p.write_bytes(blob)
        return p
    return _make
