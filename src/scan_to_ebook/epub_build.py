"""Epub build stage: pandoc book.md → book.epub.

Wrapper subprocess pandoc. --toc --toc-depth=2 --split-level=1 chia spine
theo `# ` heading. Cover image optional.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def build_epub(
    *,
    input_md: Path,
    output_epub: Path,
    cover: Path | None = None,
    toc_depth: int = 2,
    split_level: int = 1,
) -> dict:
    if not input_md.exists():
        raise FileNotFoundError(f"input not found: {input_md}")
    if shutil.which("pandoc") is None:
        raise RuntimeError("pandoc not installed — `brew install pandoc` or apt install pandoc")

    output_epub.parent.mkdir(parents=True, exist_ok=True)

    args = [
        "pandoc",
        str(input_md),
        "-o",
        str(output_epub),
        "--from",
        "markdown",
        "--to",
        "epub",
        "--toc",
        f"--toc-depth={toc_depth}",
        f"--split-level={split_level}",
    ]
    if cover and cover.exists():
        args.append(f"--epub-cover-image={cover}")

    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"pandoc failed (rc={result.returncode}): {result.stderr.strip()}")

    # Verify EPUB magic via `file` if available; else just check size > 0
    magic_ok = True
    if shutil.which("file"):
        check = subprocess.run(["file", str(output_epub)], capture_output=True, text=True)
        magic_ok = "EPUB" in check.stdout

    size = output_epub.stat().st_size
    return {
        "output": str(output_epub),
        "size_bytes": size,
        "magic_ok": magic_ok,
        "pandoc_warnings": result.stderr.strip().splitlines() if result.stderr else [],
    }
