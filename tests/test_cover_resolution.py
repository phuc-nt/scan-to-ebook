"""Tests cho _resolve_cover (pipeline.py): chọn ảnh bìa cho epub.

Thứ tự ưu tiên: scans/cover.jpg (user override) > context.json[cover_page]
(pre-pass dò) > None. cover_page chỉ nhận basename trong scans_dir (chặn path
traversal). File không tồn tại → None (build_epub tự bỏ qua cover None).

Pure-fs, không mạng/API. context.json ghi tay vào work_dir (giả lập cache pre-pass).
"""

from __future__ import annotations

import json
from pathlib import Path

from scan_to_ebook import pipeline


def _zones(tmp_path: Path) -> tuple[Path, Path]:
    """Tạo scans/ + work/ rỗng, trả (scans_dir, work_dir)."""
    scans = tmp_path / "scans"
    work = tmp_path / "work"
    scans.mkdir()
    work.mkdir()
    return scans, work


def _write_ctx(work: Path, **fields) -> None:
    (work / "context.json").write_text(
        json.dumps({"title": "X", **fields}, ensure_ascii=False), encoding="utf-8"
    )


# ------------------------------------------------------------ priority order

def test_user_cover_jpg_wins(tmp_path):
    """scans/cover.jpg tồn tại → thắng tuyệt đối, kể cả khi cover_page khác."""
    scans, work = _zones(tmp_path)
    (scans / "cover.jpg").write_bytes(b"\xff\xd8\xff")
    (scans / "page_001.jpg").write_bytes(b"\xff\xd8\xff")
    _write_ctx(work, cover_page="page_001.jpg")
    # user_cover trả trực tiếp (chưa resolve) — so sánh không resolve.
    assert pipeline._resolve_cover(scans, work) == scans / "cover.jpg"


def test_cover_page_used_when_no_user_cover(tmp_path):
    """Không có cover.jpg → dùng context.json[cover_page] → scans/<cover_page>."""
    scans, work = _zones(tmp_path)
    (scans / "page_001.jpg").write_bytes(b"\xff\xd8\xff")
    _write_ctx(work, cover_page="page_001.jpg")
    # cover_page branch resolve() path → so sánh với bản đã resolve (macOS /tmp symlink).
    assert pipeline._resolve_cover(scans, work) == (scans / "page_001.jpg").resolve()


# ------------------------------------------------------------ no-cover cases

def test_no_cover_when_nothing(tmp_path):
    """Không cover.jpg, không context.json → None (sách trắng đen, không bìa)."""
    scans, work = _zones(tmp_path)
    assert pipeline._resolve_cover(scans, work) is None


def test_cover_page_null_returns_none(tmp_path):
    """Pre-pass trả cover_page=null → None (đã chốt: bỏ qua, không đoán bừa)."""
    scans, work = _zones(tmp_path)
    _write_ctx(work, cover_page=None)
    assert pipeline._resolve_cover(scans, work) is None


def test_cover_page_missing_field_returns_none(tmp_path):
    """context.json cũ (chưa có field cover_page) → None, không crash."""
    scans, work = _zones(tmp_path)
    _write_ctx(work)  # không có cover_page
    assert pipeline._resolve_cover(scans, work) is None


def test_cover_page_nonexistent_file_returns_none(tmp_path):
    """cover_page trỏ file không có trong scans → None (không trỏ rác cho pandoc)."""
    scans, work = _zones(tmp_path)
    _write_ctx(work, cover_page="page_099.jpg")
    assert pipeline._resolve_cover(scans, work) is None


# ------------------------------------------------------------ security: traversal

def test_cover_page_path_traversal_blocked(tmp_path):
    """cover_page chứa ../ → bị chặn (context.json hand-editable, không tin tuyệt đối)."""
    scans, work = _zones(tmp_path)
    secret = tmp_path / "secret.jpg"
    secret.write_bytes(b"\xff\xd8\xff")
    _write_ctx(work, cover_page="../secret.jpg")
    assert pipeline._resolve_cover(scans, work) is None


def test_cover_page_absolute_path_blocked(tmp_path):
    """cover_page là absolute path (chứa /) → bị chặn, chỉ nhận basename."""
    scans, work = _zones(tmp_path)
    _write_ctx(work, cover_page="/etc/passwd")
    assert pipeline._resolve_cover(scans, work) is None


def test_cover_page_non_string_returns_none(tmp_path):
    """cover_page bị sửa tay thành non-string (vd số) → None, không crash."""
    scans, work = _zones(tmp_path)
    _write_ctx(work, cover_page=123)
    assert pipeline._resolve_cover(scans, work) is None


def test_cover_page_symlink_escape_blocked(tmp_path):
    """Symlink trong scans/ trỏ RA NGOÀI → realpath-containment chặn (L1 hardening)."""
    scans, work = _zones(tmp_path)
    secret = tmp_path / "secret.jpg"
    secret.write_bytes(b"\xff\xd8\xff")
    (scans / "evil.jpg").symlink_to(secret)  # basename hợp lệ nhưng target ngoài scans
    _write_ctx(work, cover_page="evil.jpg")
    assert pipeline._resolve_cover(scans, work) is None
