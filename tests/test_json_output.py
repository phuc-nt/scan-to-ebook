"""Tests cho json_output module (P1): agent/script-friendly output modes.

Bối cảnh: pipeline báo tiến trình qua callback on_event(kind, payload). Module
adapt callback đó cho 2 mode:
- --json: stdout = DUY NHẤT 1 summary object, log người ra stderr.
- --json-lines: mỗi event → 1 dòng NDJSON ra stdout.

Yêu cầu cứng: ensure_ascii=False (giữ tiếng Việt), mode_from_args suy đúng,
SummaryCollector gom số liệu start+done, build_summary lọc paths None.
"""

from __future__ import annotations

import argparse
import json

from scan_to_ebook import json_output


# ----------------------------------------------------------- mode_from_args

def _ns(**kw) -> argparse.Namespace:
    return argparse.Namespace(**kw)


def test_mode_from_args_human_default():
    assert json_output.mode_from_args(_ns()) == "human"
    assert json_output.mode_from_args(_ns(json=False, json_lines=False)) == "human"


def test_mode_from_args_json():
    assert json_output.mode_from_args(_ns(json=True, json_lines=False)) == "json"


def test_mode_from_args_json_lines_wins():
    # json_lines bật → ưu tiên (argparse group đảm bảo không bật cả 2, nhưng test thứ tự).
    assert json_output.mode_from_args(_ns(json=False, json_lines=True)) == "json-lines"


# ----------------------------------------------------------- SummaryCollector

def test_collector_merges_start_and_done():
    c = json_output.SummaryCollector()
    c("start", {"total": 75, "skipped": 10, "todo": 65})
    c("page_ok", {"page": "page_001", "latency_s": 1.2, "in": 100, "out": 200, "dst": "x"})
    c("done", {"ok": 64, "blank": 1, "fail": 0, "cost_usd": 3.25})
    pages = c.pages()
    assert pages == {"ok": 64, "blank": 1, "fail": 0, "skipped": 10, "total": 75}
    assert c.cost_usd() == 3.25


def test_collector_empty_defaults():
    c = json_output.SummaryCollector()
    assert c.pages() == {"ok": 0, "blank": 0, "fail": 0, "skipped": 0, "total": 0}
    assert c.cost_usd() == 0.0


# ----------------------------------------------------------- build_summary

def test_build_summary_drops_none_paths():
    s = json_output.build_summary(
        stage="ocr", status="ok", pages={"ok": 1},
        cost_usd=0.05123, paths={"ocr_dir": "/x", "epub_path": None},
    )
    assert s["status"] == "ok"
    assert s["stage"] == "ocr"
    assert s["cost_usd"] == 0.0512  # round 4
    assert s["paths"] == {"ocr_dir": "/x"}  # None bị lọc


def test_build_summary_extra_merged():
    s = json_output.build_summary(
        stage="smoke", status="smoke", pages={}, cost_usd=0.0, paths={},
        extra={"est_full_cost_usd": 3.25, "message": "pass --yes"},
    )
    assert s["est_full_cost_usd"] == 3.25
    assert s["message"] == "pass --yes"


# ----------------------------------------------------------- output streams

def test_emit_event_line_ndjson_unicode(capsys):
    json_output.emit_event_line("page_ok", {"page": "trang_một", "note": "tiếng Việt"})
    out = capsys.readouterr().out.strip()
    obj = json.loads(out)
    assert obj == {"event": "page_ok", "page": "trang_một", "note": "tiếng Việt"}
    assert "tiếng Việt" in out  # ensure_ascii=False giữ dấu


def test_print_summary_single_line_parseable(capsys):
    s = {"status": "ok", "title": "Nam Phong Tạp Chí"}
    json_output.print_summary(s)
    out = capsys.readouterr().out
    assert out.count("\n") == 1  # đúng 1 dòng
    assert json.loads(out) == s


def test_human_stream_goes_to_stderr(capsys):
    json_output.human_stream("done", {"ok": 5, "blank": 0, "fail": 0, "cost_usd": 0.25})
    cap = capsys.readouterr()
    assert cap.out == ""  # stdout sạch
    assert "ok=5" in cap.err


def test_make_emitter_modes():
    cb, coll = json_output.make_emitter("human")
    assert cb is None and coll is None
    cb, coll = json_output.make_emitter("json-lines")
    assert cb is json_output.emit_event_line and coll is None
    cb, coll = json_output.make_emitter("json")
    assert cb is coll and isinstance(coll, json_output.SummaryCollector)
