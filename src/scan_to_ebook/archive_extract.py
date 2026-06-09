"""Giải nén CBZ/ZIP/CBR → scans/page_NNN.<ext>.

CBZ/ZIP: stdlib zipfile (không cần tool ngoài).
CBR: shell-out unar (preferred) hoặc unrar — probe theo thứ tự, raise rõ nếu cả
hai vắng (kèm hướng dẫn cài theo OS).

Lọc: chỉ lấy file ảnh, bỏ qua __MACOSX/ và dotfile. Sort natural để 1.jpg,
2.jpg, 10.jpg ra đúng thứ tự (lexical sort xếp sai: "10" < "2"). HEIC/HEIF
trong archive → convert via image_ops.convert_heic (rare nhưng cần xử lý).

Zip-slip guard: validate mỗi member resolve trong tmp trước khi extract
(defense-in-depth; Python 3.12+ có built-in guard nhưng explicit check tốt hơn
cho cross-version và rõ ý định security).
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from . import image_ops, ocr

ARCHIVE_SUFFIXES = {".cbz", ".zip", ".cbr"}

# Đuôi ảnh nhận ra trong archive (lowercase).
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".heic", ".heif"}
_HEIC_EXTS = {".heic", ".heif"}


def _has(binary: str) -> bool:
    return shutil.which(binary) is not None


def available_rar_backends() -> list[str]:
    """Probe unar/unrar trên PATH. Trả list theo thứ tự ưu tiên (unar trước)."""
    backends: list[str] = []
    if _has("unar"):
        backends.append("unar")
    if _has("unrar"):
        backends.append("unrar")
    return backends


def _install_hint() -> str:
    """Gợi ý cài unar/unrar theo OS (cho thông báo lỗi CBR)."""
    system = platform.system()
    if system == "Windows":
        return (
            "Windows: cài unar từ https://theunarchiver.com/command-line HOẶC "
            "WinRAR (unrar.exe phải có trong PATH)."
        )
    if system == "Linux":
        return "Linux: `sudo apt install unar` HOẶC `sudo apt install unrar`."
    return "macOS: `brew install unar` (khuyên dùng) HOẶC `brew install unrar`."


def _is_image_name(name: str) -> bool:
    """Nhận file ảnh theo đuôi. Bỏ qua __MACOSX/ metadata và dotfile."""
    # Bỏ qua macOS metadata entry và hidden file (thumbnail, .DS_Store, v.v.)
    parts = Path(name).parts
    if any(p == "__MACOSX" for p in parts):
        return False
    basename = Path(name).name
    if basename.startswith("."):
        return False
    return Path(name).suffix.lower() in _IMAGE_EXTS


def _extract_zip(src: Path, tmp: Path) -> None:
    """Giải nén ZIP/CBZ vào tmp. Zip-slip guard: validate từng member."""
    tmp_resolved = tmp.resolve()
    with zipfile.ZipFile(src, "r") as zf:
        for member in zf.infolist():
            # Normalise separator (ZIP spec dùng '/') rồi resolve path dự kiến.
            safe_name = member.filename.replace("\\", "/")
            target = (tmp / safe_name).resolve()
            # Zip-slip: member phải nằm trong tmp.
            if not str(target).startswith(str(tmp_resolved) + os.sep) and target != tmp_resolved:
                raise ValueError(
                    f"zip-slip detected: member '{member.filename}' would extract "
                    f"outside target directory"
                )
            zf.extract(member, tmp)


def _extract_rar(src: Path, tmp: Path) -> None:
    """Shell-out unar hoặc unrar để giải nén CBR. Raise RuntimeError nếu vắng cả hai."""
    backends = available_rar_backends()
    if not backends:
        raise RuntimeError(
            f"không có công cụ giải nén RAR/CBR nào. {_install_hint()}"
        )
    backend = backends[0]
    if backend == "unar":
        cmd = ["unar", "-force-overwrite", "-output-directory", str(tmp), str(src)]
    else:
        # unrar x -o+ <src> <dst>/ — trailing slash quan trọng với một số build unrar
        cmd = ["unrar", "x", "-o+", str(src), str(tmp) + "/"]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"{backend} thất bại (exit {result.returncode}): {result.stderr.strip()}"
        )


def extract(src: Path, scans_dir: Path) -> int:
    """Giải nén archive → scans_dir/page_NNN.<ext>. Trả số trang.

    CBZ/ZIP: stdlib zipfile. CBR: unar|unrar shell-out.
    Raise ValueError nếu 0 entry ảnh (archive rỗng / sai format).
    Raise RuntimeError nếu CBR nhưng không có unar/unrar.
    """
    scans_dir.mkdir(parents=True, exist_ok=True)
    suffix = src.suffix.lower()

    # Tạo tmp trong parent của scans_dir (work zone) để tránh cross-device move.
    tmp = Path(tempfile.mkdtemp(dir=scans_dir.parent))
    try:
        if suffix == ".cbr":
            _extract_rar(src, tmp)
        else:
            _extract_zip(src, tmp)

        # Recursive walk — CBR/CBZ đôi khi chứa folder con.
        all_entries: list[Path] = [
            p for p in tmp.rglob("*")
            if p.is_file() and _is_image_name(str(p.relative_to(tmp)))
        ]

        if not all_entries:
            raise ValueError(
                f"0 ảnh trong archive {src.name} — archive rỗng hoặc không phải CBZ/CBR ảnh"
            )

        # Natural sort: 1.jpg, 2.jpg, 10.jpg — không dùng lexical sort.
        all_entries.sort(key=lambda p: (ocr.natural_sort_key(p), p.suffix.lower()))

        for i, entry in enumerate(all_entries, start=1):
            ext = entry.suffix.lower()
            if ext in _HEIC_EXTS:
                # HEIC/HEIF: convert sang JPG (vision API + pandoc không đọc được HEIC).
                dst = scans_dir / f"page_{i:03d}.jpg"
                image_ops.convert_heic(entry, dst)
            else:
                dst = scans_dir / f"page_{i:03d}{ext}"
                shutil.copy2(entry, dst)

        return len(all_entries)

    finally:
        # Dọn tmp kể cả khi lỗi — tránh để lại file rác trong work zone.
        shutil.rmtree(tmp, ignore_errors=True)
