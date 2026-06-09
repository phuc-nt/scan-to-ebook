"""Google Drive FILE-link → tải file (stdlib only).

Vì sao tự viết (không gdown / google-api): runtime pure-stdlib, không thêm
dependency. Drive file công khai tải được qua endpoint `uc?export=download&id=<ID>`.
File lớn (vượt ngưỡng quét virus của Drive) trả về 1 trang HTML interstitial kèm
`confirm=<token>` thay vì bytes thật → ta parse token rồi request lại với
`&confirm=<token>`. Cookiejar giữ session cookie để lần request thứ hai hợp lệ.

Chỉ hỗ trợ FILE link và FOLDER link (không URL bất kỳ). Link sai/private/folder
fail to ở `extract_file_id` / `extract_folder_id` hoặc kiểm tra magic bytes.

SSRF safety: mọi request file đều đi qua URL được **tái tạo** từ ID đã extract
(`base = "https://drive.google.com/uc?export=download&id=" + fid`). Không bao
giờ fetch trực tiếp href thô từ HTML — invariant load-bearing, không thay đổi.
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
_FOLDER_RE = re.compile(r"/folders/([\w-]+)")  # /drive/folders/<ID>
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


def is_drive_folder_url(s: str) -> bool:
    """True iff s là Drive folder URL (có /folders/<ID> hoặc ?id= mà KHÔNG có /file/d/).

    Cố ý rộng — tolerant như is_drive_url; xác nhận folder/file thật sẽ fail to ở
    extract_folder_id / extract_file_id.
    """
    if not is_drive_url(s):
        return False
    if _FOLDER_RE.search(s):
        return True
    # ?id= không có /file/d/ cũng coi là folder candidate
    if _ID_RE.search(s):
        return False  # là file link rõ
    qs = urlparse.parse_qs(urlparse.urlparse(s).query)
    return bool(qs.get("id"))


def extract_file_id(url: str) -> str:
    """Lấy <ID> từ /file/d/<ID>/..., ?id=<ID>, hoặc uc?id=<ID>. Raise nếu không có."""
    m = _ID_RE.search(url)
    if m:
        return m.group(1)
    qs = urlparse.parse_qs(urlparse.urlparse(url).query)
    if qs.get("id"):
        return qs["id"][0]
    raise ValueError(f"Không tìm thấy file id trong link Drive: {url}")


def extract_folder_id(url: str) -> str:
    """Lấy <ID> từ /folders/<ID> hoặc ?id=<ID>. Raise ValueError nếu không có."""
    m = _FOLDER_RE.search(url)
    if m:
        return m.group(1)
    qs = urlparse.parse_qs(urlparse.urlparse(url).query)
    if qs.get("id"):
        return qs["id"][0]
    raise ValueError(f"Không tìm thấy folder id trong link Drive: {url}")


def _detect_type(data: bytes) -> str:
    """Magic-byte sniff. Trả 'pdf'|'jpg'|'png'|'gif'|'zip'|'rar'|'mobi'|'unknown'."""
    if data[:4] == b"%PDF":
        return "pdf"
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:4] in (b"GIF8",):
        return "gif"
    if data[:4] == b"PK\x03\x04":
        return "zip"  # covers .cbz, .zip
    if data[:7] == b"Rar!\x1a\x07\x00" or data[:7] == b"Rar!\x1a\x07\x01":
        return "rar"
    # PDB/MOBI: type field tại offset 60:64 là "BOOK" hoặc "TPZ3"
    if len(data) >= 64 and data[60:64] in (b"BOOK", b"TPZ3"):
        return "mobi"
    return "unknown"


def _download_bytes(url: str) -> bytes:
    """Tải URL → bytes. Xử lý interstitial confirm-token của Drive.

    Tái sử dụng chung cho download_drive_file và download_drive_any.
    SSRF: caller phải đảm bảo url đã được tái tạo từ extracted ID — không bao
    giờ truyền URL thô từ HTML vào đây.
    """
    opener = urlreq.build_opener(urlreq.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    data = _fetch(opener, url)
    if data[:4] != b"%PDF" and _detect_type(data) == "unknown":
        # Có thể là interstitial HTML cho file lớn → thử extract confirm token
        token = _confirm_token(data)
        if token:
            data = _fetch(opener, url + "&confirm=" + token)
    elif data[:4] != b"%PDF":
        # Không phải PDF nhưng magic byte đã rõ (ảnh, zip...) → không cần retry
        pass
    else:
        # PDF trực tiếp — xong
        pass
    return data


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


def download_drive_any(url: str, dest: Path) -> str:
    """Tải Drive file bất kỳ → dest. Trả detected type ('pdf'|'jpg'|'png'|...).

    SSRF-safe: URL được tái tạo từ extract_file_id(url), không fetch url gốc thô.
    Raise ValueError nếu type == 'unknown' (link private, bị chặn, hoặc HTML trả về).
    """
    fid = extract_file_id(url)
    base = "https://drive.google.com/uc?export=download&id=" + fid
    opener = urlreq.build_opener(urlreq.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    data = _fetch(opener, base)
    # Thử confirm token nếu chưa rõ type (có thể là interstitial)
    t = _detect_type(data)
    if t == "unknown":
        token = _confirm_token(data)
        if token:
            data = _fetch(opener, base + "&confirm=" + token)
            t = _detect_type(data)
    if t == "unknown":
        raise ValueError(
            f"Tải Drive không nhận dạng được loại file (link private/bị chặn/interstitial "
            f"HTML còn lại). URL gốc: {url}"
        )
    dest.write_bytes(data)
    return t


def list_drive_folder(url: str) -> list[str]:
    """Scrape child file-IDs từ Drive folder. Trả list ID (dedupe, giữ order).

    Dùng embeddedfolderview endpoint (undocumented — fragile, xem Risk trong phase-03).
    Empty / private / endpoint thay đổi → ValueError với hướng dẫn tải thủ công.

    SSRF-safe: URL embeddedfolderview được tái tạo từ extract_folder_id(url).
    """
    fid = extract_folder_id(url)
    embed_url = f"https://drive.google.com/embeddedfolderview?id={fid}#list"
    opener = urlreq.build_opener(urlreq.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    html_bytes = _fetch(opener, embed_url)
    html = html_bytes.decode("utf-8", "ignore")
    # Scrape chỉ lấy file-ID từ pattern /file/d/<ID> — không fetch href thô
    all_ids = _ID_RE.findall(html)
    seen: set[str] = set()
    ids: list[str] = []
    for child_id in all_ids:
        if child_id not in seen:
            seen.add(child_id)
            ids.append(child_id)
    if not ids:
        raise ValueError(
            "folder Drive rỗng, private, hoặc endpoint embeddedfolderview đã đổi — "
            "tải thủ công từng file rồi chạy lại với --from <local-dir>"
        )
    return ids


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
