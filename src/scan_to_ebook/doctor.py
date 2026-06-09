"""Environment self-check cho `scan2ebook doctor`.

Người lạ sau khi cài chạy lệnh này để biết thiếu gì (pandoc? key? rclone?)
trước khi tốn tiền OCR. Pure logic — `run_checks()` trả list dict, cli lo format.

An toàn: check key chỉ báo present/absent (KHÔNG bao giờ in giá trị key).
Stdlib-only. KHÔNG gọi `ocr.require_api_key()` (nó raise SystemExit).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

from . import archive_extract, image_ops, pdf_render


def _check_python() -> dict:
    """Python >= 3.10 và package import được (đang chạy ⇒ import OK)."""
    ok = sys.version_info >= (3, 10)
    ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    detail = f"Python {ver}" if ok else f"Python {ver} (cần >= 3.10)"
    return {"name": "python", "ok": ok, "essential": True, "detail": detail}


def _check_pandoc() -> dict:
    """pandoc có trên PATH (bắt buộc cho stage epub)."""
    path = shutil.which("pandoc")
    if not path:
        return {"name": "pandoc", "ok": False, "essential": True,
                "detail": "not installed (cần cho build epub — `brew install pandoc`)"}
    version = "installed (version unknown)"
    try:
        out = subprocess.run(
            ["pandoc", "--version"], capture_output=True, text=True, timeout=5
        )
        first = out.stdout.splitlines()[0].strip() if out.stdout else ""
        if first:
            version = first
    except (OSError, subprocess.SubprocessError):
        pass  # giữ fallback "installed (version unknown)"
    return {"name": "pandoc", "ok": True, "essential": True, "detail": version}


def _check_key() -> dict:
    """OPENROUTER_API_KEY có trong env (sau _load_dotenv). CHỈ present/absent."""
    present = bool(os.environ.get("OPENROUTER_API_KEY"))
    detail = "present" if present else "missing (set OPENROUTER_API_KEY in .env)"
    return {"name": "openrouter_key", "ok": present, "essential": True, "detail": detail}


def _check_rclone() -> dict:
    """rclone optional — chỉ cần khi upload. Vắng = warning, không fail build."""
    path = shutil.which("rclone")
    if path:
        return {"name": "rclone", "ok": True, "essential": False, "detail": "installed"}
    return {"name": "rclone", "ok": False, "essential": False,
            "detail": "not installed (upload disabled, optional)"}


def _check_heic() -> dict:
    """Backend convert HEIC optional — chỉ cần khi import ảnh iPhone (.heic/.heif).

    Vắng = warning (sách JPG/PNG vẫn chạy). Có ≥1 backend → liệt kê cái khả dụng."""
    backends = image_ops.available_backends()
    if backends:
        return {"name": "heic_convert", "ok": True, "essential": False,
                "detail": f"available: {', '.join(backends)}"}
    return {"name": "heic_convert", "ok": False, "essential": False,
            "detail": f"none (HEIC import disabled). {image_ops._install_hint()}"}


def _check_pdf_render() -> dict:
    """Backend render PDF optional — chỉ cần khi import từ file .pdf.

    Vắng = warning (sách ảnh JPG/PNG/HEIC vẫn chạy). Có ≥1 backend → liệt kê."""
    backends = pdf_render.available_backends()
    if backends:
        return {"name": "pdf_render", "ok": True, "essential": False,
                "detail": f"available: {', '.join(backends)}"}
    return {"name": "pdf_render", "ok": False, "essential": False,
            "detail": f"none (PDF import disabled). {pdf_render._install_hint()}"}


def _check_rar_backend() -> dict:
    """Backend giải nén CBR/RAR optional — chỉ cần khi input là .cbr.

    Vắng = warning (CBZ/ZIP vẫn chạy bình thường). Có ≥1 backend → liệt kê."""
    backends = archive_extract.available_rar_backends()
    if backends:
        return {"name": "rar_backend", "ok": True, "essential": False,
                "detail": f"available: {', '.join(backends)}"}
    return {"name": "rar_backend", "ok": False, "essential": False,
            "detail": f"none (CBR import disabled). {archive_extract._install_hint()}"}


def run_checks() -> list[dict]:
    """Chạy tất cả check. Trả list dict{name, ok, essential, detail}. Không in gì."""
    return [_check_python(), _check_pandoc(), _check_key(), _check_heic(),
            _check_pdf_render(), _check_rar_backend(), _check_rclone()]


def all_essential_ok(results: list[dict]) -> bool:
    return all(c["ok"] for c in results if c["essential"])
