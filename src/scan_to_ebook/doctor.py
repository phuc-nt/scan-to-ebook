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


def run_checks() -> list[dict]:
    """Chạy tất cả check. Trả list dict{name, ok, essential, detail}. Không in gì."""
    return [_check_python(), _check_pandoc(), _check_key(), _check_rclone()]


def all_essential_ok(results: list[dict]) -> bool:
    return all(c["ok"] for c in results if c["essential"])
