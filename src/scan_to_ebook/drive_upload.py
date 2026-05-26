"""Drive upload stage: epub → Google Drive via rclone.

Why rclone (not gdrive CLI / Python google-api):
- One-time `rclone config` (~3p OAuth), creds stored ở chỗ rclone tự quản
- Không vướng sandbox HOME issue
- Mỗi máy chỉ cần config 1 lần, sync remote name

User setup (one-time):
    brew install rclone
    rclone config        # new remote, name=gdrive, type=drive, scope=drive

Usage:
    rclone copy book.epub gdrive:Ebooks/
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


DEFAULT_REMOTE = "gdrive"
DEFAULT_FOLDER = "Ebooks"


def upload(
    *,
    local_path: Path,
    remote: str = DEFAULT_REMOTE,
    folder: str = DEFAULT_FOLDER,
    rename: str | None = None,
) -> dict:
    if shutil.which("rclone") is None:
        raise RuntimeError(
            "rclone not installed. Setup: `brew install rclone && rclone config` "
            "(new remote name=gdrive, type=drive)"
        )
    if not local_path.exists():
        raise FileNotFoundError(f"local file not found: {local_path}")

    dest = f"{remote}:{folder}"
    args = ["rclone", "copy", str(local_path), dest, "--progress"]
    if rename:
        # rclone copy không rename — copyto file→file mới rename được
        args = ["rclone", "copyto", str(local_path), f"{dest}/{rename}", "--progress"]

    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"rclone failed (rc={result.returncode}): {result.stderr.strip()}")

    return {
        "local": str(local_path),
        "remote": dest,
        "rename": rename,
        "stdout": result.stdout.strip(),
    }
