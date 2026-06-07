"""Tests cho HEIC support khi import (`init --from`).

Driver thực tế: `tests/input/Tuyen Tap Aragong` = 119 HEIC + 33 JPG chụp iPhone.
iPhone mặc định HEIC — vision API + pandoc KHÔNG đọc được → phải convert→JPG lúc
import. Xem `src/scan_to_ebook/pipeline.py:_import_images` + `_convert_heic`.

Chiến lược test: mock `subprocess.run` (sips) + `shutil.which` để chạy được trên
Linux/CI (không có sips thật). Kiểm:
- HEIC route qua sips, output page_NNN.jpg; JPG copy thẳng giữ ext.
- Thiếu sips → RuntimeError nêu tên file (KHÔNG silent-skip = mất trang).
- Mixed HEIC/JPG: 1 enumerate → page_NNN tuần tự, đúng natural-sort order.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scan_to_ebook import pipeline


def _fake_sips(monkeypatch):
    """Mock shutil.which('sips')→path + subprocess.run viết JPG out (rc=0).

    sips là macOS-only; mock để test convert path chạy trên Linux/CI."""
    monkeypatch.setattr(pipeline.shutil, "which", lambda name: "/usr/bin/sips")

    def fake_run(cmd, *a, **k):
        # cmd = ["sips","-s","format","jpeg", src, "--out", dst]
        out = Path(cmd[cmd.index("--out") + 1])
        out.write_bytes(b"\xff\xd8\xff\xe0JPG")  # giả JPG out
        return type("R", (), {"returncode": 0, "stderr": "", "stdout": ""})()

    monkeypatch.setattr(pipeline.subprocess, "run", fake_run)


def test_heic_converted_to_jpg(tmp_path, monkeypatch):
    """1 file .HEIC → sips → page_001.jpg (đuôi đổi sang jpg)."""
    _fake_sips(monkeypatch)
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
    """`.heif` (đuôi anh em của heic) cũng route qua sips → khóa _HEIC_SUFFIXES contract."""
    _fake_sips(monkeypatch)
    src = tmp_path / "src"
    src.mkdir()
    (src / "IMG_42.HEIF").write_bytes(b"heif")
    dst = tmp_path / "dst"
    dst.mkdir()
    n = pipeline._import_images(src, dst)
    assert n == 1
    assert [p.name for p in sorted(dst.glob("page_*"))] == ["page_001.jpg"]


def test_jpg_copied_unchanged(tmp_path, monkeypatch):
    """JPG không qua sips — copy thẳng, giữ đuôi .jpg."""
    _fake_sips(monkeypatch)
    src = tmp_path / "src"
    src.mkdir()
    (src / "IMG_2000.JPG").write_bytes(b"\xff\xd8\xff\xe0orig")
    dst = tmp_path / "dst"
    dst.mkdir()
    n = pipeline._import_images(src, dst)
    assert n == 1
    out = sorted(dst.glob("page_*"))
    assert [p.name for p in out] == ["page_001.jpg"]
    # copy thẳng → giữ nội dung gốc (không qua sips).
    assert out[0].read_bytes() == b"\xff\xd8\xff\xe0orig"


def test_mixed_heic_jpg_sequential_numbering(tmp_path, monkeypatch):
    """119/33 thu nhỏ: HEIC + JPG xen kẽ → page_NNN tuần tự theo natural-sort,
    bất kể nguồn. IMG_1776.HEIC < IMG_1902.JPG < IMG_2000.HEIC theo số."""
    _fake_sips(monkeypatch)
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


def test_heic_without_sips_raises_naming_file(tmp_path, monkeypatch):
    """Thiếu sips (Linux/CI) + gặp HEIC → RuntimeError nêu tên file, KHÔNG silent-skip."""
    monkeypatch.setattr(pipeline.shutil, "which", lambda name: None)
    src = tmp_path / "src"
    src.mkdir()
    (src / "IMG_1776.HEIC").write_bytes(b"heic")
    dst = tmp_path / "dst"
    dst.mkdir()
    with pytest.raises(RuntimeError, match="IMG_1776.HEIC"):
        pipeline._import_images(src, dst)


def test_no_heic_works_without_sips(tmp_path, monkeypatch):
    """Không có HEIC → không cần sips; JPG/PNG import bình thường dù sips vắng."""
    monkeypatch.setattr(pipeline.shutil, "which", lambda name: None)
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


def test_sips_failure_raises(tmp_path, monkeypatch):
    """sips chạy nhưng rc!=0 → RuntimeError (không tạo trang hỏng âm thầm)."""
    monkeypatch.setattr(pipeline.shutil, "which", lambda name: "/usr/bin/sips")

    def fail_run(cmd, *a, **k):
        return type("R", (), {"returncode": 1, "stderr": "boom", "stdout": ""})()

    monkeypatch.setattr(pipeline.subprocess, "run", fail_run)
    src = tmp_path / "src"
    src.mkdir()
    (src / "IMG_9.HEIC").write_bytes(b"heic")
    dst = tmp_path / "dst"
    dst.mkdir()
    with pytest.raises(RuntimeError, match="IMG_9.HEIC"):
        pipeline._import_images(src, dst)
