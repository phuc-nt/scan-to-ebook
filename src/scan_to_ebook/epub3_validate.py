"""Validator cấu trúc EPUB3 — kiểm tra stdlib, không cần epubcheck/JRE.

7 kiểm tra cơ bản (structural gate):
  1. Entry đầu tiên trong zip == "mimetype", stored (ZIP_STORED), nội dung đúng.
  2. Zip mở được, testzip() trả None (không CRC lỗi).
  3. META-INF/container.xml có mặt và trỏ tới OPF.
  4. OPF parse được bằng xml.etree.ElementTree.
  5. Mỗi <item href> trong manifest tồn tại trong zip.
  6. Mỗi <itemref idref> trong spine giải được ra một manifest id.
  7. Có ít nhất 1 manifest item properties="cover-image".
"""

from __future__ import annotations

import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

_OPF_NS = "http://www.idpf.org/2007/opf"


def validate_epub3(path: Path | str) -> dict:
    """Kiểm tra cấu trúc EPUB3. Trả {valid: bool, errors: list[str]}.

    Không raise — caller quyết định exit code từ `valid`.
    """
    path = Path(path)
    errors: list[str] = []

    # Check 1: mimetype entry — phải là entry đầu tiên, stored, đúng nội dung.
    try:
        with zipfile.ZipFile(path, "r") as z:
            names = z.namelist()
            if not names or names[0] != "mimetype":
                errors.append("mimetype không phải entry đầu tiên")
            else:
                info = z.getinfo("mimetype")
                if info.compress_type != zipfile.ZIP_STORED:
                    errors.append("mimetype bị nén (phải ZIP_STORED)")
                content = z.read("mimetype").decode("ascii", errors="replace")
                if content != "application/epub+zip":
                    errors.append(f"mimetype sai nội dung: {content!r}")
    except zipfile.BadZipFile as exc:
        errors.append(f"zip không mở được: {exc}")
        return {"valid": False, "errors": errors}

    # Check 2: testzip() — phát hiện CRC lỗi.
    try:
        with zipfile.ZipFile(path, "r") as z:
            bad = z.testzip()
            if bad is not None:
                errors.append(f"CRC lỗi ở entry: {bad}")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"testzip thất bại: {exc}")

    # Check 3: META-INF/container.xml có mặt và trỏ tới OPF.
    opf_path: str | None = None
    try:
        with zipfile.ZipFile(path, "r") as z:
            if "META-INF/container.xml" not in z.namelist():
                errors.append("META-INF/container.xml không tồn tại")
            else:
                root = ET.fromstring(z.read("META-INF/container.xml").decode("utf-8"))
                ns = "urn:oasis:names:tc:opendocument:xmlns:container"
                rf = root.find(f".//{{{ns}}}rootfile")
                if rf is None:
                    errors.append("container.xml không có <rootfile>")
                else:
                    opf_path = rf.get("full-path")
                    if not opf_path:
                        errors.append("container.xml rootfile thiếu full-path")
    except ET.ParseError as exc:
        errors.append(f"container.xml parse lỗi: {exc}")

    if opf_path is None:
        return {"valid": len(errors) == 0, "errors": errors}

    # Check 4-7: dựa vào OPF.
    try:
        with zipfile.ZipFile(path, "r") as z:
            all_names = set(z.namelist())
            if opf_path not in all_names:
                errors.append(f"OPF không tồn tại trong zip: {opf_path}")
                return {"valid": False, "errors": errors}

            # Check 4: OPF parse được.
            try:
                opf_root = ET.fromstring(z.read(opf_path).decode("utf-8"))
            except ET.ParseError as exc:
                errors.append(f"OPF parse lỗi: {exc}")
                return {"valid": len(errors) == 0, "errors": errors}

            # OPF base dir — href trong manifest tương đối so với thư mục OPF.
            opf_dir = opf_path.rsplit("/", 1)[0] + "/" if "/" in opf_path else ""

            manifest_el = opf_root.find(f"{{{_OPF_NS}}}manifest")
            spine_el = opf_root.find(f"{{{_OPF_NS}}}spine")

            # Check 5: mỗi manifest <item href> tồn tại trong zip.
            manifest_ids: set[str] = set()
            has_cover_image = False
            if manifest_el is not None:
                for item in manifest_el.findall(f"{{{_OPF_NS}}}item"):
                    item_id = item.get("id", "")
                    href = item.get("href", "")
                    props = item.get("properties", "")
                    manifest_ids.add(item_id)
                    if "cover-image" in props:
                        has_cover_image = True
                    full = opf_dir + href
                    if full not in all_names:
                        errors.append(f"manifest href không tồn tại: {href}")
            else:
                errors.append("OPF thiếu <manifest>")

            # Check 6: mỗi spine <itemref idref> giải được ra manifest id.
            if spine_el is not None:
                for itemref in spine_el.findall(f"{{{_OPF_NS}}}itemref"):
                    idref = itemref.get("idref", "")
                    if idref not in manifest_ids:
                        errors.append(f"spine idref không có trong manifest: {idref}")
            else:
                errors.append("OPF thiếu <spine>")

            # Check 7: cover-image manifest item.
            if not has_cover_image:
                errors.append("manifest không có item properties='cover-image'")

    except Exception as exc:  # noqa: BLE001
        errors.append(f"lỗi đọc zip khi validate: {exc}")

    return {"valid": len(errors) == 0, "errors": errors}
