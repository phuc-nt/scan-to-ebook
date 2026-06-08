"""Tests cho context pre-pass (context_prepass.py). API-mocked → CI-safe.

Cover: chọn ảnh mẫu (7+4+4=15), parse JSON strict + fail→abort, resume cache
(không gọi API), key mới (translator/pages_per_image/table_of_contents) parse +
render, spread guidance CONDITIONAL trong render_block (emit khi ppi>=2, không khi
=1), base PROMPT giữ byte-for-byte khi thread prompt_context, cost accounting.

Mock ở `context_prepass._post_context_once` (parse/render không cần HTTP) và
`ocr._post_once` (threading test). Không call mạng, không cần key thật.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scan_to_ebook import context_prepass, ocr


# ---------------------------------------------------------------- helpers

def _fake_pages(tmp_path: Path, n: int) -> list[Path]:
    """Tạo n file page_NNN.png, trả list natural-sorted."""
    for i in range(1, n + 1):
        (tmp_path / f"page_{i:03d}.png").write_bytes(b"\x89PNG\r\n")
    return sorted(tmp_path.glob("page_*.png"), key=ocr.natural_sort_key)


def _valid_ctx(pages_per_image: int = 2) -> dict:
    return {
        "title": "Tác Phẩm Aragông",
        "author": "Aragon",
        "translator": "Phùng Văn Tửu",
        "publisher": "NXB Giáo Dục",
        "year": "1998",
        "pages_per_image": pages_per_image,
        "cover_page": "page_001.png",
        "table_of_contents": [{"title": "Chương 1", "page": 12}],
        "proper_names": [{"seen": "Miraben", "canonical": "Miraben"}],
        "terminology": ["nhân-loại"],
        "layout_notes": "1 cột/trang, trang đôi",
        "footnote_convention": "số nhỏ trên cao",
        "ocr_pitfalls": ["phiên âm tên Pháp"],
    }


def _valid_ctx_json(pages_per_image: int = 2) -> str:
    return json.dumps(_valid_ctx(pages_per_image), ensure_ascii=False)


# ---------------------------------------------------------------- sample selection

def test_select_sample_152():
    pages = [Path(f"page_{i:03d}.png") for i in range(1, 153)]
    got = context_prepass.select_sample_pages(pages)
    assert len(got) == 15
    assert got[:7] == pages[0:7]
    assert got[7:11] == pages[74:78]  # mid_start = (152-4)//2 = 74
    assert got[11:15] == pages[148:152]


def test_select_sample_always_includes_first_page():
    """BẤT BIẾN cover-detect: page_001 (bìa thường ở đây) LUÔN trong mẫu, mọi cỡ sách."""
    for n in (3, 15, 16, 100, 500):
        pages = [Path(f"page_{i:03d}.png") for i in range(1, n + 1)]
        got = context_prepass.select_sample_pages(pages)
        assert pages[0] in got, f"page_001 phải nằm trong sample (n={n})"


def test_select_sample_exactly_15():
    pages = [Path(f"page_{i:03d}.png") for i in range(1, 16)]
    got = context_prepass.select_sample_pages(pages)
    assert got == pages


def test_select_sample_under_15_no_dup():
    pages = [Path(f"page_{i:03d}.png") for i in range(1, 13)]
    got = context_prepass.select_sample_pages(pages)
    assert got == pages
    assert len(got) == len(set(got))


def test_select_sample_tiny():
    pages = [Path("page_001.png"), Path("page_002.png"), Path("page_003.png")]
    assert context_prepass.select_sample_pages(pages) == pages


# ---------------------------------------------------------------- json fence

def test_strip_json_fence_fenced():
    raw = '```json\n{"title": "X"}\n```'
    assert json.loads(context_prepass._strip_json_fence(raw)) == {"title": "X"}


def test_strip_json_fence_prose_wrapped():
    raw = 'Đây là kết quả: {"title": "X"} hết.'
    assert json.loads(context_prepass._strip_json_fence(raw)) == {"title": "X"}


# ---------------------------------------------------------------- extract_context

def _patch_post(monkeypatch, content: str):
    def fake(api_key, model, sample_b64s, max_tokens):
        return content, {"latency_s": 0.1, "usage": {"prompt_tokens": 100, "completion_tokens": 50}}
    monkeypatch.setattr(context_prepass, "_post_context_once", fake)


def test_extract_context_success(tmp_path, monkeypatch):
    _patch_post(monkeypatch, _valid_ctx_json())
    pages = _fake_pages(tmp_path, 3)
    ctx, meta = context_prepass.extract_context("k", "m", pages, 4000)
    assert ctx["title"] == "Tác Phẩm Aragông"
    assert "usage" in meta
    assert ctx["_generated_by"] == "m"


def test_extract_context_new_keys(tmp_path, monkeypatch):
    _patch_post(monkeypatch, _valid_ctx_json(pages_per_image=2))
    pages = _fake_pages(tmp_path, 3)
    ctx, _ = context_prepass.extract_context("k", "m", pages, 4000)
    assert ctx["translator"] == "Phùng Văn Tửu"
    assert isinstance(ctx["pages_per_image"], int) and ctx["pages_per_image"] == 2
    assert isinstance(ctx["table_of_contents"], list)


def test_extract_context_cover_page_parsed(tmp_path, monkeypatch):
    """cover_page (field mới) parse được + giữ trong ctx → pipeline dùng làm bìa epub."""
    _patch_post(monkeypatch, _valid_ctx_json())
    pages = _fake_pages(tmp_path, 3)
    ctx, _ = context_prepass.extract_context("k", "m", pages, 4000)
    assert ctx["cover_page"] == "page_001.png"


def test_extract_context_attaches_filename_labels(tmp_path, monkeypatch):
    """Mỗi sample phải kèm (b64, mime, name) → _post_context_once nhận tên file thật.

    LLM cần nhãn tên để trả cover_page; assert sample_b64s mang đúng filename."""
    captured = {}

    def fake(api_key, model, sample_b64s, max_tokens):
        captured["samples"] = sample_b64s
        return _valid_ctx_json(), {"usage": {}}

    monkeypatch.setattr(context_prepass, "_post_context_once", fake)
    pages = _fake_pages(tmp_path, 3)
    context_prepass.extract_context("k", "m", pages, 4000)
    samples = captured["samples"]
    assert len(samples) == 3
    # mỗi phần tử là (b64, mime, name); name = filename gốc.
    assert all(len(s) == 3 for s in samples)
    assert [s[2] for s in samples] == ["page_001.png", "page_002.png", "page_003.png"]


def test_extract_context_parse_fail_raises(tmp_path, monkeypatch):
    _patch_post(monkeypatch, "not json at all")
    pages = _fake_pages(tmp_path, 3)
    with pytest.raises(RuntimeError):
        context_prepass.extract_context("k", "m", pages, 4000)


def test_extract_context_non_dict_raises(tmp_path, monkeypatch):
    _patch_post(monkeypatch, "[1, 2, 3]")
    pages = _fake_pages(tmp_path, 3)
    with pytest.raises(RuntimeError):
        context_prepass.extract_context("k", "m", pages, 4000)


def test_extract_context_retries_on_parse_fail(tmp_path, monkeypatch):
    """Body JSON cắt dở (transient) ở lần 1 → retry → lần 2 valid → success (M1).

    Trước fix M1, json.loads nằm NGOÀI retry loop nên parse-fail raise luôn."""
    calls = {"n": 0}

    def fake(api_key, model, sample_b64s, max_tokens):
        calls["n"] += 1
        if calls["n"] == 1:
            return '{"title": "X", "pages_per', {"usage": {}}  # JSON cắt dở
        return _valid_ctx_json(), {"usage": {}}

    monkeypatch.setattr(context_prepass, "_post_context_once", fake)
    monkeypatch.setattr(context_prepass.time, "sleep", lambda *a: None)  # no real backoff
    pages = _fake_pages(tmp_path, 3)
    ctx, _ = context_prepass.extract_context("k", "m", pages, 4000)
    assert ctx["title"] == "Tác Phẩm Aragông"
    assert calls["n"] == 2  # đã retry đúng 1 lần


def test_extract_context_parse_fail_exhausts_retries(tmp_path, monkeypatch):
    """Parse-fail liên tục → hết retry → raise (không treo vô hạn)."""
    calls = {"n": 0}

    def fake(api_key, model, sample_b64s, max_tokens):
        calls["n"] += 1
        return "{broken", {"usage": {}}

    monkeypatch.setattr(context_prepass, "_post_context_once", fake)
    monkeypatch.setattr(context_prepass.time, "sleep", lambda *a: None)
    pages = _fake_pages(tmp_path, 3)
    with pytest.raises(RuntimeError):
        context_prepass.extract_context("k", "m", pages, 4000)
    assert calls["n"] == 3  # 1 + 2 retries


# ---------------------------------------------------------------- render_block

def test_render_block_skips_empty():
    ctx = {"title": "X", "author": None, "proper_names": [{"seen": "A", "canonical": "B"}]}
    block = context_prepass.render_block(ctx)
    assert "X" in block
    assert "A→B" in block
    assert "Footnote:" not in block  # empty field omitted


def test_render_block_new_keys():
    block = context_prepass.render_block(_valid_ctx(pages_per_image=2))
    assert "Số trang mỗi ảnh: 2" in block
    assert "dịch: Phùng Văn Tửu" in block
    assert "MỤC LỤC" in block
    # TOC absent when empty
    ctx2 = _valid_ctx()
    ctx2["table_of_contents"] = []
    assert "MỤC LỤC" not in context_prepass.render_block(ctx2)


def test_render_block_caps_proper_names():
    ctx = _valid_ctx()
    ctx["proper_names"] = [{"seen": f"n{i}", "canonical": f"c{i}"} for i in range(60)]
    block = context_prepass.render_block(ctx)
    name_line = [ln for ln in block.splitlines() if ln.startswith("Tên riêng")][0]
    assert name_line.count("→") == 40


def test_render_block_cover_guidance_always_present():
    """Hướng dẫn cấm heading trang bìa/tựa (đầu) + colophon (cuối) LUÔN có (mọi sách)
    → pandoc --toc không nhặt chữ trang trí vào mục lục. Không phụ thuộc field nào
    trong context.json."""
    for ctx in (_valid_ctx(), {"title": "X"}, {}):
        block = context_prepass.render_block(ctx)
        assert "TRANG BÌA/TỰA ĐỀ" in block
        assert "COLOPHON" in block  # bao cả trang xuất bản cuối sách
        assert "KHÔNG dùng `## `" in block


def test_render_block_heading_consistency_always_present():
    """Rule cấp-heading nhất quán (cả tựa gốc + tựa dịch = `## `) LUÔN có mọi sách →
    --toc-depth=2 nhặt đủ cả hai, tránh tựa dịch lỡ `### ` rớt TOC (bug La Fontaine)."""
    for ctx in (_valid_ctx(), {"title": "X"}, {}):
        block = context_prepass.render_block(ctx)
        assert "CẤP HEADING NHẤT QUÁN" in block
        assert "SONG NGỮ" in block
        assert "rớt TOC" in block


def test_render_block_verse_break_when_verse():
    ctx = _valid_ctx()
    ctx["content_type"] = "verse"
    block = context_prepass.render_block(ctx)
    assert "THƠ (xuống dòng từng câu)" in block
    assert "HAI DẤU CÁCH" in block


def test_render_block_verse_break_when_mixed():
    ctx = _valid_ctx()
    ctx["content_type"] = "mixed"
    assert "THƠ (xuống dòng từng câu)" in context_prepass.render_block(ctx)


def test_render_block_no_verse_break_when_prose():
    ctx = _valid_ctx()
    ctx["content_type"] = "prose"
    assert "THƠ (xuống dòng từng câu)" not in context_prepass.render_block(ctx)


def test_render_block_no_verse_break_when_missing():
    """content_type vắng (sách cũ / không detect) → không ép xuống dòng thơ (an toàn
    cho văn xuôi)."""
    ctx = _valid_ctx()
    ctx.pop("content_type", None)
    assert "THƠ (xuống dòng từng câu)" not in context_prepass.render_block(ctx)


def test_render_block_spread_when_2():
    block = context_prepass.render_block(_valid_ctx(pages_per_image=2))
    assert "ẢNH TRANG ĐÔI" in block
    assert "trang trái" in block and "trang phải" in block


def test_render_block_no_spread_when_1():
    assert "ẢNH TRANG ĐÔI" not in context_prepass.render_block(_valid_ctx(pages_per_image=1))


def test_render_block_no_spread_when_missing():
    ctx = _valid_ctx()
    del ctx["pages_per_image"]
    assert "ẢNH TRANG ĐÔI" not in context_prepass.render_block(ctx)


def test_render_block_pages_per_image_non_numeric_falls_back():
    """context.json sửa tay thành chuỗi phi số → fallback 1 trang/ảnh, KHÔNG crash."""
    ctx = _valid_ctx()
    ctx["pages_per_image"] = "hai"  # giá trị người sửa tay không hợp lệ
    block = context_prepass.render_block(ctx)  # không raise
    assert "Số trang mỗi ảnh: 1" in block
    assert "ẢNH TRANG ĐÔI" not in block  # ppi=1 → không emit spread


# ---------------------------------------------------------------- save / load

def test_save_and_load_roundtrip(tmp_path):
    ctx = _valid_ctx()
    block = context_prepass.render_block(ctx)
    json_path, md_path = context_prepass.save_context(tmp_path, ctx, block)
    assert json_path.exists() and md_path.exists()
    loaded = context_prepass.load_context(tmp_path)
    assert loaded["translator"] == ctx["translator"]
    assert loaded["pages_per_image"] == ctx["pages_per_image"]
    assert loaded["table_of_contents"] == ctx["table_of_contents"]


def test_load_context_missing_returns_none(tmp_path):
    assert context_prepass.load_context(tmp_path) is None


# ---------------------------------------------------------------- run_prepass

def test_run_prepass_cache_hit_no_api(tmp_path, monkeypatch):
    ctx = _valid_ctx()
    context_prepass.save_context(tmp_path, ctx, context_prepass.render_block(ctx))
    _fake_pages(tmp_path, 3)

    calls = []
    monkeypatch.setattr(
        context_prepass, "_post_context_once",
        lambda *a, **k: calls.append(1) or ("x", {}),
    )
    res = context_prepass.run_prepass("k", "m", tmp_path, "*.png")
    assert res["from_cache"] is True
    assert res["cost_usd"] == 0.0
    assert calls == []  # API NOT called
    assert "ẢNH TRANG ĐÔI" in res["block"]  # re-derived from cached ctx


def test_run_prepass_miss_calls_api(tmp_path, monkeypatch):
    _fake_pages(tmp_path, 20)
    _patch_post(monkeypatch, _valid_ctx_json())
    res = context_prepass.run_prepass("k", "m", tmp_path, "*.png")
    assert res["from_cache"] is False
    assert (tmp_path / "context.json").exists()
    assert (tmp_path / "context.md").exists()


def test_run_prepass_no_images_raises(tmp_path):
    with pytest.raises(RuntimeError):
        context_prepass.run_prepass("k", "m", tmp_path, "*.png")


def test_run_prepass_splits_read_and_cache_dirs(tmp_path, monkeypatch):
    """Layout mới: ĐỌC ảnh từ scans/, GHI cache context.{json,md} vào work/.

    Khoá fix tách zone — cache KHÔNG nằm cạnh nguồn (tránh clean-room wipe nhầm)."""
    scans = tmp_path / "scans"
    work = tmp_path / "work"
    scans.mkdir()
    work.mkdir()
    _fake_pages(scans, 20)
    _patch_post(monkeypatch, _valid_ctx_json())
    res = context_prepass.run_prepass("k", "m", scans, "*.png", out_dir=work)
    assert res["from_cache"] is False
    # cache ghi vào work/, KHÔNG vào scans/ (nguồn sạch).
    assert (work / "context.json").exists()
    assert not (scans / "context.json").exists()


def test_run_prepass_cache_hit_from_out_dir(tmp_path, monkeypatch):
    """Cache hit đọc context.json từ out_dir (work/), không gọi API."""
    scans = tmp_path / "scans"
    work = tmp_path / "work"
    scans.mkdir()
    work.mkdir()
    _fake_pages(scans, 3)
    context_prepass.save_context(work, _valid_ctx(), "blk")
    calls = []
    monkeypatch.setattr(
        context_prepass, "_post_context_once",
        lambda *a, **k: calls.append(1) or ("x", {}),
    )
    res = context_prepass.run_prepass("k", "m", scans, "*.png", out_dir=work)
    assert res["from_cache"] is True and calls == []


def test_cost_accounting(tmp_path, monkeypatch):
    _fake_pages(tmp_path, 20)

    def fake(api_key, model, sample_b64s, max_tokens):
        return _valid_ctx_json(), {
            "latency_s": 0.1,
            "usage": {"prompt_tokens": 1_000_000, "completion_tokens": 100_000},
        }
    monkeypatch.setattr(context_prepass, "_post_context_once", fake)
    res = context_prepass.run_prepass("k", "m", tmp_path, "*.png")
    assert res["cost_usd"] == pytest.approx(2.5 + 1.0)  # 1M in*2.5 + 0.1M out*10


# ---------------------------------------------------------------- prompt threading

def test_prompt_context_threading(tmp_path, monkeypatch):
    """ocr_page(prompt_context='BLOCK') → text = PROMPT + '\\n\\n' + BLOCK, base byte-for-byte."""
    captured = {}

    def fake_post(api_key, model, image_b64, mime, max_tokens, prompt_context=""):
        text = ocr.PROMPT + ("\n\n" + prompt_context if prompt_context else "")
        captured["text"] = text
        return "md", {"latency_s": 0.1, "usage": {}}

    monkeypatch.setattr(ocr, "_post_once", fake_post)
    img = tmp_path / "page_001.png"
    img.write_bytes(b"\x89PNG")
    ocr.ocr_page("k", "m", img, prompt_context="BLOCK")
    assert captured["text"] == ocr.PROMPT + "\n\n" + "BLOCK"
    assert ocr.PROMPT in captured["text"]  # base PROMPT present byte-for-byte


def test_prompt_context_empty_unchanged(tmp_path, monkeypatch):
    captured = {}

    def fake_post(api_key, model, image_b64, mime, max_tokens, prompt_context=""):
        captured["text"] = ocr.PROMPT + ("\n\n" + prompt_context if prompt_context else "")
        return "md", {"latency_s": 0.1, "usage": {}}

    monkeypatch.setattr(ocr, "_post_once", fake_post)
    img = tmp_path / "page_001.png"
    img.write_bytes(b"\x89PNG")
    ocr.ocr_page("k", "m", img)
    assert captured["text"] == ocr.PROMPT  # base PROMPT unchanged
