"""Sổ cost cộng dồn per-book: `work/cost.json`.

Bài học batch lớn: mỗi pass (`all` lần 1, `ocr` retry, `all` lần 2) tự in dòng
`cost~$` cho các trang pass đó xử lý — dòng CUỐI không phải tổng, nên chi thực
phải grep + cộng tay mọi log (một nhóm 34 cuốn lệch ~10% vì thế). Ledger này ghi
MỖI lần tiêu tiền một entry vào `work/cost.json`; tổng = sum mọi entry, không
phải diễn giải log.

Thiết kế:
- Append-only list JSON: `[{"ts", "stage", "pages_ok", "tokens_in", "tokens_out",
  "cost_usd"}, ...]`. Ghi atomic (tmp + os.replace) — cùng pattern ocr._atomic_write.
- 1 cuốn = 1 lane trong batch driver (queue chung) nên không có concurrent writer
  cùng file; không cần lock.
- File hỏng (mất điện giữa ghi, JSON rác) → rename `cost.json.corrupt-<n>` rồi
  bắt đầu sổ mới — KHÔNG BAO GIỜ crash build vì sổ cost.
- Entry cost <= 0 không ghi (cache hit / dry-run không làm bẩn sổ).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

LEDGER_NAME = "cost.json"


def _ledger_path(work_dir: Path) -> Path:
    return work_dir / LEDGER_NAME


def _load_entries(ledger: Path) -> list[dict]:
    """Đọc list entry; file hỏng → rename .corrupt-N + trả sổ rỗng."""
    if not ledger.exists():
        return []
    try:
        entries = json.loads(ledger.read_text(encoding="utf-8"))
        if isinstance(entries, list):
            return entries
    except (OSError, json.JSONDecodeError):
        pass
    # hỏng/không phải list → giữ tang chứng, mở sổ mới
    n = 0
    while True:
        corrupt = ledger.with_name(f"{LEDGER_NAME}.corrupt-{n}")
        if not corrupt.exists():
            break
        n += 1
    try:
        os.replace(ledger, corrupt)
    except OSError:
        pass
    return []


def _atomic_write(dst: Path, text: str) -> None:
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, dst)


def append_entry(work_dir: Path, stage: str, summary: dict) -> None:
    """Ghi 1 entry tiêu tiền vào sổ. summary cần key cost_usd (+ ok/tokens tùy có).

    Gọi sau mỗi run_batch / pre-pass có cost > 0. Mọi lỗi I/O đều nuốt (sổ cost
    không bao giờ được phép làm fail build)."""
    try:
        cost = float(summary.get("cost_usd") or 0.0)
        if cost <= 0:
            return
        work_dir.mkdir(parents=True, exist_ok=True)
        ledger = _ledger_path(work_dir)
        entries = _load_entries(ledger)
        entries.append({
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "stage": stage,
            "pages_ok": summary.get("ok", 0),
            "tokens_in": summary.get("tokens_in", 0),
            "tokens_out": summary.get("tokens_out", 0),
            "cost_usd": round(cost, 6),
        })
        _atomic_write(ledger, json.dumps(entries, ensure_ascii=False, indent=1))
    except (OSError, TypeError, ValueError):
        pass


def total(work_dir: Path) -> float:
    """Tổng chi thực của cuốn = sum mọi entry. Sổ không có/hỏng → 0.0."""
    ledger = _ledger_path(work_dir)
    if not ledger.exists():
        return 0.0
    try:
        entries = json.loads(ledger.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0.0
    if not isinstance(entries, list):
        return 0.0
    tot = 0.0
    for e in entries:
        if isinstance(e, dict):
            try:
                tot += float(e.get("cost_usd") or 0.0)
            except (TypeError, ValueError):
                continue
    return round(tot, 6)
