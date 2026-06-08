"""Google Drive FILE-link → temp PDF download (stdlib only).

Vì sao tự viết (không gdown / google-api): runtime pure-stdlib, không thêm
dependency. Drive file công khai tải được qua endpoint `uc?export=download&id=<ID>`.
File lớn (vượt ngưỡng quét virus của Drive) trả về 1 trang HTML interstitial kèm
`confirm=<token>` thay vì bytes PDF → ta parse token rồi request lại với
`&confirm=<token>`. Cookiejar giữ session cookie để lần request thứ hai hợp lệ.

Chỉ hỗ trợ FILE link (không folder, không URL bất kỳ). Link sai/private/folder
fail to ở `extract_file_id` hoặc kiểm tra magic bytes `%PDF` với thông báo rõ.
"""

from __future__ import annotations

import http.cookiejar
import re
from pathlib import Path
from urllib import error as urlerr
from urllib import parse as urlparse
from urllib import request as urlreq

_DRIVE_HOST = "drive.google.com"
_ID_RE = re.compile(r"/file/d/([\w-]+)")  # /file/d/<ID>/view
_CONFIRM_RE = re.compile(r"confirm=([\w-]+)")  # interstitial form action / href
_TIMEOUT = 300


def is_drive_url(s: str) -> bool:
    """True iff s là URL http(s) trỏ tới drive.google.com (file link xử lý sau).

    Cố ý rộng (mọi host drive.google.com); link không-phải-file fail to ở
    extract_file_id/%PDF thay vì khớp path mong manh — KISS.
    """
    if not isinstance(s, str):  # cmd_init có thể nhận Path (local) — chỉ str mới là URL
        return False
    try:
        u = urlparse.urlparse(s)
    except ValueError:
        return False
    return u.scheme in ("http", "https") and u.netloc.endswith(_DRIVE_HOST)


def extract_file_id(url: str) -> str:
    """Lấy <ID> từ /file/d/<ID>/..., ?id=<ID>, hoặc uc?id=<ID>. Raise nếu không có."""
    m = _ID_RE.search(url)
    if m:
        return m.group(1)
    qs = urlparse.parse_qs(urlparse.urlparse(url).query)
    if qs.get("id"):
        return qs["id"][0]
    raise ValueError(f"Không tìm thấy file id trong link Drive: {url}")


def download_drive_file(url: str, dest: Path) -> None:
    """Tải Drive file → dest. Xử lý interstitial file lớn; kiểm tra magic bytes %PDF.

    Ghi đè dest. Toàn bộ bytes vào RAM rồi ghi 1 lần (chấp nhận với PDF sách
    ~100MB, khớp style repo). Raise ValueError nếu không ra PDF.
    """
    fid = extract_file_id(url)
    base = "https://drive.google.com/uc?export=download&id=" + fid
    opener = urlreq.build_opener(urlreq.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    data = _fetch(opener, base)
    if data[:4] != b"%PDF":
        token = _confirm_token(data)  # parse trang interstitial HTML
        if token:
            data = _fetch(opener, base + "&confirm=" + token)
    if data[:4] != b"%PDF":
        raise ValueError(
            "Tải Drive không ra PDF (link không public, là folder, hoặc bị chặn?)"
        )
    dest.write_bytes(data)


def _fetch(opener: urlreq.OpenerDirector, url: str) -> bytes:
    req = urlreq.Request(url, headers={"User-Agent": "scan2ebook"})
    try:
        with opener.open(req, timeout=_TIMEOUT) as resp:
            return resp.read()
    except urlerr.HTTPError as exc:
        raise ValueError(f"Drive trả lỗi HTTP {exc.code} cho {url}") from exc


def _confirm_token(html: bytes) -> str | None:
    m = _CONFIRM_RE.search(html.decode("utf-8", "ignore"))
    return m.group(1) if m else None
