"""Carve page images từ file .mobi/.azw3 (Palm Database / PDB format).

Manga MOBI/AZW3 lưu mỗi trang thành 1 PDB record (JPEG/PNG/GIF). Module này
parse bảng offset PDB, carve từng record, lọc theo magic-byte + kích thước byte
rồi ghi ra scans/page_NNN.<ext> — y giao diện normalize_input muốn.

DRM-free .mobi/.azw3 ONLY. AZW3 mã hoá (DRM) → record bytes là cipher-text →
img_ext trả None → 0 ảnh hợp lệ → raise ValueError (rõ thay vì sách trống).
Lọc kích thước pixel (thumbnail drop) nằm ở builder Phase 04 (single source of
truth cho min_px) — ở đây chỉ lọc theo byte size (rẻ, không cần decode ảnh).
"""

from __future__ import annotations

import struct
from pathlib import Path

# Byte threshold: record < MIN_BYTES hầu hết là thumbnail/cover nhỏ hoặc metadata.
# Không phải dimension filter — builder lo phần đó.
MIN_BYTES = 1000


def pdb_records(data: bytes) -> list[bytes]:
    """Parse PDB record-offset table, trả list byte-slice của mỗi record.

    PDB header layout (big-endian):
      offset 0-31  : name (32 bytes)
      offset 76    : numRecords (uint16)
      offset 78+   : record list, mỗi entry 8 bytes, 4 byte đầu là offset (uint32)

    Bounds guards: hỏng / file không phải PDB → raise ValueError thay vì IndexError.
    """
    if len(data) < 78:
        raise ValueError("not a valid PDB/MOBI: file too short")

    num = struct.unpack(">H", data[76:78])[0]

    # Đọc offset của từng record + sentinel EOF
    min_table_end = 78 + num * 8
    if len(data) < min_table_end:
        raise ValueError(
            f"not a valid PDB/MOBI: offset table needs {min_table_end} bytes, "
            f"file only {len(data)}"
        )

    offsets: list[int] = []
    for i in range(num):
        base = 78 + i * 8
        off = struct.unpack(">I", data[base : base + 4])[0]
        if off > len(data):
            raise ValueError(
                f"corrupt PDB offset table: record {i} offset {off} > file length {len(data)}"
            )
        offsets.append(off)
    offsets.append(len(data))  # sentinel: end của record cuối = EOF

    recs: list[bytes] = []
    for i in range(num):
        recs.append(data[offsets[i] : offsets[i + 1]])
    return recs


def img_ext(b: bytes) -> str | None:
    """Magic-byte sniff: trả 'jpg'/'png'/'gif' hoặc None nếu không phải ảnh."""
    if b[:3] == b"\xff\xd8\xff":
        return "jpg"
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if b[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    return None


def extract(src: Path, scans_dir: Path) -> int:
    """Carve page images từ MOBI/AZW3 → scans_dir/page_NNN.<ext>. Trả số trang.

    Lọc: img_ext không None VÀ len(record) > MIN_BYTES. Thứ tự = record order
    (= thứ tự đọc trong manga MOBI). Raise ValueError nếu 0 ảnh hợp lệ (file
    corrupt, không phải manga MOBI, hoặc AZW3 có DRM).
    """
    data = src.read_bytes()
    recs = pdb_records(data)

    scans_dir.mkdir(parents=True, exist_ok=True)

    count = 0
    for rec in recs:
        ext = img_ext(rec)
        if ext is None or len(rec) <= MIN_BYTES:
            continue
        count += 1
        out = scans_dir / f"page_{count:03d}.{ext}"
        out.write_bytes(rec)

    if count == 0:
        raise ValueError(
            f"0 ảnh hợp lệ từ {src.name} — file không phải manga MOBI, "
            "corrupt, hoặc AZW3 có DRM (cần file DRM-free)"
        )
    return count
