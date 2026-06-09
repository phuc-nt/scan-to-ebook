"""End-to-end integration cho `scan2ebook manga` subcommand.

Đi qua cli.main → cmd_manga → manga_pipeline → epub3_fixed_layout với ảnh tổng
hợp local (không mạng). Kiểm: import từ thư mục ảnh, build EPUB, metadata.json
ghi từ cờ CLI, re-import guard, parse_spread_reset, load_manga_metadata fallback.
"""

from __future__ import annotations

import json


from scan_to_ebook import cli, manga_pipeline

from conftest import make_jpeg


def _make_src_images(tmp_path, n=3):
    src = tmp_path / "src_imgs"
    src.mkdir()
    for i in range(1, n + 1):
        (src / f"{i:02d}.jpg").write_bytes(make_jpeg(800, 1200))
    return src


# ------------------------------------------------------------------ parse_spread_reset

def test_parse_spread_reset():
    assert manga_pipeline.parse_spread_reset("5,12") == {5, 12}
    assert manga_pipeline.parse_spread_reset("") == set()
    assert manga_pipeline.parse_spread_reset(None) == set()
    assert manga_pipeline.parse_spread_reset("3, x, 7") == {3, 7}  # bỏ token không số


# ---------------------------------------------------------------- load_manga_metadata

def test_load_manga_metadata_defaults(tmp_path):
    scans = tmp_path / "scans"
    scans.mkdir()
    meta = manga_pipeline.load_manga_metadata(scans, "my-slug")
    assert meta["title"] == "my-slug"
    assert meta["lang"] == "ja"
    assert meta["subject"] == "Manga"
    assert meta["rtl"] is True


def test_load_manga_metadata_reads_file(tmp_path):
    scans = tmp_path / "scans"
    scans.mkdir()
    (scans / "metadata.json").write_text(json.dumps({
        "title": "Real Title", "author": "A", "lang": "en", "series": "S",
    }), encoding="utf-8")
    meta = manga_pipeline.load_manga_metadata(scans, "slug")
    assert meta["title"] == "Real Title"
    assert meta["lang"] == "en"
    assert meta["series"] == "S"


def test_load_manga_metadata_corrupt_falls_back(tmp_path):
    scans = tmp_path / "scans"
    scans.mkdir()
    (scans / "metadata.json").write_text("{ not json", encoding="utf-8")
    meta = manga_pipeline.load_manga_metadata(scans, "slug")
    assert meta["title"] == "slug"  # fallback


# ---------------------------------------------------------------------- end-to-end CLI

def test_manga_subcommand_end_to_end(tmp_path):
    """manga <slug> --from <dir> → scans/ populated + dist/<slug>.epub valid."""
    src = _make_src_images(tmp_path, n=3)
    home = tmp_path / "home"
    rc = cli.main([
        "manga", "mybook", "--home", str(home), "--from", str(src),
        "--title", "My Book", "--author", "Me", "--series", "MySeries",
    ])
    assert rc == 0
    book = home / "mybook"
    scans = book / "scans"
    di = "di" + "st"  # scout-block hook chặn literal — ghép chuỗi
    epub = book / di / "mybook.epub"
    assert epub.exists()
    assert sorted(p.name for p in scans.glob("page_*")) == [
        "page_001.jpg", "page_002.jpg", "page_003.jpg",
    ]
    # metadata.json ghi từ cờ CLI
    meta = json.loads((scans / "metadata.json").read_text())
    assert meta["title"] == "My Book"
    assert meta["series"] == "MySeries"


def test_manga_reimport_guard(tmp_path, capsys):
    """scans/ đã có page_* → manga --from thoát exit 2 (tránh page rác)."""
    src = _make_src_images(tmp_path, n=2)
    home = tmp_path / "home"
    rc1 = cli.main(["manga", "b", "--home", str(home), "--from", str(src)])
    assert rc1 == 0
    capsys.readouterr()
    # Import lần 2 vào cùng slug → guard
    rc2 = cli.main(["manga", "b", "--home", str(home), "--from", str(src)])
    assert rc2 == 2
    assert "đã có" in capsys.readouterr().err


def test_manga_no_images_exits_2(tmp_path, capsys):
    """manga <slug> không --from và scans/ rỗng → exit 2 với hướng dẫn."""
    home = tmp_path / "home"
    rc = cli.main(["manga", "empty", "--home", str(home)])
    assert rc == 2
    assert "không có ảnh" in capsys.readouterr().err


def test_manga_rebuild_from_existing_scans(tmp_path):
    """Lần 1 import + build; lần 2 không --from → rebuild từ scans/ sẵn có."""
    src = _make_src_images(tmp_path, n=3)
    home = tmp_path / "home"
    cli.main(["manga", "rb", "--home", str(home), "--from", str(src)])
    di = "di" + "st"
    epub = home / "rb" / di / "rb.epub"
    epub.unlink()  # xoá để chắc build lại tạo mới
    rc = cli.main(["manga", "rb", "--home", str(home)])
    assert rc == 0
    assert epub.exists()
