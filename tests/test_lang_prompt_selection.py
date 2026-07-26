"""Tests cho OCR đa ngôn ngữ (vi mặc định | ja sách Nhật dọc RTL).

Phủ:
- ocr.prompt_for_lang + context_prepass.context_prompt_for_lang: chọn prompt theo
  lang, fallback vi cho None/lạ, base vi GIỮ byte-for-byte (verified artifact).
- lang thread xuống _post_once (ocr_page) → đúng base prompt mỗi ngôn ngữ.
- extract_context lưu ctx["lang"]; render_block emit spread guidance PHẢI→TRÁI khi
  ja, TRÁI→PHẢI khi vi (đảo thứ tự đọc đúng cho sách Nhật).

Mock ở `ocr._post_once` và `context_prepass._post_context_once` — không call mạng.
"""

from __future__ import annotations

from scan_to_ebook import context_prepass, ocr


# ----------------------------------------------------- prompt registry selection

def test_prompt_for_lang_vi_is_base_artifact():
    # vi = base PROMPT, byte-for-byte (verified artifact, không được đổi).
    assert ocr.prompt_for_lang("vi") is ocr.PROMPT
    assert ocr.prompt_for_lang(None) is ocr.PROMPT
    assert ocr.prompt_for_lang("") is ocr.PROMPT


def test_prompt_for_lang_ja_distinct():
    assert ocr.prompt_for_lang("ja") is ocr.JA_PROMPT
    assert ocr.JA_PROMPT is not ocr.PROMPT


def test_prompt_for_lang_normalizes_case_whitespace():
    assert ocr.prompt_for_lang(" JA ") is ocr.JA_PROMPT
    assert ocr.prompt_for_lang("Vi") is ocr.PROMPT


def test_prompt_for_lang_unknown_falls_back_to_vi():
    assert ocr.prompt_for_lang("zz") is ocr.PROMPT
    assert ocr.prompt_for_lang("fr") is ocr.PROMPT


def test_ja_prompt_has_no_vietnamese_diacritic_rule():
    # JA prompt KHÔNG mang quy tắc dấu tiếng Việt; có hướng dẫn dọc + RTL + screenshot.
    assert "tiếng Việt" not in ocr.JA_PROMPT
    assert "VERTICAL" in ocr.JA_PROMPT and "RIGHT" in ocr.JA_PROMPT
    assert "SCREENSHOT" in ocr.JA_PROMPT  # bỏ chrome Kindle


def test_context_prompt_for_lang_selection():
    assert context_prepass.context_prompt_for_lang("vi") is context_prepass.CONTEXT_PROMPT
    assert context_prepass.context_prompt_for_lang("ja") is context_prepass.CONTEXT_PROMPT_JA
    assert context_prepass.context_prompt_for_lang(None) is context_prepass.CONTEXT_PROMPT
    assert context_prepass.context_prompt_for_lang("xx") is context_prepass.CONTEXT_PROMPT


# ----------------------------------------------------- lang threading vào _post_once

def _capture_post(monkeypatch):
    captured: dict = {}

    def fake_post(api_key, model, image_b64, mime, max_tokens, prompt_context="", lang=None):
        captured["text"] = ocr.prompt_for_lang(lang) + (
            "\n\n" + prompt_context if prompt_context else ""
        )
        captured["lang"] = lang
        return "md", {"latency_s": 0.1, "usage": {}}

    monkeypatch.setattr(ocr, "_post_once", fake_post)
    return captured


def test_ocr_page_threads_ja_lang(tmp_path, monkeypatch):
    captured = _capture_post(monkeypatch)
    img = tmp_path / "page_001.png"
    img.write_bytes(b"\x89PNG")
    ocr.ocr_page("k", "m", img, lang="ja")
    assert captured["lang"] == "ja"
    assert captured["text"] == ocr.JA_PROMPT  # base prompt Nhật, không phải vi


def test_ocr_page_default_lang_uses_vi(tmp_path, monkeypatch):
    captured = _capture_post(monkeypatch)
    img = tmp_path / "page_001.png"
    img.write_bytes(b"\x89PNG")
    ocr.ocr_page("k", "m", img)  # không truyền lang → vi mặc định
    assert captured["text"] == ocr.PROMPT  # base vi byte-for-byte


# ----------------------------------------------------- render_block RTL theo lang

def _ctx(lang: str, ppi: int = 2) -> dict:
    return {"title": "デッドエンドの思い出", "pages_per_image": ppi, "lang": lang}


def test_render_block_ja_spread_reads_right_to_left():
    block = context_prepass.render_block(_ctx("ja", ppi=2))
    assert "PHẢI→TRÁI" in block
    assert "trang PHẢI trước" in block
    assert "trái→phải" not in block  # KHÔNG dùng thứ tự LTR cho sách Nhật


def test_render_block_vi_spread_reads_left_to_right():
    block = context_prepass.render_block(_ctx("vi", ppi=2))
    assert "trái→phải" in block
    assert "PHẢI→TRÁI" not in block


def test_render_block_single_page_no_spread_guidance_either_lang():
    # ppi=1 → không emit spread guidance, bất kể lang.
    for lang in ("vi", "ja"):
        block = context_prepass.render_block(_ctx(lang, ppi=1))
        assert "TRANG ĐÔI" not in block


def test_render_block_missing_lang_defaults_vi():
    # ctx cũ (trước feature) không có field lang → coi như vi (LTR).
    block = context_prepass.render_block({"title": "X", "pages_per_image": 2})
    assert "trái→phải" in block
