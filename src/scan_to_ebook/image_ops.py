"""Cross-platform image ops: HEIC/HEIF → JPG convert + downscale.

Tách riêng vì đây là chỗ DUY NHẤT phụ thuộc nền tảng. Trước đây hardcode macOS
`sips` → Windows/Linux không chạy. Module này dò backend khả dụng theo thứ tự ưu
tiên rồi dùng cái đầu tiên có:

    1. `sips`         — macOS built-in (không cần cài gì, mặc định trên Mac).
    2. `magick`       — ImageMagick (cross-platform: Windows .msi / brew / apt).
    3. `heif-convert` — libheif-tools (Linux: apt install libheif-examples).
    4. pillow-heif    — Python lib (lazy import; fallback cuối, cần `pip install`).

Mỗi backend làm được CẢ hai việc (convert HEIC, downscale JPG khác cũng được).
Convert HEIC bắt buộc thành công (mất trang = sách hỏng) → raise RuntimeError với
hướng dẫn cài đặt theo OS nếu KHÔNG backend nào xử lý được. Downscale chỉ là tối
ưu cost → fail thì caller fallback dùng ảnh gốc (không raise).

Stdlib-only ở runtime mặc định (pillow-heif chỉ import khi thật sự cần + đã cài).
"""

from __future__ import annotations

import platform
import shutil
import subprocess
from pathlib import Path

# Đuôi cần convert sang JPG trước khi OCR (vision API + pandoc không đọc HEIC/HEIF).
HEIC_SUFFIXES = {".heic", ".heif"}


def _has(binary: str) -> bool:
    return shutil.which(binary) is not None


def available_backends() -> list[str]:
    """Trả list backend khả dụng theo thứ tự ưu tiên (cho doctor + thông báo lỗi)."""
    backends: list[str] = []
    if _has("sips"):
        backends.append("sips")
    if _has("magick"):
        backends.append("magick")
    if _has("heif-convert"):
        backends.append("heif-convert")
    if _pillow_heif_available():
        backends.append("pillow-heif")
    return backends


def _pillow_heif_available() -> bool:
    """pillow-heif + PIL import được không (lazy — KHÔNG là dep cứng)."""
    try:
        import pillow_heif  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError:
        return False
    return True


def _install_hint() -> str:
    """Gợi ý cài backend theo OS hiện tại (cho thông báo lỗi convert)."""
    system = platform.system()
    if system == "Windows":
        return (
            "Windows: cài ImageMagick (https://imagemagick.org, tick 'Install legacy "
            "utilities') HOẶC `pip install pillow-heif`. iPhone HEIC cũng cần HEIF "
            "Extensions từ Microsoft Store."
        )
    if system == "Linux":
        return (
            "Linux: `sudo apt install imagemagick libheif-examples` HOẶC "
            "`pip install pillow-heif`."
        )
    return "macOS: `sips` có sẵn — nếu thiếu, `brew install imagemagick`."


# ----------------------------------------------------------------- convert HEIC

def _convert_sips(src: Path, dst: Path) -> bool:
    result = subprocess.run(
        ["sips", "-s", "format", "jpeg", str(src), "--out", str(dst)],
        capture_output=True, text=True,
    )
    return result.returncode == 0 and dst.exists()


def _convert_magick(src: Path, dst: Path) -> bool:
    result = subprocess.run(
        ["magick", str(src), str(dst)], capture_output=True, text=True,
    )
    return result.returncode == 0 and dst.exists()


def _convert_heif_convert(src: Path, dst: Path) -> bool:
    # heif-convert <in.heic> <out.jpg>; quality mặc định đủ tốt cho OCR.
    result = subprocess.run(
        ["heif-convert", str(src), str(dst)], capture_output=True, text=True,
    )
    return result.returncode == 0 and dst.exists()


def _convert_pillow_heif(src: Path, dst: Path) -> bool:
    try:
        import pillow_heif
        from PIL import Image
    except ImportError:
        return False
    try:
        pillow_heif.register_heif_opener()
        with Image.open(src) as im:
            im.convert("RGB").save(dst, format="JPEG", quality=92)
    except Exception:
        return False
    return dst.exists()


_CONVERTERS = {
    "sips": _convert_sips,
    "magick": _convert_magick,
    "heif-convert": _convert_heif_convert,
    "pillow-heif": _convert_pillow_heif,
}


def convert_heic(src: Path, dst: Path) -> None:
    """Convert HEIC/HEIF → JPG bằng backend khả dụng đầu tiên. dst phải đuôi .jpg.

    iPhone mặc định chụp HEIC — vision API + pandoc KHÔNG đọc được. Convert lúc
    import (1 lần, không lặp mỗi OCR retry). Thử lần lượt sips→magick→heif-convert
    →pillow-heif; backend đầu thành công thì dừng. KHÔNG backend nào được → raise
    với hướng dẫn cài theo OS (KHÔNG silent-skip: mất trang = sách hỏng)."""
    backends = available_backends()
    if not backends:
        raise RuntimeError(
            f"không có công cụ convert HEIC nào (cần cho {src.name}). {_install_hint()}"
        )
    errors: list[str] = []
    for name in backends:
        try:
            if _CONVERTERS[name](src, dst):
                return
            errors.append(f"{name}: thất bại (xem output)")
        except (OSError, subprocess.SubprocessError) as exc:
            errors.append(f"{name}: {exc}")
        # Dọn file out dở dang trước khi thử backend kế (tránh để lại JPG hỏng).
        dst.unlink(missing_ok=True)
    raise RuntimeError(
        f"convert HEIC thất bại cho {src.name} (đã thử {', '.join(backends)}): "
        f"{'; '.join(errors)}. {_install_hint()}"
    )


# -------------------------------------------------------------------- downscale

def _downscale_sips(src: Path, dst: Path, max_dim: int) -> bool:
    result = subprocess.run(
        ["sips", "-s", "format", "jpeg", "-Z", str(max_dim), str(src), "--out", str(dst)],
        capture_output=True, text=True,
    )
    return result.returncode == 0 and dst.exists()


def _downscale_magick(src: Path, dst: Path, max_dim: int) -> bool:
    # `WxH>` chỉ thu nhỏ khi lớn hơn (giữ ảnh nhỏ nguyên), giữ tỉ lệ.
    result = subprocess.run(
        ["magick", str(src), "-resize", f"{max_dim}x{max_dim}>", str(dst)],
        capture_output=True, text=True,
    )
    return result.returncode == 0 and dst.exists()


def _downscale_pillow(src: Path, dst: Path, max_dim: int) -> bool:
    try:
        import pillow_heif
        from PIL import Image
    except ImportError:
        return False
    try:
        pillow_heif.register_heif_opener()  # cho phép mở cả HEIC nếu cần
        with Image.open(src) as im:
            im = im.convert("RGB")
            im.thumbnail((max_dim, max_dim))  # giữ tỉ lệ, chỉ thu nhỏ
            im.save(dst, format="JPEG", quality=88)
    except Exception:
        return False
    return dst.exists()


def downscale_to_jpeg(src: Path, dst: Path, max_dim: int) -> bool:
    """Downscale ảnh → JPEG ≤ max_dim (cạnh dài). Trả True nếu thành công.

    Chỉ là tối ưu cost cho pre-pass (ảnh nhỏ = ít token). Fail (không backend / lỗi)
    → trả False để caller fallback encode ảnh gốc. KHÔNG raise (downscale không
    bắt buộc, khác convert_heic)."""
    if _has("sips") and _downscale_sips(src, dst, max_dim):
        return True
    if _has("magick") and _downscale_magick(src, dst, max_dim):
        return True
    if _pillow_heif_available() and _downscale_pillow(src, dst, max_dim):
        return True
    return False
