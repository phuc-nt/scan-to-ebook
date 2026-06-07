"""Tests cho P0/P1 UX helpers: auto-load .env, slugify tên epub, multi-ext
glob (PNG+JPG), và lệnh `init` tạo skeleton inbox.

Bối cảnh (UX assessment 2026-06-07):
- F1: phải `source .env` mỗi shell → _load_dotenv() nạp tự động, KHÔNG đè env sẵn.
- F2: `all` hardcode *.png → JPG vô hình → _glob_patterns gộp nhiều ext.
- F4: epub rename = raw title → vỡ nếu có / : * → _slugify ascii kebab-case.
- F7: thiếu lệnh tạo inbox → cmd_init mkdir + import ảnh + metadata mẫu.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from scan_to_ebook import cli, ocr


# ----------------------------------------------------------- F1: auto-load .env

def test_load_dotenv_sets_missing_key(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("OPENROUTER_API_KEY=sk-test-123\n", encoding="utf-8")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    cli._load_dotenv()
    assert os.environ["OPENROUTER_API_KEY"] == "sk-test-123"


def test_load_dotenv_does_not_override_existing(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("OPENROUTER_API_KEY=from-file\n", encoding="utf-8")
    monkeypatch.setenv("OPENROUTER_API_KEY", "from-shell")
    cli._load_dotenv()
    assert os.environ["OPENROUTER_API_KEY"] == "from-shell"  # export/source thắng


def test_load_dotenv_skips_comments_and_quotes(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        '# comment\n\nFOO="quoted val"\nBAR=plain\n', encoding="utf-8"
    )
    monkeypatch.delenv("FOO", raising=False)
    monkeypatch.delenv("BAR", raising=False)
    cli._load_dotenv()
    assert os.environ["FOO"] == "quoted val"
    assert os.environ["BAR"] == "plain"


# ------------------------------------------------------------- F4: slugify

def test_slugify_strips_vietnamese_diacritics():
    assert cli._slugify("Nam Phong Tạp Chí Q01 (1917)") == "nam-phong-tap-chi-q01-1917"


def test_slugify_handles_d_and_forbidden_chars():
    assert cli._slugify("Đảng Đỏ") == "dang-do"
    assert cli._slugify("Phở/Bún: ngon*") == "pho-bun-ngon"


def test_slugify_empty_fallback():
    assert cli._slugify("") == "book"
    assert cli._slugify("///") == "book"


# ------------------------------------------------- F2: multi-ext glob + dedupe

def test_glob_patterns_multi_ext(tmp_path: Path):
    for n in ("page_2.png", "page_10.png", "page_1.jpg", "ignore.txt"):
        (tmp_path / n).write_bytes(b"x")
    got = sorted(ocr._glob_patterns(tmp_path, cli.IMAGE_PATTERNS), key=ocr.natural_sort_key)
    assert [p.name for p in got] == ["page_1.jpg", "page_2.png", "page_10.png"]


def test_glob_patterns_dedupe(tmp_path: Path):
    """File khớp nhiều glob (vd cả *.png và *.PNG case-insensitive fs) không trùng."""
    (tmp_path / "page_1.png").write_bytes(b"x")
    got = ocr._glob_patterns(tmp_path, "*.png,*.png")
    assert len(got) == 1


# ----------------------------------------------------------------- F7: init

def test_cmd_init_creates_skeleton(tmp_path: Path):
    import argparse

    args = argparse.Namespace(
        slug="my-book", base=tmp_path, from_dir=None,
        title="Sách Của Tôi", author="Ai Đó", lang="vi", year="2020",
    )
    rc = cli.cmd_init(args)
    assert rc == 0
    inbox = tmp_path / "my-book"
    assert inbox.is_dir()
    meta = json.loads((inbox / "metadata.json").read_text(encoding="utf-8"))
    assert meta["title"] == "Sách Của Tôi"
    assert meta["author"] == "Ai Đó"


def test_cmd_init_imports_and_renames(tmp_path: Path):
    import argparse

    src = tmp_path / "scan"
    src.mkdir()
    # Filename lộn xộn, non-padded → import phải natural-sort + rename page_NNN
    for n in ("IMG_2.jpg", "IMG_10.jpg", "IMG_1.png"):
        (src / n).write_bytes(b"x")

    args = argparse.Namespace(
        slug="b", base=tmp_path / "inbox", from_dir=src,
        title=None, author=None, lang="vi", year=None,
    )
    rc = cli.cmd_init(args)
    assert rc == 0
    inbox = tmp_path / "inbox" / "b"
    pages = sorted(p.name for p in inbox.glob("page_*"))
    assert pages == ["page_001.png", "page_002.jpg", "page_003.jpg"]
    # title fallback = slug khi không truyền --title
    meta = json.loads((inbox / "metadata.json").read_text(encoding="utf-8"))
    assert meta["title"] == "b"


def test_cmd_init_refuses_reimport_when_pages_exist(tmp_path: Path):
    """Re-import vào inbox đã có page_* phải abort (rc=2), không để page rác."""
    import argparse

    inbox = tmp_path / "inbox" / "b"
    inbox.mkdir(parents=True)
    (inbox / "page_001.png").write_bytes(b"x")  # đã có sẵn 1 page

    src = tmp_path / "scan"
    src.mkdir()
    (src / "new.jpg").write_bytes(b"y")

    args = argparse.Namespace(
        slug="b", base=tmp_path / "inbox", from_dir=src,
        title=None, author=None, lang="vi", year=None,
    )
    rc = cli.cmd_init(args)
    assert rc == 2
    # page cũ giữ nguyên, không import thêm
    assert sorted(p.name for p in inbox.glob("page_*")) == ["page_001.png"]


def test_import_images_deterministic_same_stem(tmp_path: Path):
    """Cùng stem khác ext → thứ tự ổn định theo suffix, không phụ thuộc glob."""
    src = tmp_path / "s"
    src.mkdir()
    (src / "scan_1.png").write_bytes(b"x")
    (src / "scan_1.jpg").write_bytes(b"y")
    dst = tmp_path / "d"
    dst.mkdir()
    n = cli._import_images(src, dst)
    assert n == 2
    # .jpg < .png theo suffix tie-break → page_001 là .jpg
    assert sorted(p.name for p in dst.glob("page_*")) == ["page_001.jpg", "page_002.png"]


def test_cmd_init_keeps_existing_metadata(tmp_path: Path):
    import argparse

    inbox = tmp_path / "b"
    inbox.mkdir()
    (inbox / "metadata.json").write_text('{"title":"GIỮ NGUYÊN"}', encoding="utf-8")
    args = argparse.Namespace(
        slug="b", base=tmp_path, from_dir=None,
        title="Mới", author=None, lang="vi", year=None,
    )
    cli.cmd_init(args)
    meta = json.loads((inbox / "metadata.json").read_text(encoding="utf-8"))
    assert meta["title"] == "GIỮ NGUYÊN"  # không ghi đè
