"""Tests cho 2 feature P2 (bài học batch 3):

`scan2ebook verify` — validate EPUB bằng zipfile.testzip (shell `unzip -t` loop
đã chứng minh không tin được: CSV comma mis-split + exit code bị nuốt).

`work/cost.json` — sổ cost cộng dồn mọi pass; tổng sổ = chi thực (dòng `cost~$`
cuối log KHÔNG phải tổng — một nhóm 34 cuốn từng lệch ~10% vì thế).
"""

from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path

from scan_to_ebook import cli, cost_ledger, epub_verify


# ---------------------------------------------------------------- helpers

def _make_epub(path: Path, n_files: int = 30, payload: bytes = b"x" * 512) -> None:
    """Zip lành >= TINY_BYTES."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        for i in range(n_files):
            zf.writestr(f"OEBPS/page_{i}.xhtml", payload)
    assert path.stat().st_size >= epub_verify.TINY_BYTES


def _make_book_home(root: Path, slug: str, epub: str = "ok") -> Path:
    """Book-home <root>/<slug>/{scans,dist}. epub: ok|tiny|badzip|missing."""
    home = root / slug
    (home / "scans").mkdir(parents=True)
    dst = home / "dist" / f"{slug}.epub"
    if epub == "ok":
        _make_epub(dst)
    elif epub == "tiny":
        dst.parent.mkdir(parents=True)
        dst.write_bytes(b"PK\x03\x04tiny")
    elif epub == "badzip":
        dst.parent.mkdir(parents=True)
        dst.write_bytes(b"\x00" * (epub_verify.TINY_BYTES + 100))
    # missing: không tạo dist file
    return home


# ---------------------------------------------------------------- verify_paths

def test_verify_classifies_all_four_states(tmp_path):
    root = tmp_path / "books"
    for slug, kind in (("a-ok", "ok"), ("b-tiny", "tiny"),
                       ("c-badzip", "badzip"), ("d-missing", "missing")):
        _make_book_home(root, slug, kind)
    results = epub_verify.verify_paths([root])
    by_label = {r.label: r.status for r in results}
    assert by_label == {"a-ok": "OK", "b-tiny": "TINY",
                        "c-badzip": "BADZIP", "d-missing": "MISSING"}
    counts = epub_verify.summarize(results)
    assert (counts["OK"], counts["TINY"], counts["BADZIP"], counts["MISSING"],
            counts["total"]) == (1, 1, 1, 1, 4)


def test_verify_single_book_home_and_epub_file(tmp_path):
    home = _make_book_home(tmp_path, "solo", "ok")
    # dạng book-home
    (r,) = epub_verify.verify_paths([home])
    assert (r.label, r.status) == ("solo", "OK")
    # dạng file .epub trực tiếp
    (r2,) = epub_verify.verify_paths([home / "dist" / "solo.epub"])
    assert r2.status == "OK"
    # file .epub không tồn tại → MISSING (không exception)
    (r3,) = epub_verify.verify_paths([tmp_path / "nope.epub"])
    assert r3.status == "MISSING"


def test_verify_empty_or_wrong_dir_reports_missing(tmp_path):
    empty = tmp_path / "empty-dir"
    empty.mkdir()
    (r,) = epub_verify.verify_paths([empty])
    assert r.status == "MISSING"


def test_cmd_verify_rc_and_json(tmp_path, capsys):
    root = tmp_path / "books"
    _make_book_home(root, "good", "ok")
    _make_book_home(root, "broken", "badzip")

    rc = cli.cmd_verify(argparse.Namespace(paths=[root], json=True))
    out = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert out["status"] == "partial"
    assert out["counts"]["OK"] == 1 and out["counts"]["BADZIP"] == 1

    # chỉ cuốn lành → rc 0
    rc = cli.cmd_verify(argparse.Namespace(paths=[root / "good"], json=False))
    assert rc == 0
    human = capsys.readouterr().out
    assert "OK=1" in human


# ---------------------------------------------------------------- cost ledger

def test_ledger_accumulates_across_passes(tmp_path):
    work = tmp_path / "work"
    cost_ledger.append_entry(work, "prepass", {"cost_usd": 0.01})
    cost_ledger.append_entry(work, "ocr", {"cost_usd": 1.50, "ok": 100,
                                           "tokens_in": 5, "tokens_out": 9})
    cost_ledger.append_entry(work, "ocr", {"cost_usd": 0.25, "ok": 7})  # pass retry
    assert cost_ledger.total(work) == 1.76
    entries = json.loads((work / "cost.json").read_text(encoding="utf-8"))
    assert [e["stage"] for e in entries] == ["prepass", "ocr", "ocr"]
    assert entries[1]["pages_ok"] == 100


def test_ledger_skips_zero_cost_entries(tmp_path):
    work = tmp_path / "work"
    cost_ledger.append_entry(work, "prepass", {"cost_usd": 0.0})   # cache hit
    cost_ledger.append_entry(work, "ocr", {"cost_usd": 0})         # toàn trang cache
    assert not (work / "cost.json").exists()
    assert cost_ledger.total(work) == 0.0


def test_ledger_survives_corrupt_file(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    (work / "cost.json").write_text("{ rac khong phai json", encoding="utf-8")
    cost_ledger.append_entry(work, "ocr", {"cost_usd": 0.5})
    # sổ mới bắt đầu lại + tang chứng được giữ
    assert cost_ledger.total(work) == 0.5
    assert (work / "cost.json.corrupt-0").exists()


def test_cmd_ocr_appends_ledger_only_in_standard_layout(tmp_path, monkeypatch):
    """output = work/ocr → ghi sổ ở work/; output tuỳ ý → KHÔNG rải cost.json."""
    from scan_to_ebook import ocr as ocr_mod

    def fake_run_batch(**kw):
        Path(kw["output_dir"]).mkdir(parents=True, exist_ok=True)
        return {"ok": 2, "fail": 0, "blank": 0, "skipped": 0, "total": 2,
                "tokens_in": 10, "tokens_out": 20, "cost_usd": 0.02, "failures": []}

    monkeypatch.setattr(ocr_mod, "run_batch", fake_run_batch)
    monkeypatch.setattr(ocr_mod, "require_api_key", lambda: "sk-test")

    scans = tmp_path / "book" / "scans"
    scans.mkdir(parents=True)

    def args(outdir):
        return argparse.Namespace(
            input=scans, output=outdir, model="m", workers=1, pattern="*.png",
            limit=None, max_tokens=12000, lang="vi", context=None,
            no_context=False, dry_run=False, json=False, json_lines=False,
        )

    std_out = tmp_path / "book" / "work" / "ocr"
    assert cli.cmd_ocr(args(std_out)) == 0
    assert cost_ledger.total(tmp_path / "book" / "work") == 0.02

    free_out = tmp_path / "somewhere-else"
    assert cli.cmd_ocr(args(free_out)) == 0
    assert not (free_out.parent / "cost.json").exists()
