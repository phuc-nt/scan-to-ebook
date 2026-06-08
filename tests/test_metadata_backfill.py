"""Tests cho `_backfill_metadata_from_context`: pre-pass tự điền title/author/year/
translator vào metadata.json khi metadata còn mặc định (title == slug).

Bối cảnh (book-04 / La Fontaine, 2026-06-08):
- `init` tạo metadata.json TRƯỚC pre-pass nên title = slug (vd `book-04`).
- Không backfill → TOC/title-page hiện slug thay vì tên sách thật → bug.
- Fix: pre-pass dò được title thật → ghi vào metadata.json + mutate meta in-place.

Bất biến kiểm thử:
- Backfill CHỈ khi title == slug (user chưa đặt). title thật → no-op (tôn trọng user).
- ctx không có title → no-op (không đoán, giữ slug).
- author/year/translator chỉ điền khi đúng kiểu; year ép về str.
- lang KHÔNG bị đụng (user/init chọn).
"""

from __future__ import annotations

import json
from pathlib import Path

from scan_to_ebook import pipeline


def _meta_default(slug: str) -> dict:
    """meta như `_load_metadata` trả khi metadata.json còn mặc định (title=slug)."""
    return {"title": slug, "author": None, "lang": "vi", "year": None}


def test_backfill_fills_title_author_year_translator(tmp_path: Path):
    scans = tmp_path / "scans"
    scans.mkdir()
    meta = _meta_default("book-04")
    ctx = {
        "title": "THƠ NGỤ-NGÔN LA FONTAINE",
        "author": "Jean de La Fontaine",
        "translator": "Nguyễn Văn Vĩnh",
        "year": 1943,
    }
    pipeline._backfill_metadata_from_context(scans, "book-04", meta, ctx)

    # meta mutate in-place → run hiện tại dùng title thật.
    assert meta["title"] == "THƠ NGỤ-NGÔN LA FONTAINE"
    assert meta["author"] == "Jean de La Fontaine"
    assert meta["year"] == "1943"  # int ép về str
    assert meta["lang"] == "vi"  # không đụng

    # metadata.json ghi đầy đủ (gồm translator dù build chưa render).
    on_disk = json.loads((scans / "metadata.json").read_text(encoding="utf-8"))
    assert on_disk["title"] == "THƠ NGỤ-NGÔN LA FONTAINE"
    assert on_disk["author"] == "Jean de La Fontaine"
    assert on_disk["translator"] == "Nguyễn Văn Vĩnh"
    assert on_disk["year"] == "1943"
    assert on_disk["lang"] == "vi"


def test_backfill_noop_when_user_set_title(tmp_path: Path):
    scans = tmp_path / "scans"
    scans.mkdir()
    meta = {"title": "Tên Thật User Đặt", "author": "Ai Đó", "lang": "vi", "year": None}
    ctx = {"title": "PRE-PASS TITLE", "author": "Khác", "translator": "X", "year": 2000}
    pipeline._backfill_metadata_from_context(scans, "book-04", meta, ctx)

    # title != slug → không ghi đè gì.
    assert meta["title"] == "Tên Thật User Đặt"
    assert meta["author"] == "Ai Đó"
    # không tạo metadata.json (no-op).
    assert not (scans / "metadata.json").exists()


def test_backfill_noop_when_ctx_no_title(tmp_path: Path):
    scans = tmp_path / "scans"
    scans.mkdir()
    meta = _meta_default("book-04")
    ctx = {"title": None, "author": "Có Tác Giả"}
    pipeline._backfill_metadata_from_context(scans, "book-04", meta, ctx)

    assert meta["title"] == "book-04"  # giữ slug, không đoán
    assert meta["author"] is None
    assert not (scans / "metadata.json").exists()


def test_backfill_title_only_when_author_year_missing(tmp_path: Path):
    scans = tmp_path / "scans"
    scans.mkdir()
    meta = _meta_default("book-04")
    ctx = {"title": "CHỈ CÓ TỰA"}
    pipeline._backfill_metadata_from_context(scans, "book-04", meta, ctx)

    assert meta["title"] == "CHỈ CÓ TỰA"
    assert meta["author"] is None
    assert meta["year"] is None
    on_disk = json.loads((scans / "metadata.json").read_text(encoding="utf-8"))
    assert on_disk["title"] == "CHỈ CÓ TỰA"
    assert on_disk["translator"] is None


def test_backfill_ignores_wrong_types(tmp_path: Path):
    scans = tmp_path / "scans"
    scans.mkdir()
    meta = _meta_default("book-04")
    # author là list, translator là dict, title là str hợp lệ → chỉ title nhận.
    ctx = {"title": "TỰA OK", "author": ["a", "b"], "translator": {"x": 1}, "year": [1]}
    pipeline._backfill_metadata_from_context(scans, "book-04", meta, ctx)

    assert meta["title"] == "TỰA OK"
    assert meta["author"] is None  # list bị bỏ
    assert meta["year"] is None  # list bị bỏ
    on_disk = json.loads((scans / "metadata.json").read_text(encoding="utf-8"))
    assert on_disk["translator"] is None  # dict bị bỏ


def test_backfill_year_string_kept(tmp_path: Path):
    scans = tmp_path / "scans"
    scans.mkdir()
    meta = _meta_default("book-04")
    ctx = {"title": "TỰA", "year": "1967"}
    pipeline._backfill_metadata_from_context(scans, "book-04", meta, ctx)
    assert meta["year"] == "1967"
