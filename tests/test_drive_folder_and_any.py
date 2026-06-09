"""Tests cho Drive folder listing + download_drive_any (manga input path).

Mở rộng test_drive_url.py (PDF-only) sang: file bất kỳ (jpg/zip/mobi/...) +
folder listing. Mạng mock hoàn toàn (monkeypatch opener). Kiểm SSRF: mọi URL
fetch đều có dạng uc?export=download&id= hoặc embeddedfolderview (tái tạo từ ID),
KHÔNG fetch href thô.
"""

from __future__ import annotations

import pytest

from scan_to_ebook import drive_download

from conftest import make_jpeg

_URL_FILE = "https://drive.google.com/file/d/FILE_abc/view"
_URL_FOLDER = "https://drive.google.com/drive/folders/FOLDER_xyz"
_URL_FOLDER_Q = "https://drive.google.com/open?id=FOLDER_q9"


class _FakeResp:
    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _mock_opener(monkeypatch, responses: list[bytes], calls: list[str]):
    seq = iter(responses)

    class _Opener:
        def open(self, req, timeout=None):
            calls.append(req.full_url)
            return _FakeResp(next(seq))

    monkeypatch.setattr(drive_download.urlreq, "build_opener", lambda *a, **k: _Opener())


# ----------------------------------------------------------------------- _detect_type

@pytest.mark.parametrize("data,expected", [
    (b"%PDF-1.7", "pdf"),
    (b"\xff\xd8\xff\xe0", "jpg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"GIF89a....", "gif"),
    (b"PK\x03\x04....", "zip"),
    (b"Rar!\x1a\x07\x00", "rar"),
    (b"<html>sign in</html>", "unknown"),
])
def test_detect_type(data, expected):
    assert drive_download._detect_type(data) == expected


def test_detect_type_mobi():
    # PDB type field "BOOK" ở offset 60:64
    data = b"\x00" * 60 + b"BOOK" + b"rest"
    assert drive_download._detect_type(data) == "mobi"


# ------------------------------------------------------------------ is_drive_folder_url

def test_is_drive_folder_url_folders_form():
    assert drive_download.is_drive_folder_url(_URL_FOLDER) is True


def test_is_drive_folder_url_id_query_form():
    assert drive_download.is_drive_folder_url(_URL_FOLDER_Q) is True


def test_is_drive_folder_url_file_link_false():
    assert drive_download.is_drive_folder_url(_URL_FILE) is False


def test_is_drive_folder_url_non_drive_false():
    assert drive_download.is_drive_folder_url("https://dropbox.com/folder") is False


# --------------------------------------------------------------------- extract_folder_id

def test_extract_folder_id_folders_form():
    assert drive_download.extract_folder_id(_URL_FOLDER) == "FOLDER_xyz"


def test_extract_folder_id_id_query():
    assert drive_download.extract_folder_id(_URL_FOLDER_Q) == "FOLDER_q9"


def test_extract_folder_id_missing_raises():
    with pytest.raises(ValueError, match="folder id"):
        drive_download.extract_folder_id("https://drive.google.com/drive/my-drive")


# ------------------------------------------------------------------ download_drive_any

def test_download_any_jpg(tmp_path, monkeypatch):
    calls: list[str] = []
    _mock_opener(monkeypatch, [make_jpeg(800, 1200)], calls)
    dest = tmp_path / "dl.bin"
    t = drive_download.download_drive_any(_URL_FILE, dest)
    assert t == "jpg"
    assert dest.exists()
    # SSRF: URL fetch là uc?export=download&id= tái tạo từ ID, không phải URL gốc
    assert len(calls) == 1
    assert "uc?export=download&id=FILE_abc" in calls[0]


def test_download_any_zip(tmp_path, monkeypatch):
    _mock_opener(monkeypatch, [b"PK\x03\x04fake-zip-body"], [])
    dest = tmp_path / "dl.bin"
    assert drive_download.download_drive_any(_URL_FILE, dest) == "zip"


def test_download_any_confirm_token_retry(tmp_path, monkeypatch):
    """Interstitial HTML (type unknown) → retry kèm confirm token → ra ảnh."""
    calls: list[str] = []
    html = b"<html><a href='?confirm=TK9&id=x'>download</a></html>"
    _mock_opener(monkeypatch, [html, make_jpeg(800, 1200)], calls)
    t = drive_download.download_drive_any(_URL_FILE, tmp_path / "dl.bin")
    assert t == "jpg"
    assert len(calls) == 2
    assert "confirm=TK9" in calls[1]


def test_download_any_unknown_raises(tmp_path, monkeypatch):
    """Cả 2 lần đều HTML (private/blocked) → ValueError, không ghi file."""
    _mock_opener(monkeypatch, [b"<html>x</html>", b"<html>y</html>"], [])
    dest = tmp_path / "dl.bin"
    with pytest.raises(ValueError, match="không nhận dạng"):
        drive_download.download_drive_any(_URL_FILE, dest)
    assert not dest.exists()


# ------------------------------------------------------------------- list_drive_folder

_FOLDER_HTML = (
    b"<html><body>"
    b"<a href='/file/d/CHILD_1/view'>page1</a>"
    b"<a href='/file/d/CHILD_2/view'>page2</a>"
    b"<a href='/file/d/CHILD_1/view'>dup</a>"  # duplicate → dedupe
    b"</body></html>"
)


def test_list_drive_folder_scrapes_ids(monkeypatch):
    calls: list[str] = []
    _mock_opener(monkeypatch, [_FOLDER_HTML], calls)
    ids = drive_download.list_drive_folder(_URL_FOLDER)
    assert ids == ["CHILD_1", "CHILD_2"]  # dedupe + giữ order
    # SSRF: fetch embeddedfolderview tái tạo từ folder id
    assert "embeddedfolderview?id=FOLDER_xyz" in calls[0]


def test_list_drive_folder_empty_raises(monkeypatch):
    _mock_opener(monkeypatch, [b"<html>no files</html>"], [])
    with pytest.raises(ValueError, match="tải thủ công"):
        drive_download.list_drive_folder(_URL_FOLDER)
