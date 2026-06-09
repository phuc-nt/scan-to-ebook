"""Tests cho Google Drive file-link ingest (`init --from <drive-url>`).

Pipeline mở rộng `init --from` nhận thêm link Drive file (ngoài dir ảnh / PDF
local). Drive → tải PDF về temp trong book-home → render như PDF local → xoá temp.

Mạng bị mock hoàn toàn (monkeypatch opener) → chạy offline trên CI. Kiểm:
- extract_file_id: 3 dạng link + thiếu id.
- is_drive_url: drive thật / non-drive / local path / non-str (Path).
- download: PDF trực tiếp, interstitial confirm-token (assert request lần 2 có
  &confirm=), validate %PDF fail, HTTP error → ValueError.
- cmd_init: URL branch tải + render + dọn temp; local-PDF passthrough (regression
  guard cho việc bỏ type=Path).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from scan_to_ebook import cli, drive_download, pdf_render

_URL_FILE = "https://drive.google.com/file/d/ABC_123-x/view?usp=drivesdk"
_URL_OPEN = "https://drive.google.com/open?id=ID_open99"
_URL_UC = "https://drive.google.com/uc?id=ID_uc77&export=download"


# ----------------------------------------------------------------- extract_file_id

def test_extract_file_id_file_form():
    assert drive_download.extract_file_id(_URL_FILE) == "ABC_123-x"


def test_extract_file_id_open_form():
    assert drive_download.extract_file_id(_URL_OPEN) == "ID_open99"


def test_extract_file_id_uc_form():
    assert drive_download.extract_file_id(_URL_UC) == "ID_uc77"


def test_extract_file_id_missing_raises():
    with pytest.raises(ValueError, match="file id"):
        drive_download.extract_file_id("https://drive.google.com/drive/my-drive")


# -------------------------------------------------------------------- is_drive_url

@pytest.mark.parametrize("url", [_URL_FILE, _URL_OPEN, _URL_UC])
def test_is_drive_url_true(url):
    assert drive_download.is_drive_url(url) is True


def test_is_drive_url_non_drive():
    assert drive_download.is_drive_url("https://dropbox.com/s/x.pdf") is False


def test_is_drive_url_local_path():
    assert drive_download.is_drive_url("/Users/foo/book.pdf") is False


def test_is_drive_url_non_str_path():
    """cmd_init có thể truyền Path (local) — is_drive_url phải False, không raise."""
    assert drive_download.is_drive_url(Path("/tmp/book.pdf")) is False


def test_is_drive_url_malformed_no_throw():
    # urlparse của Python rất khoan dung; assert chỉ là không raise + trả bool.
    assert drive_download.is_drive_url("http://[::1") is False


# -------------------------------------------------------------- download (mocked)

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
    """Monkeypatch build_opener → opener.open trả lần lượt từng response, ghi URL."""
    seq = iter(responses)

    class _Opener:
        def open(self, req, timeout=None):
            calls.append(req.full_url)
            return _FakeResp(next(seq))

    monkeypatch.setattr(drive_download.urlreq, "build_opener", lambda *a, **k: _Opener())


def test_download_direct_pdf(tmp_path, monkeypatch):
    """Response đầu đã là PDF → ghi thẳng, không cần confirm token."""
    calls: list[str] = []
    _mock_opener(monkeypatch, [b"%PDF-1.7 body"], calls)
    dest = tmp_path / "out.pdf"
    drive_download.download_drive_file(_URL_FILE, dest)
    assert dest.read_bytes() == b"%PDF-1.7 body"
    assert len(calls) == 1  # không request lần 2


def test_download_confirm_token_interstitial(tmp_path, monkeypatch):
    """Response đầu = HTML interstitial có confirm=TOKEN → request lần 2 kèm token → PDF."""
    calls: list[str] = []
    html = b"<html><a href='/uc?export=download&confirm=t0k3n&id=x'>Download</a></html>"
    _mock_opener(monkeypatch, [html, b"%PDF-1.7 big"], calls)
    dest = tmp_path / "out.pdf"
    drive_download.download_drive_file(_URL_FILE, dest)
    assert dest.read_bytes() == b"%PDF-1.7 big"
    assert len(calls) == 2
    assert "confirm=t0k3n" in calls[1]


def test_download_virus_scan_form_uses_usercontent_host(tmp_path, monkeypatch):
    """REGRESSION (Pluto): file lớn → trang 'Virus scan warning' với form
    <input name="confirm" value="t"> action=drive.usercontent.google.com.

    Phải: (1) parse confirm token từ FORM field (không phải href), (2) request
    lần 2 qua host usercontent kèm id + confirm. Bug cũ retry trên drive.google
    .com/uc → vẫn trả HTML → file 394MB tải fail. SSRF: URL rebuild từ file-id.
    """
    calls: list[str] = []
    virus_html = (
        b'<html><head><title>Google Drive - Virus scan warning</title></head>'
        b'<body><form id="download-form" '
        b'action="https://drive.usercontent.google.com/download" method="get">'
        b'<input type="hidden" name="id" value="ABC_123-x">'
        b'<input type="hidden" name="export" value="download">'
        b'<input type="hidden" name="confirm" value="t">'
        b'<input type="hidden" name="uuid" value="dead-beef-uuid"></form></body></html>'
    )
    # mobi magic: type field "BOOK" tại offset 60:64
    mobi = b"Pluto_1234" + b"\x00" * 50 + b"BOOK" + b"\x00" * 100
    _mock_opener(monkeypatch, [virus_html, mobi], calls)
    dest = tmp_path / "out.bin"
    t = drive_download.download_drive_any(_URL_FILE, dest)
    assert t == "mobi"
    assert len(calls) == 2
    assert calls[1].startswith("https://drive.usercontent.google.com/download")
    assert "id=ABC_123-x" in calls[1]
    assert "confirm=t" in calls[1]
    # SSRF: id trong URL retry là file-id đã extract, KHÔNG phải href thô từ HTML
    assert "drive.google.com/uc" not in calls[1]


def test_download_not_pdf_raises(tmp_path, monkeypatch):
    """Cả 2 response đều không phải PDF (link private/folder) → ValueError, không ghi file."""
    calls: list[str] = []
    _mock_opener(monkeypatch, [b"<html>Sign in</html>", b"<html>still html</html>"], calls)
    dest = tmp_path / "out.pdf"
    with pytest.raises(ValueError, match="không ra PDF"):
        drive_download.download_drive_file(_URL_FILE, dest)
    assert not dest.exists()


def test_download_http_error_raises(tmp_path, monkeypatch):
    """HTTPError từ opener → ValueError có mã lỗi, không leak HTTPError thô."""
    from urllib import error as urlerr

    class _Opener:
        def open(self, req, timeout=None):
            raise urlerr.HTTPError(req.full_url, 404, "Not Found", {}, None)

    monkeypatch.setattr(drive_download.urlreq, "build_opener", lambda *a, **k: _Opener())
    with pytest.raises(ValueError, match="HTTP 404"):
        drive_download.download_drive_file(_URL_FILE, tmp_path / "out.pdf")


# ------------------------------------------------------------------ cmd_init wiring

def _init_args(slug, from_dir, home):
    return argparse.Namespace(
        slug=slug, from_dir=from_dir, home=home,
        title=None, author=None, lang="vi", year=None,
    )


def _fake_renderer(monkeypatch, n_pages: int = 2):
    monkeypatch.setattr(pdf_render, "available_backends", lambda: ["pdftoppm"])

    def fake_render(pdf, out_dir, dpi=pdf_render.DEFAULT_DPI):
        out = []
        for i in range(1, n_pages + 1):
            p = out_dir / f"{pdf_render._RENDER_PREFIX}-{i:03d}.jpg"
            p.write_bytes(b"\xff\xd8\xff\xe0pg" + str(i).encode())
            out.append(p)
        return out

    monkeypatch.setattr(pdf_render, "render_pdf_to_images", fake_render)


def test_init_from_drive_url_downloads_renders_cleans(tmp_path, monkeypatch, capsys):
    """init --from <drive-url> → tải PDF, render qua _import_pdf, xoá temp _drive_download.pdf."""
    _fake_renderer(monkeypatch, n_pages=2)
    calls: list[str] = []
    _mock_opener(monkeypatch, [b"%PDF-1.7 fake-book"], calls)
    rc = cli.cmd_init(_init_args("mybook", _URL_FILE, tmp_path / "home"))
    assert rc == 0
    book_home = tmp_path / "home" / "mybook"
    scans = book_home / "scans"
    assert [p.name for p in sorted(scans.glob("page_*"))] == ["page_001.jpg", "page_002.jpg"]
    assert "tải PDF từ Google Drive" in capsys.readouterr().out
    # temp đã dọn (không để _drive_download.pdf rác trong book-home).
    assert not (book_home / "_drive_download.pdf").exists()


def test_init_from_drive_url_cleans_temp_on_failure(tmp_path, monkeypatch):
    """Render lỗi sau khi tải → temp vẫn được xoá (try/finally), không để rác."""
    _mock_opener(monkeypatch, [b"%PDF-1.7 fake-book"], [])

    def boom(pdf, dst, dpi=pdf_render.DEFAULT_DPI):
        raise RuntimeError("render failed")

    monkeypatch.setattr(cli, "_import_pdf", boom)
    with pytest.raises(RuntimeError, match="render failed"):
        cli.cmd_init(_init_args("mybook", _URL_FILE, tmp_path / "home"))
    assert not (tmp_path / "home" / "mybook" / "_drive_download.pdf").exists()


def test_init_from_local_pdf_passthrough(tmp_path, monkeypatch, capsys):
    """Regression guard cho bỏ type=Path: local PDF path (str) vẫn route đúng _import_pdf."""
    _fake_renderer(monkeypatch, n_pages=2)
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    rc = cli.cmd_init(_init_args("mybook", str(pdf), tmp_path / "home"))
    assert rc == 0
    scans = tmp_path / "home" / "mybook" / "scans"
    assert [p.name for p in sorted(scans.glob("page_*"))] == ["page_001.jpg", "page_002.jpg"]
    assert "Rendered 2 trang PDF" in capsys.readouterr().out
