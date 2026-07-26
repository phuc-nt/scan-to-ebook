"""Tests cho 2 tool batch public (tools/ — thay hẳn pattern sed-copy per-group
từng gây bug: README nhóm sau mang nguyên text nhóm trước, 3 chỗ phải sửa tay).

- tools/fix_toc_junk.py: heuristics dọn TOC (đã verify ~180 cuốn) — pure function
  clean_book_md + CLI --csv/--slugs.
- tools/batch_ocr_runner.py: phần thuần test được không cần mạng — load CSV
  (quoted comma), phân loại kết quả WARN, đọc key, đếm 429.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent.parent / "tools"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


fix_toc = _load("fix_toc_junk")
runner = _load("batch_ocr_runner")


# ---------------------------------------------------------------- fix_toc_junk

BOOK_MD = """\
---
title: "Sách Test"
---

## Sách Test

## MỤC LỤC

Chương 1 .... 5
Chương 2 .... 9

## Chương 1

Nội dung chương một.

## Sách Test

Đây là chương THẬT trùng tên sách (có body) — không được đụng.
"""


def test_clean_book_md_removes_toc_and_demotes_empty_title():
    cleaned, removed_toc, demoted = fix_toc.clean_book_md(
        BOOK_MD, "Sách Test", ["Tác Giả X"])
    assert removed_toc == 1
    assert demoted == ["Sách Test"]          # chỉ heading body-RỖNG đầu file
    assert "MỤC LỤC" not in cleaned
    assert "Chương 1 .... 5" not in cleaned  # ruột block TOC bay theo
    # chương thật trùng tên sách (có body) giữ nguyên heading
    assert "## Sách Test\n\nĐây là chương THẬT" in cleaned
    assert "## Chương 1" in cleaned


def test_clean_book_md_noop_when_clean():
    text = "## Chương 1\n\nNội dung.\n"
    cleaned, removed_toc, demoted = fix_toc.clean_book_md(text, "Tên Khác", [])
    assert (removed_toc, demoted) == (0, [])
    assert cleaned == text


def test_fix_toc_cli_csv_mode(tmp_path, capsys):
    home = tmp_path / "books"
    bm = home / "sach-test" / "work" / "book.md"
    bm.parent.mkdir(parents=True)
    bm.write_text(BOOK_MD, encoding="utf-8")
    csv_file = tmp_path / "slugs.csv"
    csv_file.write_text(
        'slug,title,authors\nsach-test,"Sách Test","Tác Giả X"\n', encoding="utf-8")

    rc = fix_toc.main(["--home", str(home), "--csv", str(csv_file)])
    assert rc == 0
    assert "SLUGS:sach-test" in capsys.readouterr().out
    assert bm.with_suffix(".md.bak").exists()          # backup
    assert "MỤC LỤC" not in bm.read_text(encoding="utf-8")


def test_fix_toc_cli_slugs_mode_missing_book_skipped(tmp_path, capsys):
    rc = fix_toc.main(["--home", str(tmp_path), "--slugs", "khong-ton-tai"])
    assert rc == 0
    assert "0 cuốn sửa" in capsys.readouterr().out


# ---------------------------------------------------------------- batch runner

def test_load_books_handles_quoted_commas(tmp_path):
    """Bài học gốc: authors chứa dấu phẩy — IFS=, trong bash split sai."""
    f = tmp_path / "g.csv"
    f.write_text(
        'slug,title,authors,pdf_path\n'
        'sach-mot,"Sách Một: Tập I","Nguyễn A, Trần B",/pdf/sach-mot.pdf\n',
        encoding="utf-8")
    rows = runner.load_books(f)
    assert rows[0]["authors"] == "Nguyễn A, Trần B"
    assert rows[0]["pdf_path"] == "/pdf/sach-mot.pdf"


def test_load_books_limit_and_missing_columns(tmp_path):
    f = tmp_path / "g.csv"
    f.write_text("slug,title,authors,pdf_path\n" +
                 "".join(f"s{i},t,a,/p{i}.pdf\n" for i in range(5)), encoding="utf-8")
    assert len(runner.load_books(f, limit=2)) == 2
    bad = tmp_path / "bad.csv"
    bad.write_text("slug,title\nx,y\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        runner.load_books(bad)


@pytest.mark.parametrize(
    "ok,init_failed,hit_402,expected",
    [
        (True, False, False, "DONE"),
        (False, False, False, "WARN(no-epub)"),
        (False, True, False, "WARN(init-fail)"),
        (False, False, True, "STOP(402)"),
        (True, False, True, "STOP(402)"),    # 402 thắng mọi nhãn khác
    ],
)
def test_classify_result_matrix(ok, init_failed, hit_402, expected):
    assert runner.classify_result(ok, init_failed, hit_402) == expected


@pytest.mark.parametrize(
    "ok,init_failed,hit_402,dead,expected",
    [
        (True, False, False, 3, "DONE(dead=3)"),   # EPUB có nhưng thiếu 3 trang → nhãn riêng
        (True, False, False, 0, "DONE"),            # dead=0 giữ nhãn cũ
        (False, False, False, 3, "WARN(no-epub)"),  # không EPUB → dead không đổi nhãn
        (True, False, True, 3, "STOP(402)"),        # 402 vẫn thắng
    ],
)
def test_classify_result_dead_pages(ok, init_failed, hit_402, dead, expected):
    """B7 review 2026-07-26: sách build kèm dead placeholder từng label DONE trơn —
    batch 'xanh' che sách thiếu ruột, phải quét tay 250 cuốn mới thấy."""
    assert runner.classify_result(ok, init_failed, hit_402, dead) == expected


def test_count_dead_takes_last_match_across_logs(tmp_path):
    """Lấy match CUỐI theo thứ tự log (pass build cuối = số chốt); log thiếu bỏ qua."""
    a = tmp_path / "slug-all-1.log"
    a.write_text("⚠ 5 trang DEAD placeholder (OCR FAILED, THIẾU nội dung): page_1\n", encoding="utf-8")
    b = tmp_path / "slug-all-2.log"
    b.write_text("ok\n⚠ 3 trang DEAD placeholder (OCR FAILED, THIẾU nội dung): page_2\n", encoding="utf-8")
    missing = tmp_path / "khong-ton-tai.log"
    assert runner.count_dead(a, b, missing) == 3
    assert runner.count_dead(missing) == 0
    clean = tmp_path / "clean.log"
    clean.write_text("DONE, khong canh bao\n", encoding="utf-8")
    assert runner.count_dead(clean) == 0


def test_read_api_key_from_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    envf = tmp_path / "creds.env"
    envf.write_text('FOO=bar\nOPENROUTER_API_KEY="sk-file-key"\n', encoding="utf-8")
    assert runner.read_api_key(envf) == "sk-file-key"
    # env thắng file
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-env-key")
    assert runner.read_api_key(envf) == "sk-env-key"
    # không đâu có → exit
    monkeypatch.delenv("OPENROUTER_API_KEY")
    with pytest.raises(SystemExit):
        runner.read_api_key(None)


def test_count_429(tmp_path):
    a = tmp_path / "a.log"
    a.write_text("HTTP 429 Too Many Requests\nrate-limit hit\nok\n", encoding="utf-8")
    b = tmp_path / "khong-ton-tai.log"
    assert runner.count_429(a, b) == 2
