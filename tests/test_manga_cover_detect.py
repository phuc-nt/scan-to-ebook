"""Auto-dò bìa manga (`--auto-cover`) — unit + CLI e2e, KHÔNG call mạng.

Mock ở `manga_cover_detect._post_cover_once` (parse/clamp/fallback không cần HTTP).
Ảnh tổng hợp từ conftest.make_jpeg. Không cần key thật ở các test mock; test
key-gate set/clear OPENROUTER_API_KEY qua monkeypatch.
"""

from __future__ import annotations

import pytest

from scan_to_ebook import cli, manga_cover_detect

from conftest import make_jpeg


def _scans(tmp_path, n=5):
    d = tmp_path / "scans"
    d.mkdir()
    for i in range(1, n + 1):
        (d / f"page_{i:03d}.jpg").write_bytes(make_jpeg(800, 1200))
    return d


def _patch_post(monkeypatch, content: str):
    """Giả _post_cover_once trả (content, meta) — không HTTP."""
    def fake(api_key, model, samples, max_tokens):
        return content, {"latency_s": 0.1, "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
    monkeypatch.setattr(manga_cover_detect, "_post_cover_once", fake)


# ----------------------------------------------------------------- parse / detect unit

def test_detect_picks_model_index(tmp_path, monkeypatch):
    """Model trả cover_index=3 → detect trả 3, from_model=True."""
    scans = _scans(tmp_path, 5)
    _patch_post(monkeypatch, '{"cover_index": 3, "reason": "bìa thật ở trang 3"}')
    idx, info = manga_cover_detect.detect_cover_index("k", "m", scans, min_px=400)
    assert idx == 3
    assert info["from_model"] is True
    assert info["chosen"] == 3
    assert "trang 3" in info["reason"]


def test_detect_null_falls_back_to_one(tmp_path, monkeypatch):
    """Model không thấy bìa (null) → fallback 1, from_model=False (vd vol bắt đầu giữa truyện)."""
    scans = _scans(tmp_path, 5)
    _patch_post(monkeypatch, '{"cover_index": null, "reason": "không có bìa trước"}')
    idx, info = manga_cover_detect.detect_cover_index("k", "m", scans, min_px=400)
    assert idx == 1
    assert info["from_model"] is False


def test_detect_out_of_range_falls_back(tmp_path, monkeypatch):
    """index ngoài [1,n_samples] → None → fallback 1 (model bịa số ngoài ảnh đã gửi)."""
    scans = _scans(tmp_path, 3)
    _patch_post(monkeypatch, '{"cover_index": 99, "reason": "x"}')
    idx, info = manga_cover_detect.detect_cover_index("k", "m", scans, min_px=400)
    assert idx == 1
    assert info["from_model"] is False


def test_detect_json_fence_stripped(tmp_path, monkeypatch):
    """Model bọc ```json fence → vẫn parse (reuse context_prepass._strip_json_fence)."""
    scans = _scans(tmp_path, 4)
    _patch_post(monkeypatch, '```json\n{"cover_index": 2, "reason": "ok"}\n```')
    idx, _ = manga_cover_detect.detect_cover_index("k", "m", scans, min_px=400)
    assert idx == 2


def test_detect_only_sends_first_pages(tmp_path, monkeypatch):
    """Chỉ gửi ≤MAX_DETECT_PAGES ảnh đầu → index cao hơn số ảnh gửi = ngoài khoảng."""
    scans = _scans(tmp_path, 20)
    captured = {}

    def fake(api_key, model, samples, max_tokens):
        captured["n"] = len(samples)
        return '{"cover_index": 1, "reason": "ok"}', {"usage": {}}

    monkeypatch.setattr(manga_cover_detect, "_post_cover_once", fake)
    manga_cover_detect.detect_cover_index("k", "m", scans, min_px=400)
    assert captured["n"] == manga_cover_detect.MAX_DETECT_PAGES


# --------------------------------------------------------------------------- CLI e2e

def test_cli_auto_cover_uses_detected_index(tmp_path, monkeypatch):
    """--auto-cover (default cover-index) → epub cover-image = trang model chọn."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    _patch_post(monkeypatch, '{"cover_index": 3, "reason": "bìa"}')
    src = tmp_path / "src"
    src.mkdir()
    for i in range(1, 6):
        (src / f"{i:02d}.jpg").write_bytes(make_jpeg(800, 1200))
    home = tmp_path / "home"
    rc = cli.main(["manga", "ac", "--home", str(home), "--from", str(src), "--auto-cover"])
    assert rc == 0
    di = "di" + "st"
    epub = home / "ac" / di / "ac.epub"
    assert epub.exists()
    import zipfile
    with zipfile.ZipFile(epub) as z:
        opf = z.read("OEBPS/content.opf").decode()
    assert '<meta name="cover" content="img3"/>' in opf


def test_cli_cover_index_overrides_auto_cover(tmp_path, monkeypatch, capsys):
    """--cover-index tay (khác 1) đè --auto-cover: KHÔNG gọi LLM, dùng index tay."""
    called = {"n": 0}

    def fake(*a, **k):
        called["n"] += 1
        return '{"cover_index": 3}', {"usage": {}}

    monkeypatch.setattr(manga_cover_detect, "_post_cover_once", fake)
    src = tmp_path / "src"
    src.mkdir()
    for i in range(1, 6):
        (src / f"{i:02d}.jpg").write_bytes(make_jpeg(800, 1200))
    home = tmp_path / "home"
    rc = cli.main([
        "manga", "ov", "--home", str(home), "--from", str(src),
        "--auto-cover", "--cover-index", "2",
    ])
    assert rc == 0
    assert called["n"] == 0  # LLM KHÔNG được gọi
    assert "đè --auto-cover" in capsys.readouterr().err
    di = "di" + "st"
    with __import__("zipfile").ZipFile(home / "ov" / di / "ov.epub") as z:
        opf = z.read("OEBPS/content.opf").decode()
    assert '<meta name="cover" content="img2"/>' in opf


def test_cli_auto_cover_runtime_error_falls_back_not_crash(tmp_path, monkeypatch, capsys):
    """auto-cover lỗi mạng/parse (RuntimeError) → KHÔNG huỷ build; fallback bìa 1 + WARN."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    def boom(*a, **k):
        raise RuntimeError("HTTP 400 Bad Request: image format illegal")

    monkeypatch.setattr(manga_cover_detect, "_post_cover_once", boom)
    src = tmp_path / "src"
    src.mkdir()
    for i in range(1, 6):
        (src / f"{i:02d}.jpg").write_bytes(make_jpeg(800, 1200))
    home = tmp_path / "home"
    rc = cli.main(["manga", "fb", "--home", str(home), "--from", str(src), "--auto-cover"])
    assert rc == 0  # build vẫn thành công
    assert "auto-cover thất bại" in capsys.readouterr().err
    di = "di" + "st"
    with __import__("zipfile").ZipFile(home / "fb" / di / "fb.epub") as z:
        opf = z.read("OEBPS/content.opf").decode()
    assert '<meta name="cover" content="img1"/>' in opf  # fallback trang 1


def test_cli_auto_cover_without_key_clean_error(tmp_path, monkeypatch):
    """--auto-cover thiếu OPENROUTER_API_KEY → SystemExit sạch (không traceback)."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    # cli.main gọi _load_dotenv() → có thể nạp lại key từ .env repo → vô hiệu hoá để
    # test key-gate đúng trạng thái "không key" (test không được phụ thuộc .env máy).
    monkeypatch.setattr(cli, "_load_dotenv", lambda: None)
    src = tmp_path / "src"
    src.mkdir()
    for i in range(1, 4):
        (src / f"{i:02d}.jpg").write_bytes(make_jpeg(800, 1200))
    home = tmp_path / "home"
    with pytest.raises(SystemExit) as exc:
        cli.main(["manga", "nk", "--home", str(home), "--from", str(src), "--auto-cover"])
    assert "OPENROUTER_API_KEY" in str(exc.value)
