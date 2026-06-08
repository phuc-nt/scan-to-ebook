"""Cross-platform PDF → page-image render (cho PDF input).

Người dùng có PDF của sách (scan, hoặc born-digital text layer hỏng encoding như
calibre/Quartz xuất) → render từng trang thành JPG rồi cho qua pipeline OCR y như
ảnh scan. KHÔNG trích text layer: PDF born-digital thường có ToUnicode CMap hỏng →
pdftotext ra ký tự rác, trong khi ảnh render đọc tốt. Render→OCR là đường nhất quán
xử lý được CẢ PDF scan lẫn PDF text-hỏng.

Render là chỗ phụ thuộc nền tảng → mirror image_ops.py: dò backend theo thứ tự ưu
tiên rồi dùng cái đầu tiên có:

    1. `pdftoppm`  — poppler (cross-platform: brew/apt/choco). Render chuẩn nhất.
    2. `magick`    — ImageMagick (cần Ghostscript để đọc PDF; cross-platform).
    3. `sips`      — macOS built-in, fallback cuối (chỉ render được PDF 1 trang).

Render BẮT BUỘC thành công (thiếu trang = sách hỏng) → raise RuntimeError với hướng
dẫn cài theo OS nếu KHÔNG backend nào render được. Stdlib-only ở runtime.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
from pathlib import Path

from . import ocr

PDF_SUFFIXES = {".pdf"}

# DPI render mặc định: 150 đủ nét cho OCR text (chữ rõ) mà file không quá to/tốn
# token nếu sau này downscale. Override qua tham số dpi.
DEFAULT_DPI = 150


def _has(binary: str) -> bool:
    return shutil.which(binary) is not None


def available_backends() -> list[str]:
    """Trả list backend render PDF khả dụng theo thứ tự ưu tiên (cho doctor + lỗi)."""
    backends: list[str] = []
    if _has("pdftoppm"):
        backends.append("pdftoppm")
    if _has("magick"):
        backends.append("magick")
    if _has("sips"):
        backends.append("sips")
    return backends


def _install_hint() -> str:
    """Gợi ý cài backend render PDF theo OS hiện tại (cho thông báo lỗi)."""
    system = platform.system()
    if system == "Windows":
        return (
            "Windows: cài poppler (https://github.com/oschwartz10612/poppler-windows, "
            "thêm bin/ vào PATH) HOẶC ImageMagick + Ghostscript."
        )
    if system == "Linux":
        return "Linux: `sudo apt install poppler-utils` HOẶC `sudo apt install imagemagick ghostscript`."
    return "macOS: `brew install poppler` (pdftoppm) HOẶC `brew install imagemagick ghostscript`."


# --------------------------------------------------------------- render backends
# Mỗi backend render TOÀN BỘ pdf vào out_dir với prefix cố định rồi caller gom +
# rename page_NNN. Trả True nếu sinh ≥1 ảnh.

_RENDER_PREFIX = "_pdfpage"


def _rendered_jpgs(out_dir: Path) -> list[Path]:
    """List JPG do backend sinh ra (prefix cố định), natural-sort theo số trang.

    Dùng ocr.natural_sort_key (template chung của codebase) thay vì sort chuỗi:
    magick dùng %03d nên >999 trang ra `-1000.jpg` mà sort lexical xếp trước
    `-999.jpg` → đảo trang (= sách hỏng). Natural-sort so theo GIÁ TRỊ số nên đúng
    thứ tự bất kể độ rộng zero-pad của backend."""
    return sorted(out_dir.glob(f"{_RENDER_PREFIX}*.jpg"), key=ocr.natural_sort_key)


def _render_pdftoppm(pdf: Path, out_dir: Path, dpi: int) -> bool:
    # pdftoppm -jpeg -r DPI <pdf> <out_dir>/<prefix> → <prefix>-NNN.jpg
    result = subprocess.run(
        ["pdftoppm", "-jpeg", "-r", str(dpi), str(pdf), str(out_dir / _RENDER_PREFIX)],
        capture_output=True, text=True,
    )
    return result.returncode == 0 and bool(_rendered_jpgs(out_dir))


def _render_magick(pdf: Path, out_dir: Path, dpi: int) -> bool:
    # -density trước input để đặt DPI raster hoá; %03d → zero-pad 3 (đủ cho <1000 trang).
    out_pattern = str(out_dir / f"{_RENDER_PREFIX}-%03d.jpg")
    result = subprocess.run(
        ["magick", "-density", str(dpi), str(pdf), "-quality", "92", out_pattern],
        capture_output=True, text=True,
    )
    return result.returncode == 0 and bool(_rendered_jpgs(out_dir))


def _render_sips(pdf: Path, out_dir: Path, dpi: int) -> bool:
    # sips KHÔNG render multi-page PDF → image hàng loạt được (chỉ trang đầu / metadata).
    # Giữ làm fallback CHỈ cho PDF 1 trang; đa số sách >1 trang nên backend này hiếm
    # khi đủ. Trả True chỉ khi thực sự sinh ảnh.
    dst = out_dir / f"{_RENDER_PREFIX}-001.jpg"
    result = subprocess.run(
        ["sips", "-s", "format", "jpeg", str(pdf), "--out", str(dst)],
        capture_output=True, text=True,
    )
    return result.returncode == 0 and dst.exists()


_RENDERERS = {
    "pdftoppm": _render_pdftoppm,
    "magick": _render_magick,
    "sips": _render_sips,
}


def render_pdf_to_images(pdf: Path, out_dir: Path, dpi: int = DEFAULT_DPI) -> list[Path]:
    """Render mọi trang PDF → JPG trong out_dir, trả list path đã sort theo trang.

    Thử lần lượt pdftoppm→magick→sips; backend đầu sinh được ảnh thì dừng. KHÔNG
    backend nào được → raise RuntimeError với hướng dẫn cài theo OS (KHÔNG silent:
    thiếu trang = sách hỏng). Caller (import) tự rename các path trả về thành
    page_NNN. out_dir phải tồn tại."""
    backends = available_backends()
    if not backends:
        raise RuntimeError(
            f"không có công cụ render PDF nào (cần cho {pdf.name}). {_install_hint()}"
        )
    errors: list[str] = []
    for name in backends:
        # Dọn ảnh prefix cũ trước mỗi lần thử (tránh trộn output 2 backend).
        for stale in _rendered_jpgs(out_dir):
            stale.unlink(missing_ok=True)
        try:
            if _RENDERERS[name](pdf, out_dir, dpi):
                return _rendered_jpgs(out_dir)
            errors.append(f"{name}: không sinh ảnh")
        except (OSError, subprocess.SubprocessError) as exc:
            errors.append(f"{name}: {exc}")
    raise RuntimeError(
        f"render PDF thất bại cho {pdf.name} (đã thử {', '.join(backends)}): "
        f"{'; '.join(errors)}. {_install_hint()}"
    )
