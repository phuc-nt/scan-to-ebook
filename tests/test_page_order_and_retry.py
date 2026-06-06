"""Regression tests cho 2 bug phát hiện qua full-run Nam Phong Q01 (75 trang):

Bug 1: thứ tự trang sai. Filename không zero-pad (page_5..page_80) +
       sorted() lexical → page_10 đứng trước page_5 → trang 5-9 (bìa, mục lục)
       bị nhét xuống cuối book.md. Fix: natural_sort_key tách số → sort số học.

Bug 2: fail oan khi response JSON bị cắt/malformed. json.JSONDecodeError không
       khớp pattern transient nào → raise luôn, không retry (trang text dày
       page_37/44 mất nội dung). Fix: gắn marker "malformed response" → retry.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scan_to_ebook import ocr, post_process


# ---------------------------------------------------------------- Bug 1: order

def test_natural_sort_key_numeric_order():
    """page_9 phải đứng trước page_10 (không lexical string sort)."""
    names = ["page_10", "page_5", "page_80", "page_9", "page_13", "page_6"]
    got = sorted((Path(n + ".md") for n in names), key=ocr.natural_sort_key)
    assert [p.stem for p in got] == [
        "page_5", "page_6", "page_9", "page_10", "page_13", "page_80",
    ]


def test_merge_pages_orders_numerically(tmp_path: Path):
    """merge_pages phải ghép theo số trang, không theo lexical filename."""
    ocr_dir = tmp_path / "ocr"
    ocr_dir.mkdir()
    # Tạo 3 trang với marker nhận biết. Lexical sort sẽ xếp 10 trước 5.
    (ocr_dir / "page_5.md").write_text("PAGE_FIVE", encoding="utf-8")
    (ocr_dir / "page_9.md").write_text("PAGE_NINE", encoding="utf-8")
    (ocr_dir / "page_10.md").write_text("PAGE_TEN", encoding="utf-8")

    out = tmp_path / "book.md"
    post_process.merge_pages(input_dir=ocr_dir, output_path=out, title="T")

    body = out.read_text(encoding="utf-8")
    # Đúng thứ tự: 5 < 9 < 10
    assert body.index("PAGE_FIVE") < body.index("PAGE_NINE") < body.index("PAGE_TEN")


def test_collect_pending_pages_numeric_order(tmp_path: Path):
    """collect_pending_pages trả todo theo thứ tự số trang."""
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    for n in (5, 9, 10, 80):
        (inbox / f"page_{n}.png").write_bytes(b"x")
    todo, total = ocr.collect_pending_pages(inbox, "*.png", out, limit=None)
    assert total == 4
    assert [p.stem for p in todo] == ["page_5", "page_9", "page_10", "page_80"]


# ---------------------------------------------------------------- Bug 2: retry

def _err(msg: str):
    return RuntimeError(msg)


@pytest.mark.parametrize(
    "msg",
    [
        "malformed response (JSON parse): Expecting value: line 167 column 1",
        "HTTP 429 Too Many Requests",
        "HTTP 503 Service Unavailable",
        "empty content (finish_reason=None)",
    ],
)
def test_ocr_page_retries_transient(monkeypatch, tmp_path: Path, msg):
    """Transient (bao gồm malformed JSON) phải được retry tới hết số lần."""
    img = tmp_path / "page_1.png"
    img.write_bytes(b"\x89PNG\r\n")
    monkeypatch.setattr(ocr.time, "sleep", lambda *_: None)  # không chờ backoff

    calls = {"n": 0}

    def fake_post_once(*_a, **_k):
        calls["n"] += 1
        raise _err(msg)

    monkeypatch.setattr(ocr, "_post_once", fake_post_once)
    with pytest.raises(RuntimeError):
        ocr.ocr_page("k", "m", img, retries=2)
    # retries=2 → 1 lần đầu + 2 retry = 3 lần gọi
    assert calls["n"] == 3, f"expected 3 attempts for transient, got {calls['n']}"


@pytest.mark.parametrize("msg", ["HTTP 403 Forbidden", "HTTP 400 Bad Request"])
def test_ocr_page_no_retry_on_non_transient(monkeypatch, tmp_path: Path, msg):
    """Non-transient (4xx config/auth) fail ngay lần đầu, không retry."""
    img = tmp_path / "page_1.png"
    img.write_bytes(b"\x89PNG\r\n")
    monkeypatch.setattr(ocr.time, "sleep", lambda *_: None)

    calls = {"n": 0}

    def fake_post_once(*_a, **_k):
        calls["n"] += 1
        raise _err(msg)

    monkeypatch.setattr(ocr, "_post_once", fake_post_once)
    with pytest.raises(RuntimeError):
        ocr.ocr_page("k", "m", img, retries=2)
    assert calls["n"] == 1, f"non-transient must not retry, got {calls['n']} calls"
