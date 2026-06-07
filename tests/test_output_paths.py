"""Tests cho path resolution sau restructure (layout: <home>/<slug>/{scans,work,dist}).

Yêu cầu:
- _resolve_data_root ưu tiên: --home > $SCAN2EBOOK_HOME > $SCAN2EBOOK_OUTPUT_ROOT
  (deprecated, warn) > ~/scan2ebook.
- _resolve_book_paths: slug → data-root/slug; path (có separator / tồn tại) → dùng thẳng.
- _resolve_output_root (shim cũ) giờ trả zone work/ (cache root), KHÔNG còn phẳng.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from scan_to_ebook import pipeline


def _args(**over) -> argparse.Namespace:
    base = dict(home=None, output=None)
    base.update(over)
    return argparse.Namespace(**base)


# --------------------------------------------------------- _resolve_data_root

def test_data_root_home_flag_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("SCAN2EBOOK_HOME", str(tmp_path / "env"))
    got = pipeline._resolve_data_root(_args(home=tmp_path / "flag"))
    assert got == tmp_path / "flag"  # --home thắng cả env


def test_data_root_env_home(tmp_path, monkeypatch):
    monkeypatch.setenv("SCAN2EBOOK_HOME", str(tmp_path / "env"))
    monkeypatch.delenv("SCAN2EBOOK_OUTPUT_ROOT", raising=False)
    got = pipeline._resolve_data_root(_args())
    assert got == tmp_path / "env"


def test_data_root_legacy_env_alias_warns(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("SCAN2EBOOK_HOME", raising=False)
    monkeypatch.setenv("SCAN2EBOOK_OUTPUT_ROOT", str(tmp_path / "legacy"))
    got = pipeline._resolve_data_root(_args())
    assert got == tmp_path / "legacy"
    assert "deprecated" in capsys.readouterr().err.lower()


def test_data_root_default_is_home_scan2ebook(monkeypatch):
    monkeypatch.delenv("SCAN2EBOOK_HOME", raising=False)
    monkeypatch.delenv("SCAN2EBOOK_OUTPUT_ROOT", raising=False)
    got = pipeline._resolve_data_root(_args())
    assert got == Path.home() / "scan2ebook"
    assert got.is_absolute()


# ------------------------------------------------------- _resolve_book_paths

def test_book_paths_from_slug(tmp_path, monkeypatch):
    monkeypatch.delenv("SCAN2EBOOK_OUTPUT_ROOT", raising=False)
    bp = pipeline._resolve_book_paths(_args(home=tmp_path / "root"), Path("mybook"))
    assert bp.book_home == tmp_path / "root" / "mybook"
    assert bp.scans_dir == bp.book_home / "scans"
    assert bp.work_dir == bp.book_home / "work"
    assert bp.ocr_dir == bp.book_home / "work" / "ocr"
    assert bp.dist_dir == bp.book_home / "dist"


def test_book_paths_from_explicit_path(tmp_path):
    # Có separator → coi là path tới book-home, KHÔNG ghép data-root.
    home = tmp_path / "some" / "where" / "mybook"
    bp = pipeline._resolve_book_paths(_args(), home)
    assert bp.book_home == home
    assert bp.scans_dir == home / "scans"


def test_book_paths_output_override(tmp_path):
    bp = pipeline._resolve_book_paths(_args(output=tmp_path / "X"), Path("ignored-slug"))
    assert bp.book_home == tmp_path / "X"
    assert bp.work_dir == tmp_path / "X" / "work"


def test_book_paths_output_override_warns(tmp_path, capsys):
    # M1: --output deprecated → cảnh báo stderr (mirror SCAN2EBOOK_OUTPUT_ROOT).
    pipeline._resolve_book_paths(_args(output=tmp_path / "X"), Path("ignored-slug"))
    assert "deprecated" in capsys.readouterr().err.lower()


def test_book_paths_slug_not_swallowed_by_cwd_dir(tmp_path, monkeypatch):
    # H1: slug trần trùng tên thư mục trong CWD vẫn resolve về data-root, KHÔNG
    # bị CWD nuốt (quyết định path chỉ dựa separator, không dò is_dir()).
    monkeypatch.delenv("SCAN2EBOOK_OUTPUT_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "mybook").mkdir()  # bẫy: dir cùng tên slug ngay trong CWD
    bp = pipeline._resolve_book_paths(_args(home=tmp_path / "root"), Path("mybook"))
    assert bp.book_home == tmp_path / "root" / "mybook"


def test_book_paths_relative_dir_is_path_mode(tmp_path, monkeypatch):
    # H1: path có separator (thư mục cha) → path mode, ghép từ CWD.
    monkeypatch.chdir(tmp_path)
    bp = pipeline._resolve_book_paths(_args(), Path("sub/mybook"))
    assert bp.book_home == Path("sub/mybook")


# --------------------------------------------- _resolve_output_root shim (work/)

def test_resolve_output_root_shim_returns_work(tmp_path, monkeypatch):
    monkeypatch.delenv("SCAN2EBOOK_OUTPUT_ROOT", raising=False)
    got = pipeline._resolve_output_root(_args(home=tmp_path / "root"), tmp_path / "x", "mybook")
    assert got == tmp_path / "root" / "mybook" / "work"


def test_resolve_output_root_shim_output_flag(tmp_path):
    got = pipeline._resolve_output_root(_args(output=tmp_path / "X"), tmp_path / "x", "mybook")
    assert got == tmp_path / "X" / "work"
    assert got.resolve().is_absolute()
