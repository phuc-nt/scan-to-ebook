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
        "empty content (finish_reason=None)",
    ],
)
def test_ocr_page_content_class_early_aborts_on_same_error_class(monkeypatch, tmp_path: Path, msg):
    """Lỗi CONTENT-class (empty/malformed) lặp cùng lớp → early-abort sau N lần.

    Provider đọc cùng ảnh trả cùng kết quả hỏng = deterministic thật; retry thêm
    chỉ tốn thời gian/tiền. Với retries=4 mà lỗi luôn cùng class, abort ở lần thứ
    _DETERMINISTIC_ABORT_AFTER thay vì chạy hết 5 lần. Đây là fix cho tail-stall
    (1 trang chết deterministic từng kéo cả cuốn 2h+)."""
    img = tmp_path / "page_1.png"
    img.write_bytes(b"\x89PNG\r\n")
    monkeypatch.setattr(ocr.time, "sleep", lambda *_: None)  # không chờ backoff

    calls = {"n": 0}

    def fake_post_once(*_a, **_k):
        calls["n"] += 1
        raise _err(msg)

    monkeypatch.setattr(ocr, "_post_once", fake_post_once)
    # Early-abort phải raise DeadPageError (marker cho run_batch ghi placeholder) —
    # KHÔNG phải RuntimeError thường (loại đó để trang trống cho pass sau cứu).
    with pytest.raises(ocr.DeadPageError):
        ocr.ocr_page("k", "m", img, retries=4)
    # Cùng class mỗi lần → abort ngay khi đếm được _DETERMINISTIC_ABORT_AFTER lần,
    # KHÔNG chạy hết retries+1=5 lần.
    assert calls["n"] == ocr._DETERMINISTIC_ABORT_AFTER, (
        f"deterministic same-class phải abort sau {ocr._DETERMINISTIC_ABORT_AFTER} lần, "
        f"got {calls['n']}"
    )


@pytest.mark.parametrize(
    "msg",
    [
        "HTTP 429 Too Many Requests",
        "HTTP 503 Service Unavailable",
        "request timed out / network error: The read operation timed out",
    ],
)
def test_ocr_page_infra_transient_never_dead_even_when_repeated(monkeypatch, tmp_path: Path, msg):
    """429/5xx/timeout LẶP CÙNG CLASS vẫn KHÔNG DeadPageError — retry hết vòng rồi
    raise thường (trang trống, pass sau cứu).

    Bug B2 review 2026-07-26: burst rate-limit / incident provider trả lỗi y hệt
    nhau trong 1-2s → counter deterministic cũ chôn oan trang nghẽn tạm thành
    placeholder vĩnh viễn (mất nội dung + phải cứu tay). Lỗi hạ tầng không nói gì
    về trang → phải hưởng trọn retry budget."""
    img = tmp_path / "page_1.png"
    img.write_bytes(b"\x89PNG\r\n")
    monkeypatch.setattr(ocr.time, "sleep", lambda *_: None)

    calls = {"n": 0}

    def fake_post_once(*_a, **_k):
        calls["n"] += 1
        raise _err(msg)

    monkeypatch.setattr(ocr, "_post_once", fake_post_once)
    with pytest.raises(RuntimeError) as ei:
        ocr.ocr_page("k", "m", img, retries=3)
    assert not isinstance(ei.value, ocr.DeadPageError), (
        f"lỗi hạ tầng lặp lại KHÔNG được thành DeadPageError: {ei.value}"
    )
    # Hưởng trọn retry budget: retries=3 → 4 lần gọi, không early-abort.
    assert calls["n"] == 4, f"phải retry hết vòng (4 lần), got {calls['n']}"


@pytest.mark.parametrize("marker", ["data_inspection_failed", "image format is illegal"])
def test_ocr_page_moderation_400_dead_on_first_attempt(monkeypatch, tmp_path: Path, marker):
    """HTTP 400 moderation/định-dạng-ảnh → DeadPageError NGAY lần đầu (1 call).

    Bug B1 review 2026-07-26: fix C1 scope placeholder về DeadPageError nhưng 400
    moderation là non-transient → raise thường → không placeholder → trang kẹt todo
    vĩnh viễn → mọi pass `all` fail>0 → sách KHÔNG BAO GIỜ build được (WARN loop).
    400 content-deterministic phải dead ngay: retry cùng ảnh không bao giờ khác."""
    img = tmp_path / "page_1.png"
    img.write_bytes(b"\x89PNG\r\n")
    monkeypatch.setattr(ocr.time, "sleep", lambda *_: None)

    calls = {"n": 0}

    def fake_post_once(*_a, **_k):
        calls["n"] += 1
        raise _err(f'HTTP 400 Bad Request: {{"error":{{"code":"{marker}","message":"..."}}}}')

    monkeypatch.setattr(ocr, "_post_once", fake_post_once)
    with pytest.raises(ocr.DeadPageError):
        ocr.ocr_page("k", "m", img, retries=4)
    assert calls["n"] == 1, f"400 deterministic phải dead ngay lần 1, got {calls['n']}"


def test_ocr_page_transient_varying_class_retries_to_exhaustion(monkeypatch, tmp_path: Path):
    """Lỗi transient nhưng KHÁC class mỗi lần (line/col khác) → KHÔNG early-abort.

    Malformed JSON với vị trí char khác nhau mỗi lần = có tiến triển/khác nhau →
    _error_class chuẩn hoá số về '#' nhưng nếu phần chữ khác thì class khác. Ở đây
    ta cố tình xen kẽ 2 class khác hẳn nhau để chứng minh same_class_count reset →
    chạy hết retries."""
    img = tmp_path / "page_1.png"
    img.write_bytes(b"\x89PNG\r\n")
    monkeypatch.setattr(ocr.time, "sleep", lambda *_: None)

    # Xen kẽ 2 lớp lỗi transient khác hẳn nhau → không bao giờ 2 lần liên tiếp cùng class.
    msgs = [
        "HTTP 429 Too Many Requests",
        "HTTP 503 Service Unavailable",
    ]
    calls = {"n": 0}

    def fake_post_once(*_a, **_k):
        m = msgs[calls["n"] % len(msgs)]
        calls["n"] += 1
        raise _err(m)

    monkeypatch.setattr(ocr, "_post_once", fake_post_once)
    with pytest.raises(RuntimeError) as ei:
        ocr.ocr_page("k", "m", img, retries=2)
    # retries=2 → 1 lần đầu + 2 retry = 3 lần gọi (không early-abort vì class đổi mỗi lần)
    assert calls["n"] == 3, f"class đổi mỗi lần phải retry hết, got {calls['n']}"
    # Hết retry vì transient (nghẽn tạm) KHÔNG phải deterministic → không DeadPageError
    # (trang phải còn trống để pass retry sau cứu, không bị placeholder hoá).
    assert not isinstance(ei.value, ocr.DeadPageError)


def test_error_class_normalizes_varying_json_positions():
    """2 lỗi malformed JSON khác line/col/char → cùng error class (deterministic)."""
    a = ("malformed response (JSON parse): Expecting value: "
         "line 2997 column 1 (char 16478) | body[:200]='...'")
    b = ("malformed response (JSON parse): Expecting value: "
         "line 3050 column 1 (char 20000) | body[:200]='khac han'")
    assert ocr._error_class(a) == ocr._error_class(b)
    # Khác nguyên nhân (no choices) → khác class
    c = "no choices in response: something"
    assert ocr._error_class(a) != ocr._error_class(c)


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


# --------------------------------------- Bug 4: timeout không được wrap → fail oan

def test_post_once_wraps_timeout_as_transient(monkeypatch, tmp_path: Path):
    """`urlopen` timeout raise TimeoutError (KHÔNG phải RuntimeError). _post_once
    phải wrap thành RuntimeError chứa 'timed out' để _is_transient bắt được → retry.

    Bug thật batch3: provider ôm connection im lặng, trang kẹt hết timeout rồi FAIL
    thẳng (ocr_page chỉ except RuntimeError, TimeoutError lọt lên run_batch → fail),
    không backoff-retry. Kéo cả cuốn 27-29 phút. Hạ timeout 300→90s + wrap timeout."""
    img = tmp_path / "page_1.png"
    img.write_bytes(b"\x89PNG\r\n")

    def fake_urlopen(*_a, **_k):
        raise TimeoutError("The read operation timed out")

    monkeypatch.setattr(ocr.urlreq, "urlopen", fake_urlopen)
    with pytest.raises(RuntimeError) as ei:
        ocr._post_once("k", "m", "Yg==", "image/png", 100)
    assert ocr._is_transient(str(ei.value)), f"timeout phải là transient: {ei.value}"


# ------------------------------------------------- Bug 3: AppleDouble sidecar

def test_glob_skips_appledouble_sidecars(tmp_path: Path):
    """`._page_NNN.jpg` (macOS trên exFAT/SMB) KHÔNG được coi là trang sách.

    Phát hiện khi chạy sách từ ổ ngoài exFAT: macOS đẻ 1 file `._x.jpg` kèm mỗi
    ảnh → glob nhặt cả 2 → số trang nhân đôi, mỗi file rác ăn HTTP 400 "image
    format is illegal" → fail-rate ~50% và vẫn tính tiền. Im lặng nên phải khoá."""
    for i in (1, 2, 3):
        (tmp_path / f"page_{i:03d}.jpg").write_bytes(b"\xff\xd8\xff\xe0real")
        (tmp_path / f"._page_{i:03d}.jpg").write_bytes(b"\x00\x05\x16\x07junk")
    (tmp_path / ".DS_Store").write_bytes(b"junk")

    found = ocr._glob_patterns(tmp_path, "*.jpg")
    names = sorted(p.name for p in found)
    assert names == ["page_001.jpg", "page_002.jpg", "page_003.jpg"], names


def test_collect_pending_pages_excludes_sidecars(tmp_path: Path):
    """Sidecar không lọt vào todo/total của collect_pending_pages (đếm = tiền)."""
    src = tmp_path / "scans"
    src.mkdir()
    for i in (1, 2):
        (src / f"page_{i:03d}.jpg").write_bytes(b"\xff\xd8\xff\xe0real")
        (src / f"._page_{i:03d}.jpg").write_bytes(b"\x00\x05\x16\x07junk")

    todo, total = ocr.collect_pending_pages(src, "*.jpg", tmp_path / "out", None)
    assert total == 2, f"total phải đếm 2 trang thật, không phải 4: {total}"
    assert all(not p.name.startswith("._") for p in todo)


# --------------------------- Bug 5: dead-page placeholder cắt vòng re-OCR cross-pass

def test_run_batch_writes_dead_placeholder_and_next_pass_skips(monkeypatch, tmp_path: Path):
    """Trang fail DETERMINISTIC (DeadPageError) → run_batch ghi DEAD_PLACEHOLDER
    (.md size>0) → collect_pending_pages pass sau BỎ QUA trang đó (không re-OCR).

    Đây là fix tail-stall: trước đây trang chết không có .md nên mỗi pass (all-1,
    ocr retry, all-2) lại ôm đủ vòng retry cho nó → 1 trang kéo cả cuốn 2h+."""
    inbox = tmp_path / "scans"
    inbox.mkdir()
    out = tmp_path / "out"
    (inbox / "page_1.png").write_bytes(b"\x89PNG\r\n")

    def always_dead(*_a, **_k):
        raise ocr.DeadPageError("malformed response (JSON parse): Expecting value: line 1")

    monkeypatch.setattr(ocr, "ocr_page", always_dead)

    summary = ocr.run_batch(
        api_key="k", input_dir=inbox, output_dir=out,
        model="m", workers=1, pattern="*.png",
    )
    assert summary["fail"] == 1, f"trang chết vẫn phải tính fail: {summary}"
    assert summary["ok"] == 0

    md = out / "page_1.md"
    assert md.exists() and md.stat().st_size > 0, "phải ghi placeholder size>0"
    assert "OCR FAILED" in md.read_text(encoding="utf-8")

    # Pass sau: trang đã có .md non-empty → không còn trong todo (không re-OCR).
    todo, total = ocr.collect_pending_pages(inbox, "*.png", out, None)
    assert total == 1
    assert todo == [], f"trang dead phải bị skip pass sau, còn todo: {todo}"
    # list_dead_pages phát hiện đúng trang để pipeline la to trước khi build.
    assert ocr.list_dead_pages(out) == ["page_1"]


def test_run_batch_moderation_400_writes_placeholder_and_next_pass_skips(monkeypatch, tmp_path: Path):
    """End-to-end qua ocr_page THẬT: 400 data_inspection_failed → placeholder →
    pass sau skip → sách build được (trang tính fail, list_dead_pages thấy).

    Đây là kịch bản batch3 thật (4 trang / 3 cuốn bị moderation chặn); code sau fix
    C1 từng để các cuốn đó WARN loop vô hạn."""
    inbox = tmp_path / "scans"
    inbox.mkdir()
    out = tmp_path / "out"
    (inbox / "page_1.png").write_bytes(b"\x89PNG\r\n")
    monkeypatch.setattr(ocr.time, "sleep", lambda *_: None)

    def fake_post_once(*_a, **_k):
        raise _err('HTTP 400 Bad Request: {"error":{"code":"data_inspection_failed"}}')

    monkeypatch.setattr(ocr, "_post_once", fake_post_once)
    summary = ocr.run_batch(
        api_key="k", input_dir=inbox, output_dir=out,
        model="m", workers=1, pattern="*.png",
    )
    assert summary["fail"] == 1, f"trang moderation vẫn tính fail: {summary}"
    md = out / "page_1.md"
    assert md.exists() and "OCR FAILED" in md.read_text(encoding="utf-8")
    # Pass sau: placeholder size>0 → skip, sách build được thay vì kẹt todo mãi.
    todo, _ = ocr.collect_pending_pages(inbox, "*.png", out, None)
    assert todo == [], f"trang moderation phải bị skip pass sau: {todo}"
    assert ocr.list_dead_pages(out) == ["page_1"]


def test_run_batch_non_deterministic_fail_leaves_page_blank(monkeypatch, tmp_path: Path):
    """Fail KHÔNG-deterministic (402 hết credit / transient hết retry) → KHÔNG ghi
    placeholder — trang còn trống để lần chạy lại (sau nạp credit) OCR tiếp.

    Bug C1 review 2026-07-26: placeholder hoá mọi fail biến 402 giữa chừng thành
    mất nội dung vĩnh viễn mà fail=0 + verify vẫn xanh."""
    inbox = tmp_path / "scans"
    inbox.mkdir()
    out = tmp_path / "out"
    (inbox / "page_1.png").write_bytes(b"\x89PNG\r\n")

    def fail_402(*_a, **_k):
        raise RuntimeError("HTTP 402 Payment Required")

    monkeypatch.setattr(ocr, "ocr_page", fail_402)
    summary = ocr.run_batch(
        api_key="k", input_dir=inbox, output_dir=out,
        model="m", workers=1, pattern="*.png",
    )
    assert summary["fail"] == 1
    assert not (out / "page_1.md").exists(), "402 KHÔNG được placeholder hoá"
    # Trang vẫn trong todo pass sau → chạy lại sau nạp credit sẽ OCR tiếp.
    todo, _ = ocr.collect_pending_pages(inbox, "*.png", out, None)
    assert [p.stem for p in todo] == ["page_1"]


# ------------------------------------ B4: waste-token accounting (sổ cost khớp chi thực)

def _usage_exc(msg: str, tin: int, tout: int) -> RuntimeError:
    e = RuntimeError(msg)
    e.usage = {"prompt_tokens": tin, "completion_tokens": tout}
    return e


def test_ocr_page_retry_success_carries_waste_tokens(monkeypatch, tmp_path: Path):
    """Attempt fail (empty content, ĐÃ bill input tokens) rồi thành công → meta mang
    waste_tokens_* để run_batch cộng đủ cost.

    B4 review 2026-07-26: trước fix chỉ usage lần thành công được ghi sổ — token
    của attempt fail biến mất → sổ cost under-count hệ thống."""
    img = tmp_path / "page_1.png"
    img.write_bytes(b"\x89PNG\r\n")
    monkeypatch.setattr(ocr.time, "sleep", lambda *_: None)

    calls = {"n": 0}

    def fake_post_once(*_a, **_k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _usage_exc("empty content (finish_reason=error)", 1000, 5)
        return "nội dung", {"latency_s": 1.0, "usage": {"prompt_tokens": 1100, "completion_tokens": 900}}

    monkeypatch.setattr(ocr, "_post_once", fake_post_once)
    text, meta = ocr.ocr_page("k", "m", img, retries=4)
    assert text == "nội dung"
    assert meta["waste_tokens_in"] == 1000 and meta["waste_tokens_out"] == 5


def test_run_batch_counts_tokens_of_blank_and_dead_pages(monkeypatch, tmp_path: Path):
    """Blank page + dead page đều đã bill input tokens → summary tokens/cost phải tính.

    Trang blank: 1 call phát hiện blank vẫn tốn ~nghìn input tokens (ảnh full-res).
    Trang dead (2 attempt empty): tốn gấp đôi. Trước fix cả hai ghi tokens=0."""
    inbox = tmp_path / "scans"
    inbox.mkdir()
    out = tmp_path / "out"
    (inbox / "page_1.png").write_bytes(b"\x89PNG\r\n")  # sẽ blank
    (inbox / "page_2.png").write_bytes(b"\x89PNG\r\n")  # sẽ dead (2 lần empty)
    monkeypatch.setattr(ocr.time, "sleep", lambda *_: None)

    # Mock ở tầng ocr_page: exception thoát ra mang waste_usage (ocr_page thật gắn
    # attr này từ usage các attempt — xem test retry_success ở trên cho tầng dưới).
    def fake_ocr_page(_key, _model, page_path, **_k):
        if page_path.name == "page_1.png":
            exc = RuntimeError(ocr._BLANK_MARKER)
            exc.waste_usage = (800, 0)  # 1 call phát hiện blank, đã bill 800 in
            raise exc
        raise ocr._dead("empty content (finish_reason=error)", 2000, 10)  # 2 attempt

    monkeypatch.setattr(ocr, "ocr_page", fake_ocr_page)
    summary = ocr.run_batch(
        api_key="k", input_dir=inbox, output_dir=out,
        model="m", workers=1, pattern="*.png",
    )
    assert summary["blank"] == 1 and summary["fail"] == 1
    # 800 (blank) + 2000 (dead) input tokens phải vào summary → cost > 0.
    assert summary["tokens_in"] == 2800, summary
    assert summary["tokens_out"] == 10, summary
    assert summary["cost_usd"] > 0


def test_run_batch_402_circuit_breaker_skips_remaining_pages(monkeypatch, tmp_path: Path):
    """402 ở 1 trang → các trang CHƯA gọi API bị bỏ qua tại chỗ (không bắn call chết).

    B3 review 2026-07-26: credit cạn giữa cuốn, mọi trang còn lại vẫn bắn mỗi trang
    1 call chắc-chắn-402 — hàng trăm trang × N lane wind-down = hàng nghìn call vô
    ích dội API. Circuit breaker: trang đầu dính 402 set event, trang sau skip local.
    Trang skip vẫn tính fail + để TRỐNG (resume sau nạp credit OCR tiếp)."""
    inbox = tmp_path / "scans"
    inbox.mkdir()
    out = tmp_path / "out"
    for i in (1, 2, 3):
        (inbox / f"page_{i}.png").write_bytes(b"\x89PNG\r\n")

    calls = {"n": 0}

    def fail_402(*_a, **_k):
        calls["n"] += 1
        raise RuntimeError("HTTP 402 Payment Required")

    monkeypatch.setattr(ocr, "ocr_page", fail_402)
    summary = ocr.run_batch(
        api_key="k", input_dir=inbox, output_dir=out,
        model="m", workers=1, pattern="*.png",
    )
    # workers=1 tuần tự: trang 1 gọi API dính 402 → trang 2, 3 skip không gọi.
    assert calls["n"] == 1, f"chỉ trang đầu được gọi API, got {calls['n']}"
    assert summary["fail"] == 3, "trang skip vẫn tính fail (chưa OCR xong)"
    # Không trang nào bị placeholder → cả 3 còn nguyên todo cho lần chạy sau.
    todo, _ = ocr.collect_pending_pages(inbox, "*.png", out, None)
    assert [p.stem for p in todo] == ["page_1", "page_2", "page_3"]
    # Message trang skip vẫn chứa "402 Payment" để batch driver grep log nhận diện.
    assert all("402" in err for _, err in summary["failures"])
