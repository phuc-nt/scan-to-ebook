"""Tests cho smoke cost-gate (P3): `scan2ebook all <inbox> --smoke`.

Invariant an toàn (CỐT LÕI): KHÔNG bao giờ tiêu cost full nếu chưa có `--yes`,
interactive 'y', hoặc (non-tty) thì abort thay vì treo input().

Chiến lược test: monkeypatch ocr.run_batch (đếm số lần gọi + limit) và
pipeline._build_book (tránh pandoc thật) → kiểm soát luồng gate mà không gọi API/pandoc.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from scan_to_ebook import cli, ocr, pipeline


def _make_inbox(tmp_path: Path, n_pages: int = 20) -> Path:
    """Tạo book-home theo layout mới: <home>/testbook/scans/page_*.png. Trả book-home."""
    book_home = tmp_path / "home" / "testbook"
    scans = book_home / "scans"
    scans.mkdir(parents=True)
    for i in range(1, n_pages + 1):
        (scans / f"page_{i:03d}.png").write_bytes(b"\x89PNG\r\n")
    return book_home


def _smoke_args(inbox: Path, _output=None, **over) -> argparse.Namespace:
    """inbox = path tới book-home (chứa scans/). `_output` (positional cũ) bị bỏ qua:
    layout mới resolve zones từ book-home path trực tiếp (không cần output root riêng)."""
    base = dict(
        inbox=inbox, home=None, output=None, model="m", workers=2, max_tokens=12000,
        dry_run=False, smoke=True, yes=False, upload=False,
        remote="r", folder="f", json=False, json_lines=False,
    )
    base.update(over)
    return argparse.Namespace(**base)


@pytest.fixture
def patched(monkeypatch):
    """Patch run_batch + _build_book + require_api_key. Trả dict tracking."""
    calls = {"run_batch": [], "build_book": 0}

    def fake_run_batch(*, api_key, input_dir, output_dir, model, workers,
                       pattern, limit=None, max_tokens, on_event=None, prompt_context="",
                       lang=None):
        calls["run_batch"].append({"limit": limit})
        # giả lập đã OCR `limit` (hoặc tất cả) trang vào output_dir để
        # collect_pending_pages đếm remaining đúng.
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        pages = sorted(Path(input_dir).glob("page_*.png"))
        n = limit if limit is not None else len(pages)
        for p in pages[:n]:
            (Path(output_dir) / f"{p.stem}.md").write_text("x", encoding="utf-8")
        if on_event:
            on_event("start", {"total": len(pages), "skipped": 0, "todo": n})
            on_event("done", {"ok": n, "blank": 0, "fail": 0, "cost_usd": n * 0.05})
        return {"ok": n, "fail": 0, "blank": 0, "skipped": 0,
                "total": len(pages), "cost_usd": n * 0.05}

    def fake_build_book(bp, scans_dir, meta, *, suffix=""):
        calls["build_book"] += 1
        epub = (bp.work_dir / f"book{suffix}.epub") if suffix else (bp.dist_dir / f"{bp.book_home.name}.epub")
        return {"stats": {"pages_merged": 1, "chars": 1, "h1": 0, "h2": 0, "footnotes": 0},
                "epub_result": {"size_bytes": 2048, "magic_ok": True, "output": str(epub),
                                "pandoc_warnings": []},
                "book_md": bp.work_dir / f"book{suffix}.md", "book_epub": epub}

    monkeypatch.setattr(ocr, "run_batch", fake_run_batch)
    # _build_book đã chuyển sang pipeline.py; smoke/full gate gọi nó qua namespace
    # pipeline → patch ở pipeline mới chặn đúng live call path.
    monkeypatch.setattr(pipeline, "_build_book", fake_build_book)
    monkeypatch.setattr(ocr, "require_api_key", lambda: "sk-test")
    # Context pre-pass chạy TRƯỚC run_batch (gọi API thật) → stub trả block rỗng +
    # cost 0 để test gate flow độc lập (prepass có suite riêng test_context_prepass).
    monkeypatch.setattr(
        pipeline, "_run_prepass_or_abort", lambda **kw: ("", 0.0)
    )
    return calls


# ---------------------------------------------- safety: no full spend w/o approval

def test_smoke_human_answer_no_aborts(patched, tmp_path, monkeypatch, capsys):
    """Human, trả 'n' → smoke OCR (limit=10) rồi STOP. KHÔNG OCR full."""
    inbox = _make_inbox(tmp_path, 20)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *a: "n")
    rc = cli.cmd_all(_smoke_args(inbox, tmp_path / "out"))
    assert rc == 0
    # đúng 1 lần run_batch và là smoke (limit=10) — KHÔNG có lần full (limit=None).
    assert patched["run_batch"] == [{"limit": 10}]
    assert patched["build_book"] == 1  # chỉ mini epub


def test_smoke_human_answer_yes_runs_full(patched, tmp_path, monkeypatch):
    """Human, trả 'y' → smoke THEN full. 2 lần run_batch (limit=10, limit=None)."""
    inbox = _make_inbox(tmp_path, 20)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *a: "y")
    rc = cli.cmd_all(_smoke_args(inbox, tmp_path / "out"))
    assert rc == 0
    limits = [c["limit"] for c in patched["run_batch"]]
    assert limits == [10, None]  # smoke rồi full
    assert patched["build_book"] == 2  # mini + final


def test_smoke_yes_flag_skips_prompt(patched, tmp_path, monkeypatch):
    """--yes → smoke THEN full, KHÔNG gọi input()."""
    inbox = _make_inbox(tmp_path, 20)

    def boom(*a):
        raise AssertionError("input() không được gọi khi --yes")

    monkeypatch.setattr("builtins.input", boom)
    rc = cli.cmd_all(_smoke_args(inbox, tmp_path / "out", yes=True))
    assert rc == 0
    assert [c["limit"] for c in patched["run_batch"]] == [10, None]


def test_smoke_non_tty_aborts_safely(patched, tmp_path, monkeypatch):
    """Non-tty (pipe/CI) không --yes → abort an toàn, KHÔNG treo input(), KHÔNG full."""
    inbox = _make_inbox(tmp_path, 20)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    def boom(*a):
        raise AssertionError("input() không được gọi khi non-tty")

    monkeypatch.setattr("builtins.input", boom)
    rc = cli.cmd_all(_smoke_args(inbox, tmp_path / "out"))
    assert rc == 0
    assert patched["run_batch"] == [{"limit": 10}]  # chỉ smoke


# ---------------------------------------------------------- json gate

def test_smoke_json_gate_returns_cost_exit0(patched, tmp_path, monkeypatch, capsys):
    """--smoke --json (no --yes) → 1 summary {est_full_cost_usd}, exit 0, KHÔNG full."""
    import json as _json

    inbox = _make_inbox(tmp_path, 20)

    def boom(*a):
        raise AssertionError("input() không được gọi ở json mode")

    monkeypatch.setattr("builtins.input", boom)
    rc = cli.cmd_all(_smoke_args(inbox, tmp_path / "out", json=True))
    assert rc == 0
    assert patched["run_batch"] == [{"limit": 10}]  # KHÔNG full
    out = capsys.readouterr().out.strip()
    obj = _json.loads(out)  # stdout = đúng 1 JSON object
    assert obj["status"] == "smoke"
    assert obj["est_full_cost_usd"] == pytest.approx(0.5)  # 10 còn lại × 0.05
    assert obj["remaining_pages"] == 10
    assert "smoke_epub" in obj["paths"]


def test_smoke_est_uses_measured_per_page_cost(tmp_path, monkeypatch, capsys):
    """est_full_cost_usd suy từ cost THẬT của smoke (token-based), không phải flat $0.05.

    Smoke OCR 10 trang tốn $2.00 → per_page=$0.20 → 10 trang còn lại → est=$2.00
    (khác hẳn flat 10×0.05=$0.50)."""
    import json as _json

    inbox = _make_inbox(tmp_path, 20)

    def fake_run_batch(*, api_key, input_dir, output_dir, model, workers,
                       pattern, limit=None, max_tokens, on_event=None, prompt_context="",
                       lang=None):
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        pages = sorted(Path(input_dir).glob("page_*.png"))
        n = limit if limit is not None else len(pages)
        for p in pages[:n]:
            (Path(output_dir) / f"{p.stem}.md").write_text("x", encoding="utf-8")
        if on_event:
            on_event("start", {"total": len(pages), "skipped": 0, "todo": n})
            on_event("done", {"ok": n, "blank": 0, "fail": 0, "cost_usd": 2.0})
        return {"ok": n, "fail": 0, "blank": 0, "skipped": 0,
                "total": len(pages), "cost_usd": 2.0}

    monkeypatch.setattr(ocr, "run_batch", fake_run_batch)
    monkeypatch.setattr(pipeline, "_build_book", lambda *a, **k: {
        "stats": {"pages_merged": 1, "chars": 1, "h1": 0, "h2": 0, "footnotes": 0},
        "epub_result": {"size_bytes": 2048, "magic_ok": True, "output": "x", "pandoc_warnings": []},
        "book_md": tmp_path / "out" / "book.smoke.md", "book_epub": tmp_path / "out" / "book.smoke.epub"})
    monkeypatch.setattr(ocr, "require_api_key", lambda: "sk-test")
    monkeypatch.setattr(pipeline, "_run_prepass_or_abort", lambda **kw: ("", 0.0))

    rc = cli.cmd_all(_smoke_args(inbox, tmp_path / "out", json=True))
    assert rc == 0
    obj = _json.loads(capsys.readouterr().out.strip())
    # $2.00 / 10 ok = $0.20/trang × 10 còn lại = $2.00 (KHÔNG phải flat $0.50).
    assert obj["est_full_cost_usd"] == pytest.approx(2.0)


def test_smoke_yes_carries_prepass_cost_into_full_summary(
    patched, tmp_path, monkeypatch, capsys
):
    """M2: prepass cost tiêu ở smoke PHẢI xuất hiện trong summary full (--yes).

    Full prepass = cache hit (cost 0) → nếu không carry, prepass_cost_usd=0 ⇒
    agent under-count spend. Stub prepass trả cost $0.09 ở smoke, full = cache (0).
    """
    import json as _json

    inbox = _make_inbox(tmp_path, 20)

    # smoke prepass tốn $0.09; full prepass (lần 2) = cache hit cost 0.
    prepass_calls = {"n": 0}

    def fake_prepass(**kw):
        prepass_calls["n"] += 1
        return ("", 0.09) if prepass_calls["n"] == 1 else ("", 0.0)

    monkeypatch.setattr(pipeline, "_run_prepass_or_abort", fake_prepass)
    rc = cli.cmd_all(_smoke_args(inbox, tmp_path / "out", yes=True, json=True))
    assert rc == 0
    assert [c["limit"] for c in patched["run_batch"]] == [10, None]  # smoke → full
    obj = _json.loads(capsys.readouterr().out.strip())
    assert obj["status"] == "ok"
    # prepass_cost_usd cuối = carried (0.09 từ smoke) + full prepass (0) = 0.09.
    assert obj["prepass_cost_usd"] == pytest.approx(0.09)
    # cost tổng = OCR full (20×0.05=1.0) + prepass carried 0.09 = 1.09.
    assert obj["cost_usd"] == pytest.approx(1.09)


def test_smoke_fewer_than_10_pages(patched, tmp_path, monkeypatch, capsys):
    """<10 trang: smoke OCR tất cả, remaining=0, est $0.00, gate vẫn hiện (json)."""
    import json as _json

    inbox = _make_inbox(tmp_path, 5)
    rc = cli.cmd_all(_smoke_args(inbox, tmp_path / "out", json=True))
    assert rc == 0
    obj = _json.loads(capsys.readouterr().out.strip())
    assert obj["remaining_pages"] == 0
    assert obj["est_full_cost_usd"] == 0.0
