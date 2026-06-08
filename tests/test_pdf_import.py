"""Tests cho PDF support khi import (`init --from book.pdf`).

Driver thực tế: `tests/input/Chuyen Thu Mien Nam - Antoine de Saint-Exupery.pdf`
= calibre/Quartz born-digital PDF, text layer hỏng encoding (pdftotext ra rác) →
phải render từng trang → JPG → OCR. Xem `pipeline._import_pdf` → `pdf_render.
render_pdf_to_images`.

Render là chỗ phụ thuộc nền tảng (pdftoppm/magick/sips). Test routing mock ở lớp
`pdf_render` để chạy trên mọi OS (CI không có poppler). Một test integration dùng
backend THẬT, gate qua `pytest.skip` nếu không có backend. Kiểm:
- PDF route qua render, rename page_NNN.jpg tuần tự, dọn file render thô.
- init --from book.pdf → cmd_init gọi _import_pdf (không nhầm sang _import_images).
- Không backend nào → RuntimeError nêu tên file (KHÔNG silent-skip).
- Suffix không phải .pdf và không phải dir → exit 2.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from scan_to_ebook import cli, pdf_render, pipeline


def _fake_renderer(monkeypatch, n_pages: int = 3):
    """Mock 1 backend khả dụng + render sinh n_pages JPG (prefix render thô).

    Backend thật cần poppler/ImageMagick; mock để test render path chạy trên CI."""
    monkeypatch.setattr(pdf_render, "available_backends", lambda: ["pdftoppm"])

    def fake_render(pdf, out_dir, dpi=pdf_render.DEFAULT_DPI):
        out: list[Path] = []
        for i in range(1, n_pages + 1):
            p = out_dir / f"{pdf_render._RENDER_PREFIX}-{i:03d}.jpg"
            p.write_bytes(b"\xff\xd8\xff\xe0pdfpage" + str(i).encode())
            out.append(p)
        return out

    monkeypatch.setattr(pdf_render, "render_pdf_to_images", fake_render)


# -------------------------------------------------------------- _import_pdf unit

def test_import_pdf_renames_sequential(tmp_path, monkeypatch):
    """PDF render → page_001.jpg..page_NNN.jpg tuần tự; file render thô dọn sạch."""
    _fake_renderer(monkeypatch, n_pages=3)
    dst = tmp_path / "scans"
    dst.mkdir()
    n = pipeline._import_pdf(tmp_path / "book.pdf", dst)
    assert n == 3
    out = sorted(dst.glob("page_*"))
    assert [p.name for p in out] == ["page_001.jpg", "page_002.jpg", "page_003.jpg"]
    # Không còn file render thô (_pdfpage*) sau rename.
    assert not list(dst.glob(f"{pdf_render._RENDER_PREFIX}*"))


def test_rendered_jpgs_orders_past_999_page_boundary(tmp_path):
    """_rendered_jpgs natural-sort theo GIÁ TRỊ số: magick %03d → trang 1000 ra
    `-1000.jpg` (tràn width). Lexical sort xếp `-1000` TRƯỚC `-999` = đảo trang =
    sách hỏng; natural-sort phải giữ đúng 998<999<1000<1001."""
    out = tmp_path / "scans"
    out.mkdir()
    # Tạo theo thứ tự xáo trộn để chứng minh sort (không phải insertion order).
    for i in (1000, 998, 1001, 999):
        (out / f"{pdf_render._RENDER_PREFIX}-{i:03d}.jpg").write_bytes(b"\xff\xd8x")
    got = [p.name for p in pdf_render._rendered_jpgs(out)]
    assert got == [
        f"{pdf_render._RENDER_PREFIX}-998.jpg",
        f"{pdf_render._RENDER_PREFIX}-999.jpg",
        f"{pdf_render._RENDER_PREFIX}-1000.jpg",
        f"{pdf_render._RENDER_PREFIX}-1001.jpg",
    ]


def test_import_pdf_no_backend_raises_naming_file(tmp_path, monkeypatch):
    """Không backend nào (CI trống) → RuntimeError nêu tên PDF, KHÔNG silent."""
    monkeypatch.setattr(pdf_render, "available_backends", lambda: [])
    dst = tmp_path / "scans"
    dst.mkdir()
    pdf = tmp_path / "mybook.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    with pytest.raises(RuntimeError, match="mybook.pdf"):
        pipeline._import_pdf(pdf, dst)


# -------------------------------------------------------------- cmd_init routing

def _init_args(slug, from_dir, home):
    return argparse.Namespace(
        slug=slug, from_dir=from_dir, home=home,
        title=None, author=None, lang="vi", year=None,
    )


def test_init_from_pdf_routes_to_import_pdf(tmp_path, monkeypatch, capsys):
    """init --from book.pdf → cmd_init gọi _import_pdf (PDF branch), in 'Rendered N trang'."""
    _fake_renderer(monkeypatch, n_pages=2)
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    rc = cli.cmd_init(_init_args("mybook", pdf, tmp_path / "home"))
    assert rc == 0
    scans = tmp_path / "home" / "mybook" / "scans"
    assert [p.name for p in sorted(scans.glob("page_*"))] == ["page_001.jpg", "page_002.jpg"]
    assert "Rendered 2 trang PDF" in capsys.readouterr().out


def test_init_from_nonexistent_nonpdf_errors(tmp_path):
    """--from trỏ file không tồn tại + không phải .pdf → exit 2 (không phải dir/PDF)."""
    rc = cli.cmd_init(_init_args("x", tmp_path / "nope.txt", tmp_path / "home"))
    assert rc == 2


def test_init_from_pdf_reimport_guard(tmp_path, monkeypatch, capsys):
    """scans/ đã có page_* → init --from book.pdf bị chặn (tránh page rác), exit 2."""
    _fake_renderer(monkeypatch, n_pages=2)
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    scans = tmp_path / "home" / "mybook" / "scans"
    scans.mkdir(parents=True)
    (scans / "page_001.jpg").write_bytes(b"old")
    rc = cli.cmd_init(_init_args("mybook", pdf, tmp_path / "home"))
    assert rc == 2
    assert "đã có" in capsys.readouterr().err


# ----------------------------------------------------- integration (real backend)

def test_render_real_pdf_if_backend_available(tmp_path):
    """Integration: render PDF thật bằng backend khả dụng. Skip nếu CI trống.

    Dùng test PDF thật nếu có; nếu vắng (gitignored ~180MB) cũng skip — chỉ chạy
    khi cả backend lẫn fixture sẵn sàng."""
    if not pdf_render.available_backends():
        pytest.skip("không có backend render PDF (poppler/ImageMagick)")
    fixture = Path(__file__).parent / "input" / "Chuyen Thu Mien Nam - Antoine de Saint-Exupery.pdf"
    if not fixture.is_file():
        pytest.skip("không có PDF fixture (gitignored)")
    dst = tmp_path / "scans"
    dst.mkdir()
    # Chỉ cần xác nhận render sinh ảnh hợp lệ; full 143 trang chậm → render rồi
    # kiểm trang đầu là JPG thật (magic FFD8).
    pages = pdf_render.render_pdf_to_images(fixture, dst, dpi=72)
    assert len(pages) >= 1
    assert pages[0].read_bytes()[:2] == b"\xff\xd8"  # JPEG magic
