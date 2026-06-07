"""Tests cho HEIC support khi import (`init --from`).

Driver thực tế: `tests/input/Tuyen Tap Aragong` = 119 HEIC + 33 JPG chụp iPhone.
iPhone mặc định HEIC — vision API + pandoc KHÔNG đọc được → phải convert→JPG lúc
import. Xem `src/scan_to_ebook/pipeline.py:_import_images` → `image_ops.convert_heic`.

Convert giờ cross-platform (sips/magick/heif-convert/pillow-heif). Test mock ở lớp
`image_ops` để chạy trên mọi OS (Linux/CI/Windows không có backend thật). Kiểm:
- HEIC route qua converter, output page_NNN.jpg; JPG copy thẳng giữ ext.
- Không backend nào → RuntimeError nêu tên file (KHÔNG silent-skip = mất trang).
- Mixed HEIC/JPG: 1 enumerate → page_NNN tuần tự, đúng natural-sort order.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scan_to_ebook import image_ops, pipeline


def _fake_converter(monkeypatch):
    """Mock 1 backend khả dụng ('sips') + convert_heic viết JPG out (thành công).

    Backend thật macOS-only (sips) hoặc cần cài (magick); mock để test convert path
    chạy trên Linux/CI/Windows."""
    monkeypatch.setattr(image_ops, "available_backends", lambda: ["sips"])

    def fake_convert(src, dst):
        dst.write_bytes(b"\xff\xd8\xff\xe0JPG")  # giả JPG out

    monkeypatch.setattr(image_ops, "convert_heic", fake_convert)


def test_heic_converted_to_jpg(tmp_path, monkeypatch):
    """1 file .HEIC → convert → page_001.jpg (đuôi đổi sang jpg)."""
    _fake_converter(monkeypatch)
    src = tmp_path / "src"
    src.mkdir()
    (src / "IMG_1776.HEIC").write_bytes(b"heic")
    dst = tmp_path / "dst"
    dst.mkdir()
    n = pipeline._import_images(src, dst)
    assert n == 1
    out = sorted(dst.glob("page_*"))
    assert [p.name for p in out] == ["page_001.jpg"]


def test_heif_extension_also_converted(tmp_path, monkeypatch):
    """`.heif` (đuôi anh em của heic) cũng route qua converter → khóa HEIC_SUFFIXES contract."""
    _fake_converter(monkeypatch)
    src = tmp_path / "src"
    src.mkdir()
    (src / "IMG_42.HEIF").write_bytes(b"heif")
    dst = tmp_path / "dst"
    dst.mkdir()
    n = pipeline._import_images(src, dst)
    assert n == 1
    assert [p.name for p in sorted(dst.glob("page_*"))] == ["page_001.jpg"]


def test_jpg_copied_unchanged(tmp_path, monkeypatch):
    """JPG không qua converter — copy thẳng, giữ đuôi .jpg."""
    _fake_converter(monkeypatch)
    src = tmp_path / "src"
    src.mkdir()
    (src / "IMG_2000.JPG").write_bytes(b"\xff\xd8\xff\xe0orig")
    dst = tmp_path / "dst"
    dst.mkdir()
    n = pipeline._import_images(src, dst)
    assert n == 1
    out = sorted(dst.glob("page_*"))
    assert [p.name for p in out] == ["page_001.jpg"]
    # copy thẳng → giữ nội dung gốc (không qua converter).
    assert out[0].read_bytes() == b"\xff\xd8\xff\xe0orig"


def test_mixed_heic_jpg_sequential_numbering(tmp_path, monkeypatch):
    """119/33 thu nhỏ: HEIC + JPG xen kẽ → page_NNN tuần tự theo natural-sort,
    bất kể nguồn. IMG_1776.HEIC < IMG_1902.JPG < IMG_2000.HEIC theo số."""
    _fake_converter(monkeypatch)
    src = tmp_path / "src"
    src.mkdir()
    (src / "IMG_1902.JPG").write_bytes(b"\xff\xd8\xff\xe0jpg")
    (src / "IMG_2000.HEIC").write_bytes(b"heic2")
    (src / "IMG_1776.HEIC").write_bytes(b"heic1")
    dst = tmp_path / "dst"
    dst.mkdir()
    n = pipeline._import_images(src, dst)
    assert n == 3
    out = sorted(dst.glob("page_*"))
    # natural-sort theo số: 1776(heic)→001, 1902(jpg)→002, 2000(heic)→003.
    assert [p.name for p in out] == ["page_001.jpg", "page_002.jpg", "page_003.jpg"]
    # page_002 là JPG copy thẳng → giữ nội dung gốc.
    assert (dst / "page_002.jpg").read_bytes() == b"\xff\xd8\xff\xe0jpg"


def test_heic_without_backend_raises_naming_file(tmp_path, monkeypatch):
    """Không backend nào (Linux/CI/Windows trống) + gặp HEIC → RuntimeError nêu
    tên file, KHÔNG silent-skip. Dùng convert_heic THẬT (chỉ mock backend list)."""
    monkeypatch.setattr(image_ops, "available_backends", lambda: [])
    src = tmp_path / "src"
    src.mkdir()
    (src / "IMG_1776.HEIC").write_bytes(b"heic")
    dst = tmp_path / "dst"
    dst.mkdir()
    with pytest.raises(RuntimeError, match="IMG_1776.HEIC"):
        pipeline._import_images(src, dst)


def test_no_heic_works_without_backend(tmp_path, monkeypatch):
    """Không có HEIC → không cần backend; JPG/PNG import bình thường dù backend vắng."""
    monkeypatch.setattr(image_ops, "available_backends", lambda: [])
    src = tmp_path / "src"
    src.mkdir()
    (src / "page1.jpg").write_bytes(b"\xff\xd8\xff\xe0a")
    (src / "page2.png").write_bytes(b"\x89PNGb")
    dst = tmp_path / "dst"
    dst.mkdir()
    n = pipeline._import_images(src, dst)
    assert n == 2
    out = sorted(dst.glob("page_*"))
    assert [p.name for p in out] == ["page_001.jpg", "page_002.png"]


def test_convert_failure_raises(tmp_path, monkeypatch):
    """Backend khả dụng nhưng convert rc!=0 ở MỌI backend → RuntimeError (không
    tạo trang hỏng âm thầm). Mock backend đơn + converter fail → convert_heic raise."""
    monkeypatch.setattr(image_ops, "available_backends", lambda: ["magick"])
    monkeypatch.setitem(image_ops._CONVERTERS, "magick", lambda src, dst: False)
    src = tmp_path / "src"
    src.mkdir()
    (src / "IMG_9.HEIC").write_bytes(b"heic")
    dst = tmp_path / "dst"
    dst.mkdir()
    with pytest.raises(RuntimeError, match="IMG_9.HEIC"):
        pipeline._import_images(src, dst)
