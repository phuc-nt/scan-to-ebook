"""Tests cho P0/P1: blank-page auto-placeholder, atomic write, footnote
renumber cross-page, và heading variety (Hồi + số viết chữ).

Bối cảnh:
- P0.2: trang trống thật (empty content + finish_reason=stop) → ghi
  placeholder `<!-- blank page -->`, KHÔNG tính fail, KHÔNG retry.
- P2.1: ghi .md atomic (tmp + os.replace) → không để lại file nửa-ghi khi
  bị ngắt giữa chừng (resume check size>0 sẽ skip nhầm file corrupt).
- P1: mỗi page OCR đánh footnote độc lập từ [^1]; merge phải shift theo
  counter để không đụng số. Heading nhận thêm Hồi/Quyển + số viết chữ.
"""

from __future__ import annotations

from pathlib import Path

from scan_to_ebook import ocr, post_process


# ----------------------------------------------------------- P2.1: atomic write

def test_atomic_write_creates_file_no_tmp_left(tmp_path: Path):
    dst = tmp_path / "page_1.md"
    ocr._atomic_write(dst, "nội dung")
    assert dst.read_text(encoding="utf-8") == "nội dung"
    # tmp phải đã được rename đi, không còn sót
    assert not (tmp_path / "page_1.md.tmp").exists()


def test_atomic_write_overwrites(tmp_path: Path):
    dst = tmp_path / "page_1.md"
    dst.write_text("cũ", encoding="utf-8")
    ocr._atomic_write(dst, "mới")
    assert dst.read_text(encoding="utf-8") == "mới"


# --------------------------------------------------- P0.2: blank classification

def test_blank_marker_not_transient():
    """Blank (giấy trống) KHÔNG nằm trong transient set → không retry."""
    assert not ocr._is_transient(ocr._BLANK_MARKER)


def test_empty_content_is_transient():
    """empty content với finish_reason khác stop = lỗi tạm → retry."""
    assert ocr._is_transient("empty content (finish_reason=None)")


# --------------------------------------------- P1: footnote renumber cross-page

def test_renumber_footnotes_offset_zero_noop():
    t = "Câu[^1].\n\n[^1]: ghi chú"
    out, mx = post_process.renumber_footnotes(t, 0)
    assert out == t
    assert mx == 1


def test_renumber_footnotes_shifts_ref_and_def():
    t = "A[^1] B[^2].\n\n[^1]: x\n[^2]: y"
    out, mx = post_process.renumber_footnotes(t, 5)
    assert "[^6]" in out and "[^7]" in out
    assert "[^1]" not in out and "[^2]" not in out
    assert mx == 2


def test_renumber_footnotes_skips_code_fence():
    """[^N] trong fenced code block là literal, không được shift."""
    t = "Văn bản[^1].\n\n```\narr[^1] = x\n```\n\n[^1]: chú"
    out, mx = post_process.renumber_footnotes(t, 5)
    assert "arr[^1] = x" in out  # trong code block: giữ nguyên
    assert "Văn bản[^6]" in out  # ngoài code block: shift
    assert mx == 1


def test_merge_pages_footnotes_unique_across_pages(tmp_path: Path):
    """2 page cùng dùng [^1] → sau merge phải là [^1] và [^2], không trùng."""
    ocr_dir = tmp_path / "ocr"
    ocr_dir.mkdir()
    (ocr_dir / "page_1.md").write_text("Trang một[^1].\n\n[^1]: chú 1", encoding="utf-8")
    (ocr_dir / "page_2.md").write_text("Trang hai[^1].\n\n[^1]: chú 2", encoding="utf-8")

    out = tmp_path / "book.md"
    stats = post_process.merge_pages(input_dir=ocr_dir, output_path=out, title="T")
    body = out.read_text(encoding="utf-8")

    assert stats["footnotes"] == 2
    # page1 giữ [^1], page2 shift thành [^2]
    assert body.count("[^1]:") == 1
    assert body.count("[^2]:") == 1
    assert "chú 1" in body and "chú 2" in body


# --------------------------------------------------------- P1: heading variety

def _is_heading(line: str) -> bool:
    return post_process._is_chapter_heading(line)


def test_heading_matches_variety():
    # Heading thật: ĐỨNG RIÊNG (ngắn), hoặc kèm tiêu đề IN HOA sau dấu câu.
    for s in ["Chương I", "CHƯƠNG 5", "Hồi thứ nhất", "Phần thứ hai",
              "Quyển II", "Chương mười", "HỒI thứ ba",
              "CHƯƠNG I: NGÀY TRỞ VỀ", "Phần thứ nhất - MỞ ĐẦU"]:
        assert _is_heading(s), f"phải nhận diện heading: {s!r}"


def test_heading_no_false_positive():
    for s in ["Phần lớn dân chúng", "Hồi đó tôi còn nhỏ",
              "Chương trình nghị sự", "Thiên nhiên tươi đẹp"]:
        assert not _is_heading(s), f"không được nhận nhầm: {s!r}"


def test_heading_no_false_positive_prose_starting_with_keyword():
    """REGRESSION: văn xuôi MỞ bằng 'Phần thứ <chữ>' + tiếp chữ thường KHÔNG là heading.

    Bug thật (aragong-q1-quarter): regex cũ có đuôi `.*` nuốt cả đoạn ~400 chữ →
    'Phần thứ hai của tiểu thuyết...' thành # h1 (sai + split chương giả trong epub).
    Chặn bằng: đuôi sau số phải hết-dòng / dấu-câu+IN-HOA, và cả dòng phải ngắn.
    """
    prose = [
        "Phần thứ hai của tiểu thuyết kể chuyện Catơrin Ximônitzê (Catherine "
        "Simonidzé), bố là trùm tư sản dầu lửa ở Giêorgi, ruồng bỏ vợ.",
        "Phần cuối cùng với hình ảnh Clara Zetkin ở Đại hội Balơ họp trong một "
        "tòa nhà thờ giữa những hồi chuông ngân vang báo hiệu tương lai.",
        "Chương ba mở ra một khung cảnh hoàn toàn khác với những gì đã kể.",
    ]
    for s in prose:
        assert not _is_heading(s), f"đoạn văn KHÔNG được thành heading: {s[:50]!r}"


def test_upgrade_keeps_prose_starting_with_keyword_as_paragraph():
    """End-to-end: upgrade_chapter_headings KHÔNG đụng đoạn văn mở bằng 'Phần thứ hai'."""
    text = (
        "Phần thứ hai của tiểu thuyết kể chuyện Catơrin, bố là trùm tư sản dầu "
        "lửa ở Giêorgi, ruồng bỏ vợ nên mấy mẹ con sang sống ở Pari."
    )
    out = post_process.upgrade_chapter_headings(text)
    assert not out.lstrip().startswith("#"), "đoạn văn bị nâng nhầm thành heading"
    assert out == text  # giữ nguyên 100%


def test_upgrade_chapter_heading_promotes_to_h1():
    text = "Hồi thứ nhất\n\nNội dung mở đầu."
    out = post_process.upgrade_chapter_headings(text)
    assert out.startswith("# Hồi thứ nhất")


def test_atx_heading_missing_space_normalized():
    """`##幽霊の家` (model CJH bỏ space ASCII) → `## 幽霊の家` để pandoc nhận heading.

    CommonMark bắt buộc space sau #; thiếu thì render thành text, mất split point + TOC.
    Gặp thật ở OCR sách Nhật (デッドエンドの思い出 page_004)."""
    assert post_process._normalize_atx_heading("##幽霊の家") == "## 幽霊の家"
    assert post_process._normalize_atx_heading("#見出し") == "# 見出し"
    assert post_process._normalize_atx_heading("##### x") == "##### x"  # đã có space → nguyên
    assert post_process._normalize_atx_heading("普通の文。") == "普通の文。"  # văn xuôi không đụng


def test_upgrade_keeps_cjk_h2_heading_with_normalized_space():
    # h2 không phải keyword chương VN → giữ h2 nhưng đã chuẩn-hoá space (không rớt về line gốc).
    out = post_process.upgrade_chapter_headings("##幽霊の家\n\n本文。")
    assert "## 幽霊の家" in out
    assert "##幽霊の家" not in out
