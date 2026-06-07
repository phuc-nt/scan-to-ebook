"""Output emitters cho agent/script-friendly modes (`--json`, `--json-lines`).

Bối cảnh: pipeline báo tiến trình qua callback `on_event(kind, payload)`
(ocr.run_batch). Mặc định cli dùng `_print_ocr_event` in cho người đọc. Hai
emitter ở đây adapt CÙNG signature đó cho agent:

- json-lines: mỗi event → 1 dòng JSON ra stdout (stream realtime).
- json: log người đẩy sang stderr, stdout chỉ in DUY NHẤT 1 object tổng kết
  cuối cùng (agent parse stdout = sạch, không lẫn log).

Stdlib-only. `ensure_ascii=False` mọi nơi để giữ tiếng Việt có dấu.
"""

from __future__ import annotations

import json
import sys


def _dumps(obj: dict) -> str:
    """JSON 1 dòng, giữ Unicode (tiếng Việt) nguyên vẹn."""
    return json.dumps(obj, ensure_ascii=False)


def mode_from_args(args) -> str:
    """Suy ra output mode từ argparse Namespace.

    Trả 'json' | 'json-lines' | 'human'. Hai cờ loại trừ nhau (argparse
    mutually-exclusive group đảm bảo không bật cả hai)."""
    if getattr(args, "json_lines", False):
        return "json-lines"
    if getattr(args, "json", False):
        return "json"
    return "human"


def emit_event_line(kind: str, payload: dict) -> None:
    """In 1 dòng NDJSON cho 1 event ra stdout (dùng ở json-lines mode)."""
    line = {"event": kind}
    line.update(payload)
    print(_dumps(line), flush=True)


class SummaryCollector:
    """Emitter cho `--json` mode: nuốt event, gom số liệu, in 1 object cuối.

    Dùng làm `on_event` cho run_batch. Lưu payload của event 'done' (chứa
    ok/blank/fail/cost). Không in gì trong lúc chạy — cli gọi `print_summary`
    sau khi pipeline xong để đảm bảo stdout chỉ có đúng 1 object."""

    def __init__(self) -> None:
        self.done_payload: dict = {}
        self.start_payload: dict = {}
        self.context_payload: dict = {}

    def __call__(self, kind: str, payload: dict) -> None:
        if kind == "start":
            self.start_payload = dict(payload)
        elif kind == "done":
            self.done_payload = dict(payload)
        elif kind in ("context_ok", "context_fail"):
            self.context_payload = dict(payload)
        # các event page_* không cần buffer ở json mode (chi tiết per-page bỏ
        # qua; muốn chi tiết thì dùng --json-lines).

    def pages(self) -> dict:
        """Khối `pages` cho summary, gộp start (total/skipped) + done (ok/blank/fail)."""
        d = self.done_payload
        return {
            "ok": d.get("ok", 0),
            "blank": d.get("blank", 0),
            "fail": d.get("fail", 0),
            "skipped": self.start_payload.get("skipped", 0),
            "total": self.start_payload.get("total", 0),
        }

    def cost_usd(self) -> float:
        return self.done_payload.get("cost_usd", 0.0)


def build_summary(
    *,
    stage: str,
    status: str,
    pages: dict,
    cost_usd: float,
    paths: dict,
    extra: dict | None = None,
) -> dict:
    """Dựng summary object chuẩn cho `--json` mode.

    `paths` chỉ giữ key có giá trị (ocr-only run không có epub_path)."""
    summary = {
        "status": status,
        "stage": stage,
        "pages": pages,
        "cost_usd": round(cost_usd, 4),
        "paths": {k: v for k, v in paths.items() if v is not None},
    }
    if extra:
        summary.update(extra)
    return summary


def print_summary(summary: dict) -> None:
    """In summary object ra stdout (đúng 1 dòng, parse được bằng json.loads)."""
    print(_dumps(summary), flush=True)


def make_emitter(mode: str):
    """Trả `(on_event_callback, collector_or_None)` theo mode.

    - human      → (None, None): cli dùng emitter người mặc định của nó.
    - json-lines → (emit_event_line, None): mỗi event ra stdout NDJSON.
    - json       → (collector, collector): collector vừa là on_event vừa giữ
      số liệu để cli build summary cuối. Log người cli tự lo đẩy sang stderr.
    """
    if mode == "json-lines":
        return emit_event_line, None
    if mode == "json":
        collector = SummaryCollector()
        return collector, collector
    return None, None


def human_stream(kind: str, payload: dict) -> None:
    """Bản sao `_print_ocr_event` nhưng ra STDERR — dùng ở json/json-lines mode
    để người vẫn thấy tiến trình mà không bẩn stdout. Giữ format giống stdout
    human path."""
    if kind == "start":
        print(
            f"Total found: {payload['total']} | resumed (skipped): {payload['skipped']} | todo: {payload['todo']}",
            file=sys.stderr,
        )
    elif kind == "page_ok":
        print(
            f"  - {payload['page']}: ok latency={payload['latency_s']}s in={payload['in']} out={payload['out']} -> {payload['dst']}",
            file=sys.stderr,
        )
    elif kind == "page_blank":
        print(f"  - {payload['page']}: blank → placeholder {payload['dst']}", file=sys.stderr)
    elif kind == "page_fail":
        print(f"  - {payload['page']}: FAIL {payload['error']}", file=sys.stderr)
    elif kind == "context_ok":
        cached = " (cached)" if payload.get("from_cache") else ""
        print(
            f"Context: {payload.get('title')} | {payload.get('pages_per_image')}p/ảnh | "
            f"{payload.get('toc_entries')} mục lục | {payload.get('proper_names')} tên riêng "
            f"| ~${payload.get('cost_usd')}{cached}",
            file=sys.stderr,
        )
    elif kind == "context_fail":
        print(f"Context pre-pass FAIL: {payload.get('error')}", file=sys.stderr)
    elif kind == "done":
        print(
            f"\nDone. ok={payload['ok']} blank={payload['blank']} fail={payload['fail']} cost~${payload['cost_usd']}",
            file=sys.stderr,
        )
