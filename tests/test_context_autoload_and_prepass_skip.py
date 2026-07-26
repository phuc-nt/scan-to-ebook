"""Regression tests cho 2 fix P1 (bài học batch 3):

Fix 1 — `ocr` trần auto-load context block: pass retry của batch driver gọi
        `scan2ebook ocr <scans> <work/ocr>` → trước đây mọi trang retry OCR bằng
        base prompt (mất chính tả cổ / chế độ thơ / tên riêng) dù work/context.json
        nằm ngay cạnh. Nay tự load (context.json là source-of-truth, re-derive qua
        render_block); `--no-context` tắt; `--context <file>` override.

Fix 2 — `all` bỏ qua pre-pass khi 0 trang cần OCR (+ `--skip-prepass`): sách bị
        provider moderation chặn ảnh mẫu (`data_inspection_failed`) từng abort cả
        build dù md đã đủ 100% — pre-pass chỉ phục vụ OCR nên 0-todo → skip hẳn.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from scan_to_ebook import cli, context_prepass, ocr, pipeline


# ---------------------------------------------------------------- helpers

def _book_home(tmp_path: Path, n_pages: int = 3, with_context: bool = True,
               ocr_done: int = 0) -> Path:
    """Book-home chuẩn <home>/testbook/{scans,work/ocr}. Trả book-home."""
    home = tmp_path / "home" / "testbook"
    scans = home / "scans"
    ocr_dir = home / "work" / "ocr"
    scans.mkdir(parents=True)
    ocr_dir.mkdir(parents=True)
    for i in range(1, n_pages + 1):
        (scans / f"page_{i:03d}.png").write_bytes(b"\x89PNG\r\n")
    for i in range(1, ocr_done + 1):
        (ocr_dir / f"page_{i:03d}.md").write_text("noi dung", encoding="utf-8")
    if with_context:
        (home / "work" / "context.json").write_text(
            '{"title": "Sách Test", "content_type": "prose"}', encoding="utf-8"
        )
    return home


def _ocr_args(input_dir: Path, output_dir: Path, **over) -> argparse.Namespace:
    base = dict(
        input=input_dir, output=output_dir, model="m", workers=1,
        pattern="*.png", limit=None, max_tokens=12000, lang="vi",
        context=None, no_context=False, dry_run=False,
        json=False, json_lines=False,
    )
    base.update(over)
    return argparse.Namespace(**base)


def _all_args(inbox: Path, **over) -> argparse.Namespace:
    base = dict(
        inbox=inbox, home=None, output=None, model="m", workers=1,
        max_tokens=12000, dry_run=False, smoke=False, yes=True, upload=False,
        skip_prepass=False, remote="r", folder="f", json=False, json_lines=False,
    )
    base.update(over)
    return argparse.Namespace(**base)


@pytest.fixture
def captured_batch(monkeypatch):
    """Patch ocr.run_batch để bắt prompt_context; không gọi API thật."""
    seen = {}

    def fake_run_batch(*, api_key, input_dir, output_dir, model, workers,
                       pattern, limit=None, max_tokens, on_event=None,
                       prompt_context="", lang=None):
        seen["prompt_context"] = prompt_context
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        return {"ok": 0, "fail": 0, "blank": 0, "skipped": 0, "total": 0,
                "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "failures": []}

    monkeypatch.setattr(ocr, "run_batch", fake_run_batch)
    monkeypatch.setattr(ocr, "require_api_key", lambda: "sk-test")
    return seen


# ------------------------------------------------- Fix 1: ocr context auto-load

def test_ocr_autoloads_sibling_context(tmp_path, captured_batch):
    """work/context.json cạnh output → run_batch nhận block render từ cache."""
    home = _book_home(tmp_path)
    rc = cli.cmd_ocr(_ocr_args(home / "scans", home / "work" / "ocr"))
    assert rc == 0
    expected = context_prepass.render_block({"title": "Sách Test", "content_type": "prose"})
    assert captured_batch["prompt_context"] == expected
    assert "BỐI CẢNH SÁCH" in captured_batch["prompt_context"]


def test_ocr_no_context_flag_disables_autoload(tmp_path, captured_batch):
    """--no-context → base prompt (prompt_context rỗng) dù cache tồn tại."""
    home = _book_home(tmp_path)
    rc = cli.cmd_ocr(_ocr_args(home / "scans", home / "work" / "ocr", no_context=True))
    assert rc == 0
    assert captured_batch["prompt_context"] == ""


def test_ocr_explicit_context_file_wins_over_cache(tmp_path, captured_batch):
    """--context <file> đọc verbatim, thắng cache context.json."""
    home = _book_home(tmp_path)
    ctx_file = tmp_path / "custom-context.txt"
    ctx_file.write_text("KHỐI BỐI CẢNH TỰ SOẠN", encoding="utf-8")
    rc = cli.cmd_ocr(_ocr_args(home / "scans", home / "work" / "ocr", context=ctx_file))
    assert rc == 0
    assert captured_batch["prompt_context"] == "KHỐI BỐI CẢNH TỰ SOẠN"


def test_ocr_without_cache_uses_base_prompt(tmp_path, captured_batch):
    """Không có context.json (layout tự do) → prompt_context rỗng, không lỗi."""
    home = _book_home(tmp_path, with_context=False)
    rc = cli.cmd_ocr(_ocr_args(home / "scans", home / "work" / "ocr"))
    assert rc == 0
    assert captured_batch["prompt_context"] == ""


# --------------------------------------- Fix 2: all skips prepass when possible

@pytest.fixture
def prepass_sentinel(monkeypatch):
    """Prepass gọi là FAIL test (giả lập moderation-block: gọi = nổ)."""
    calls = {"prepass": 0, "build": 0}

    def boom(**kw):
        calls["prepass"] += 1
        raise AssertionError("pre-pass không được gọi trong kịch bản này")

    def fake_build_book(bp, scans_dir, meta, *, suffix=""):
        calls["build"] += 1
        epub = bp.dist_dir / f"{bp.book_home.name}.epub"
        return {"stats": {"pages_merged": 1, "chars": 1, "h1": 0, "h2": 0, "footnotes": 0},
                "epub_result": {"size_bytes": 2048, "magic_ok": True, "output": str(epub),
                                "pandoc_warnings": []},
                "book_md": bp.work_dir / "book.md", "book_epub": epub}

    monkeypatch.setattr(pipeline, "_run_prepass_or_abort", boom)
    monkeypatch.setattr(pipeline, "_build_book", fake_build_book)
    monkeypatch.setattr(ocr, "require_api_key", lambda: "sk-test")
    return calls


def test_all_zero_todo_skips_prepass_and_builds(tmp_path, prepass_sentinel, captured_batch):
    """Md đủ 100% (rebuild) → KHÔNG gọi pre-pass, build vẫn chạy tới epub.

    Đây là fix cho sách moderation-block: trước đây rebuild-from-md vẫn abort
    ở pre-pass dù không còn gì để OCR."""
    home = _book_home(tmp_path, n_pages=3, ocr_done=3)
    rc = cli.cmd_all(_all_args(home))
    assert rc == 0
    assert prepass_sentinel["prepass"] == 0
    assert prepass_sentinel["build"] == 1
    # 0-todo vẫn dùng cache context nếu có (vô hại — run_batch nhận block, không có trang để chạy)
    assert "BỐI CẢNH SÁCH" in captured_batch["prompt_context"]


def test_all_skip_prepass_flag_with_pending_pages(tmp_path, prepass_sentinel, captured_batch):
    """--skip-prepass + còn trang chưa OCR → không gọi pre-pass, OCR vẫn chạy
    với cache context (nếu có)."""
    home = _book_home(tmp_path, n_pages=3, ocr_done=1)
    rc = cli.cmd_all(_all_args(home, skip_prepass=True))
    assert rc == 0
    assert prepass_sentinel["prepass"] == 0
    assert "BỐI CẢNH SÁCH" in captured_batch["prompt_context"]


def test_all_normal_path_still_runs_prepass(tmp_path, monkeypatch, captured_batch):
    """Đường thường (còn trang, không flag) → pre-pass VẪN được gọi (không đổi hành vi)."""
    home = _book_home(tmp_path, n_pages=3, ocr_done=0)
    calls = {"prepass": 0}

    def fake_prepass(**kw):
        calls["prepass"] += 1
        return ("BLOCK-TU-PREPASS", 0.01)

    def fake_build_book(bp, scans_dir, meta, *, suffix=""):
        epub = bp.dist_dir / f"{bp.book_home.name}.epub"
        return {"stats": {"pages_merged": 1, "chars": 1, "h1": 0, "h2": 0, "footnotes": 0},
                "epub_result": {"size_bytes": 2048, "magic_ok": True, "output": str(epub),
                                "pandoc_warnings": []},
                "book_md": bp.work_dir / "book.md", "book_epub": epub}

    monkeypatch.setattr(pipeline, "_run_prepass_or_abort", fake_prepass)
    monkeypatch.setattr(pipeline, "_build_book", fake_build_book)
    rc = cli.cmd_all(_all_args(home))
    assert rc == 0
    assert calls["prepass"] == 1
    assert captured_batch["prompt_context"] == "BLOCK-TU-PREPASS"
