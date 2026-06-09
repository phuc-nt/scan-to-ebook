"""Tests cho drive_input.fetch_to_scans: tải Drive file/folder → classify → route.

Mock drive_download (download_drive_any, list_drive_folder, is_drive_folder_url)
để khỏi mạng. Kiểm route theo type: image→_import_images, zip→archive_extract,
mobi→mobi_extract, pdf→_import_pdf; folder all-image vs mixed (ValueError).
"""

from __future__ import annotations

import pytest

from scan_to_ebook import drive_input

from conftest import make_jpeg, make_png

_URL_FILE = "https://drive.google.com/file/d/F1/view"
_URL_FOLDER = "https://drive.google.com/drive/folders/FOLDER1"


def _patch_single(monkeypatch, ftype: str, payload: bytes):
    """download_drive_any ghi payload vào dest, trả ftype. is_drive_folder_url False."""
    monkeypatch.setattr(drive_input.drive_download, "is_drive_folder_url", lambda s: False)

    def fake_dl(url, dest):
        dest.write_bytes(payload)
        return ftype

    monkeypatch.setattr(drive_input.drive_download, "download_drive_any", fake_dl)


# ---------------------------------------------------------------- single file routes

def test_single_image_routes_to_import_images(monkeypatch, tmp_path):
    _patch_single(monkeypatch, "jpg", make_jpeg(800, 1200))
    scans = tmp_path / "scans"
    n = drive_input.fetch_to_scans(_URL_FILE, scans)
    assert n == 1
    assert [p.name for p in scans.glob("page_*")] == ["page_001.jpg"]


def test_single_zip_routes_to_archive(monkeypatch, tmp_path, make_cbz):
    cbz = make_cbz({"001.jpg": make_jpeg(800, 1200), "002.jpg": make_jpeg(800, 1200)})
    _patch_single(monkeypatch, "zip", cbz.read_bytes())
    scans = tmp_path / "scans"
    n = drive_input.fetch_to_scans(_URL_FILE, scans)
    assert n == 2


def test_single_pdf_routes_to_import_pdf(monkeypatch, tmp_path):
    _patch_single(monkeypatch, "pdf", b"%PDF-1.7 fake")
    captured = {}

    def fake_import_pdf(pdf, scans_dir):
        captured["pdf"] = pdf
        (scans_dir).mkdir(parents=True, exist_ok=True)
        (scans_dir / "page_001.jpg").write_bytes(make_jpeg(800, 1200))
        return 1

    monkeypatch.setattr(drive_input.pipeline, "_import_pdf", fake_import_pdf)
    n = drive_input.fetch_to_scans(_URL_FILE, tmp_path / "scans")
    assert n == 1
    assert captured["pdf"].suffix == ".pdf"


def test_single_mobi_routes_to_mobi_extract(monkeypatch, tmp_path, make_pdb):
    from conftest import pad_to

    pdb = make_pdb([pad_to(make_jpeg(800, 1200), 2000)])
    _patch_single(monkeypatch, "mobi", pdb.read_bytes())
    n = drive_input.fetch_to_scans(_URL_FILE, tmp_path / "scans")
    assert n == 1


# ----------------------------------------------------------------------- folder routes

def _patch_folder(monkeypatch, children: list[tuple[str, bytes]]):
    """list_drive_folder trả ids; download_drive_any trả (type,payload) theo thứ tự."""
    monkeypatch.setattr(drive_input.drive_download, "is_drive_folder_url", lambda s: True)
    ids = [f"child{i}" for i in range(len(children))]
    monkeypatch.setattr(drive_input.drive_download, "list_drive_folder", lambda s: ids)
    seq = iter(children)

    def fake_dl(url, dest):
        ftype, payload = next(seq)
        dest.write_bytes(payload)
        return ftype

    monkeypatch.setattr(drive_input.drive_download, "download_drive_any", fake_dl)


def test_folder_all_images(monkeypatch, tmp_path):
    _patch_folder(monkeypatch, [
        ("jpg", make_jpeg(800, 1200)),
        ("png", make_png(800, 1200)),
        ("jpg", make_jpeg(800, 1200)),
    ])
    scans = tmp_path / "scans"
    n = drive_input.fetch_to_scans(_URL_FOLDER, scans)
    assert n == 3


def test_folder_image_order_preserved_with_opaque_ids(monkeypatch, tmp_path):
    """REGRESSION (C1): id Drive ngẫu nhiên KHÔNG được quyết định thứ tự trang.

    list_drive_folder trả id mờ, không-sort-được (1Bx, 0Az, 9Qm). Nếu temp file
    đặt tên theo id thì natural-sort của _import_images sẽ xáo trộn. Mỗi ảnh có
    chiều RỘNG riêng (810/820/830) làm vân tay → kiểm page_001..003 đúng thứ tự
    list_drive_folder, KHÔNG theo thứ tự sort của id.
    """
    # id cố tình sort khác thứ tự liệt kê: sorted([..]) = [0Az,1Bx,9Qm] ≠ thứ tự gốc
    opaque_ids = ["1Bx", "0Az", "9Qm"]
    monkeypatch.setattr(drive_input.drive_download, "is_drive_folder_url", lambda s: True)
    monkeypatch.setattr(drive_input.drive_download, "list_drive_folder", lambda s: opaque_ids)
    # Ảnh theo thứ tự liệt kê: width 810, 820, 830 (vân tay nhận diện trang)
    widths = iter([810, 820, 830])
    monkeypatch.setattr(
        drive_input.drive_download, "download_drive_any",
        lambda url, dest: (dest.write_bytes(make_jpeg(next(widths), 1200)), "jpg")[1],
    )
    scans = tmp_path / "scans"
    n = drive_input.fetch_to_scans(_URL_FOLDER, scans)
    assert n == 3
    from scan_to_ebook import epub3_fixed_layout as efl
    pages = sorted(scans.glob("page_*"))
    got_widths = [efl.jpeg_dims(p)[0] for p in pages]
    # Phải khớp thứ tự list_drive_folder (810,820,830), KHÔNG phải sort id (820,810,830)
    assert got_widths == [810, 820, 830]


def test_folder_single_archive(monkeypatch, tmp_path, make_cbz):
    cbz = make_cbz({"001.jpg": make_jpeg(800, 1200), "002.jpg": make_jpeg(800, 1200)})
    _patch_folder(monkeypatch, [("zip", cbz.read_bytes())])
    n = drive_input.fetch_to_scans(_URL_FOLDER, tmp_path / "scans")
    assert n == 2


def test_folder_mixed_raises(monkeypatch, tmp_path, make_cbz):
    """Folder chứa ảnh + archive → ambiguous → ValueError với hướng dẫn."""
    cbz = make_cbz({"001.jpg": make_jpeg(800, 1200)})
    _patch_folder(monkeypatch, [
        ("jpg", make_jpeg(800, 1200)),
        ("zip", cbz.read_bytes()),
    ])
    with pytest.raises(ValueError, match="hỗn hợp"):
        drive_input.fetch_to_scans(_URL_FOLDER, tmp_path / "scans")


def test_folder_cleanup_temp(monkeypatch, tmp_path):
    """Temp dir trong work zone được dọn sau khi xong (không để rác)."""
    _patch_folder(monkeypatch, [("jpg", make_jpeg(800, 1200))])
    scans = tmp_path / "scans"
    drive_input.fetch_to_scans(_URL_FOLDER, scans)
    # Chỉ còn scans/, không có tmp dir lẻ trong parent
    leftovers = [p for p in scans.parent.iterdir() if p.is_dir() and p != scans]
    assert leftovers == []
