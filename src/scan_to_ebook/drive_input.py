"""Drive input adapter: single Drive file OR Drive folder → scans/page_NNN.<ext>.

Điểm vào duy nhất: fetch_to_scans(src, scans_dir) → int (số trang).
Được gọi từ manga_pipeline.normalize_input khi is_drive_url(src) là True.

Hai nhánh:
  1. Folder URL → list_drive_folder → tải từng child → phân loại → route adapter.
  2. Single file URL → download_drive_any → route adapter theo type.

SSRF safety: mọi fetch file đều đi qua drive_download._download_bytes với URL
tái tạo từ ID. Module này không tự fetch URL thô — delegate hoàn toàn sang
drive_download (chokepoint SSRF nằm ở đó).
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from . import drive_download, pipeline

# Đuôi extension theo type string trả về từ drive_download._detect_type
_TYPE_TO_EXT = {
    "pdf": ".pdf",
    "jpg": ".jpg",
    "png": ".png",
    "gif": ".gif",
    "zip": ".zip",
    "rar": ".rar",
    "mobi": ".mobi",
}

_IMAGE_TYPES = {"jpg", "png", "gif"}
_ARCHIVE_TYPES = {"zip", "rar"}


def _route_single(ftype: str, fpath: Path, scans_dir: Path) -> int:
    """Route 1 file đã tải theo detected type → adapter tương ứng. Trả số trang."""
    if ftype in _IMAGE_TYPES:
        # _import_images glob thư mục — đặt file vào tmpdir riêng rồi gọi
        return pipeline._import_images(fpath.parent, scans_dir)
    if ftype in _ARCHIVE_TYPES:
        from . import archive_extract
        return archive_extract.extract(fpath, scans_dir)
    if ftype == "mobi":
        from . import mobi_extract
        return mobi_extract.extract(fpath, scans_dir)
    if ftype == "pdf":
        return pipeline._import_pdf(fpath, scans_dir)
    # Không đến đây nếu download_drive_any đã raise khi type == "unknown"
    raise ValueError(f"Loại file không hỗ trợ: {ftype}")


def fetch_to_scans(src: str, scans_dir: Path) -> int:
    """Tải Drive file/folder → scans_dir/page_NNN.<ext>. Trả số trang.

    Đây là entry point duy nhất — được gọi từ manga_pipeline.normalize_input.
    Cleanup temp dir bằng try/finally (mirror cli.py pattern).
    """
    scans_dir.mkdir(parents=True, exist_ok=True)
    # Tạo tmp trong cùng parent với scans_dir (work zone) để tránh cross-device move.
    tmp = Path(tempfile.mkdtemp(dir=scans_dir.parent))
    try:
        if drive_download.is_drive_folder_url(src):
            return _fetch_folder(src, tmp, scans_dir)
        else:
            return _fetch_single_file(src, tmp, scans_dir)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _fetch_single_file(src: str, tmp: Path, scans_dir: Path) -> int:
    """Tải 1 Drive file → classify → route. tmp là thư mục temp đã tạo."""
    dest = tmp / "dl.bin"
    ftype = drive_download.download_drive_any(src, dest)
    # Đổi tên với extension đúng để downstream adapter (glob suffix, zipfile, etc.) nhận ra
    ext = _TYPE_TO_EXT.get(ftype, ".bin")
    named = dest.with_suffix(ext)
    dest.rename(named)
    return _route_single(ftype, named, scans_dir)


def _fetch_folder(src: str, tmp: Path, scans_dir: Path) -> int:
    """Tải toàn bộ children của Drive folder → classify → route. tmp là temp dir."""
    child_ids = drive_download.list_drive_folder(src)  # raise nếu folder rỗng

    # Tải từng child → tmp/dl_NNNN.<ext>. QUAN TRỌNG: đặt tên theo CHỈ SỐ liệt kê,
    # KHÔNG theo file-id. Drive file-id là chuỗi ngẫu nhiên — nếu đặt tên theo id
    # thì nhánh all-image gọi _import_images (natural-sort theo tên) sẽ xáo trộn
    # thứ tự trang. dl_0001, dl_0002... giữ đúng thứ tự liệt kê của folder.
    typed_files: list[tuple[str, Path]] = []
    for idx, cid in enumerate(child_ids, start=1):
        uc_url = "https://drive.google.com/uc?export=download&id=" + cid
        dest = tmp / f"dl_{idx:04d}.bin"
        ftype = drive_download.download_drive_any(uc_url, dest)
        ext = _TYPE_TO_EXT.get(ftype, ".bin")
        named = dest.with_suffix(ext)
        dest.rename(named)
        typed_files.append((ftype, named))

    # Phân loại nội dung folder
    types = {t for t, _ in typed_files}
    files_by_type: dict[str, list[Path]] = {}
    for t, p in typed_files:
        files_by_type.setdefault(t, []).append(p)

    # Tất cả là ảnh → import như thư mục ảnh
    if types <= _IMAGE_TYPES:
        return pipeline._import_images(tmp, scans_dir)

    # Đúng 1 archive
    archive_files = [p for t, p in typed_files if t in _ARCHIVE_TYPES]
    if len(archive_files) == 1 and len(typed_files) == 1:
        from . import archive_extract
        return archive_extract.extract(archive_files[0], scans_dir)

    # Đúng 1 mobi
    mobi_files = [p for t, p in typed_files if t == "mobi"]
    if len(mobi_files) == 1 and len(typed_files) == 1:
        from . import mobi_extract
        return mobi_extract.extract(mobi_files[0], scans_dir)

    # Đúng 1 PDF
    pdf_files = [p for t, p in typed_files if t == "pdf"]
    if len(pdf_files) == 1 and len(typed_files) == 1:
        return pipeline._import_pdf(pdf_files[0], scans_dir)

    # Mixed hoặc ambiguous
    summary = ", ".join(f"{t}×{len(ps)}" for t, ps in sorted(files_by_type.items()))
    raise ValueError(
        f"folder Drive chứa nội dung hỗn hợp ({summary}) — không thể tự xác định "
        "định dạng. Tải thủ công và chạy lại với --from <local-file-or-dir>"
    )
