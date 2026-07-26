"""Kiểm tra cấu trúc EPUB hàng loạt: `scan2ebook verify <path>...`.

Bài học batch lớn: validate bằng vòng lặp shell `unzip -t` KHÔNG tin được —
bash mis-split CSV có dấu phẩy trong field, zsh `if cmd >/dev/null` nuốt exit
code làm skip âm thầm; chỉ python `zipfile.testzip()` bắt đúng. Trước đây mỗi
đợt lại viết lại một heredoc — nay là subcommand chính thức.

Mỗi path đầu vào là một trong:
- file `.epub`             → check chính file đó
- book-home (có scans/ hoặc dist/) → check `dist/<tên-home>.epub`
- thư mục chứa nhiều book-home     → check từng home con bên trong

Phân loại: OK (zip lành, >= TINY_BYTES) | TINY (< 10KB — vỏ rỗng/pandoc chết
giữa chừng) | BADZIP (hỏng CRC / không phải zip) | MISSING (chưa build).
rc 0 chỉ khi TẤT CẢ đều OK.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path

TINY_BYTES = 10_240


@dataclass
class VerifyResult:
    label: str   # tên hiển thị (slug hoặc filename)
    path: Path   # file epub đã check (hoặc path kỳ vọng khi MISSING)
    status: str  # "OK" | "TINY" | "BADZIP" | "MISSING"
    size: int = 0


def _check_epub(path: Path) -> tuple[str, int]:
    if not path.is_file():
        return "MISSING", 0
    size = path.stat().st_size
    if size < TINY_BYTES:
        return "TINY", size
    try:
        with zipfile.ZipFile(path) as zf:
            if zf.testzip() is not None:
                return "BADZIP", size
    except zipfile.BadZipFile:
        return "BADZIP", size
    return "OK", size


def _expected_epub(book_home: Path) -> Path:
    return book_home / "dist" / f"{book_home.name}.epub"


def _is_book_home(path: Path) -> bool:
    return (path / "scans").is_dir() or (path / "dist").is_dir()


def verify_paths(paths: list[Path]) -> list[VerifyResult]:
    """Resolve từng path theo 3 dạng ở docstring module, check hết, trả list.

    Thư mục không phải book-home và không chứa book-home nào → 1 kết quả MISSING
    (để lỗi gõ nhầm path không bao giờ im lặng)."""
    results: list[VerifyResult] = []
    for p in paths:
        p = p.expanduser()
        if p.suffix == ".epub" or p.is_file():
            status, size = _check_epub(p)
            results.append(VerifyResult(p.name, p, status, size))
        elif p.is_dir() and _is_book_home(p):
            epub = _expected_epub(p)
            status, size = _check_epub(epub)
            results.append(VerifyResult(p.name, epub, status, size))
        elif p.is_dir():
            homes = sorted(
                (d for d in p.iterdir() if d.is_dir() and _is_book_home(d)),
                key=lambda d: d.name,
            )
            if not homes:
                results.append(VerifyResult(p.name, p, "MISSING", 0))
                continue
            for home in homes:
                epub = _expected_epub(home)
                status, size = _check_epub(epub)
                results.append(VerifyResult(home.name, epub, status, size))
        else:
            results.append(VerifyResult(p.name, p, "MISSING", 0))
    return results


def summarize(results: list[VerifyResult]) -> dict:
    counts = {"OK": 0, "TINY": 0, "BADZIP": 0, "MISSING": 0}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    counts["total"] = len(results)
    return counts
