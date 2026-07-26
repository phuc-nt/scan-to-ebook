#!/usr/bin/env python3
"""Batch OCR nhiều sách song song bằng N lane — driver tham số hoá, stdlib thuần.

Thiết kế (đã verify trên đợt thực tế ~180 cuốn / 41k trang):
- N lane cùng rút sách từ MỘT queue chung → mỗi cuốn chỉ 1 lane xử lý, không
  bao giờ 2 lane đụng cùng file. 8 lane × 24 worker (192 concurrent / 1 key)
  chạy ổn không bị rate-limit đáng kể với model mặc định.
- Mỗi cuốn: `scan2ebook init --from <pdf> --dpi N` (bỏ qua nếu đã có scans) →
  `scan2ebook all --yes` tối đa 2 pass, giữa 2 pass chạy `scan2ebook ocr` retry
  (tự nạp context cache). Pass `all` cuối tự bỏ context pre-pass khi 0 trang cần
  OCR → sách bị provider moderation chặn ảnh mẫu vẫn tự ra EPUB.
- HTTP 402 (hết credit) ở bất kỳ lane nào → ngừng nhận sách mới (sách đang chạy
  kết thúc tự nhiên). 402 KHÔNG phải lỗi sách: nạp credit rồi chạy lại, OCR
  cache làm trang xong = $0.
- Kết quả cuối phân loại: DONE | WARN(no-epub) | WARN(init-fail) | STOP(402),
  kèm wall-clock, tổng thời gian tuần tự, speedup, số lần 429.

CSV cần cột: `slug,title,authors,pdf_path` (cột khác bỏ qua; field chứa dấu
phẩy phải quote chuẩn CSV). Ví dụ:

    slug,title,authors,pdf_path
    sach-mot,"Sách Một: Tập I","Tác Giả A",/path/to/sach-mot.pdf

Chạy:
    OPENROUTER_API_KEY=sk-... python3 tools/batch_ocr_runner.py \\
        --csv group1.csv --home /data/books --log-dir /data/logs \\
        --lanes 8 --workers 24 --dpi 72

Key lấy từ env `OPENROUTER_API_KEY`, hoặc `--env-file <path>` (file dạng
KEY=VALUE, chỉ đọc OPENROUTER_API_KEY).
"""

from __future__ import annotations

import argparse
import csv
import os
import pathlib
import queue
import re
import subprocess
import sys
import threading
import time

# ---------------------------------------------------------------- pure helpers


def load_books(csv_path: pathlib.Path, limit: int | None = None) -> list[dict]:
    """Đọc CSV bằng csv module (KHÔNG split tay — authors/title hay chứa dấu phẩy)."""
    with open(csv_path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    missing = [c for c in ("slug", "pdf_path") if rows and c not in rows[0]]
    if missing:
        sys.exit(f"CSV thiếu cột bắt buộc: {missing} (cần slug,title,authors,pdf_path)")
    return rows[:limit] if limit else rows


def read_api_key(env_file: pathlib.Path | None) -> str:
    """OPENROUTER_API_KEY từ env, hoặc --env-file KEY=VALUE."""
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key and env_file:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("OPENROUTER_API_KEY") and "=" in line:
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not key:
        sys.exit("Thiếu OPENROUTER_API_KEY (env hoặc --env-file)")
    return key


def classify_result(ok: bool, init_failed: bool, hit_402: bool) -> str:
    """Nhãn kết quả 1 cuốn — WARN không còn là hộp đen."""
    if hit_402:
        return "STOP(402)"
    if init_failed:
        return "WARN(init-fail)"
    return "DONE" if ok else "WARN(no-epub)"


def count_429(*log_files: pathlib.Path) -> int:
    # (?<![\d.]) chặn khớp nhầm '429' trong số thập phân của dòng cost (vd 0.429).
    n = 0
    for lf in log_files:
        if lf.exists():
            n += len(re.findall(r"(?<![\d.])429\b|rate.?limit", lf.read_text(errors="replace"), re.I))
    return n


# ---------------------------------------------------------------- runner


class BatchRunner:
    def __init__(self, args: argparse.Namespace, api_key: str):
        self.home = args.home
        self.log_dir = args.log_dir
        self.workers = str(args.workers)
        self.dpi = str(args.dpi)
        self.bin = args.scan2ebook
        self.env = dict(os.environ, SCAN2EBOOK_HOME=str(self.home),
                        OPENROUTER_API_KEY=api_key)
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.results: list[tuple[str, int, str, float, int]] = []

    def _run(self, cmd_args: list[str], log_file: pathlib.Path) -> int:
        with open(log_file, "w") as f:
            return subprocess.run([self.bin, *cmd_args], stdout=f,
                                  stderr=subprocess.STDOUT, env=self.env).returncode

    def do_book(self, row: dict, lane: int) -> None:
        slug = row["slug"]
        book_home = self.home / slug
        scans = book_home / "scans"
        epub = book_home / "dist" / f"{slug}.epub"
        t0 = time.monotonic()
        with self.lock:
            print(f"[lane{lane}] START {slug}", flush=True)

        init_failed = hit_402 = False
        log_files: list[pathlib.Path] = []
        if not list(scans.glob("page_*.jpg")) and not list(scans.glob("page_*.png")):
            init_log = self.log_dir / f"{slug}-init.log"
            log_files.append(init_log)
            rc = self._run(
                ["init", slug, "--from", row["pdf_path"].strip('"'), "--dpi", self.dpi,
                 "--author", row.get("authors", ""), "--title", row.get("title", slug)],
                init_log)
            init_failed = rc != 0

        if not init_failed:
            for attempt in (1, 2):
                all_log = self.log_dir / f"{slug}-all-{attempt}.log"
                log_files.append(all_log)
                self._run(["all", slug, "--yes", "--workers", self.workers], all_log)
                if epub.exists():
                    break
                if "402 Payment" in all_log.read_text(errors="replace"):
                    hit_402 = True
                    with self.lock:
                        print(f"[lane{lane}] !!! 402 HẾT CREDIT ở {slug} — dừng nhận việc mới", flush=True)
                    self.stop.set()
                    break
                retry_log = self.log_dir / f"{slug}-retry-{attempt}.log"
                log_files.append(retry_log)
                # `ocr` trần tự nạp work/context.json cạnh output → retry giữ context
                self._run(["ocr", str(scans), str(book_home / "work" / "ocr"),
                           "--workers", self.workers], retry_log)

        secs = time.monotonic() - t0
        label = classify_result(epub.exists(), init_failed, hit_402)
        with self.lock:
            self.results.append((slug, lane, label, secs, count_429(*log_files)))
            print(f"[lane{lane}] {label} {slug} {secs:.0f}s", flush=True)

    def worker(self, lane: int, q: queue.Queue) -> None:
        while not self.stop.is_set():
            try:
                row = q.get_nowait()
            except queue.Empty:
                return
            self.do_book(row, lane)
            q.task_done()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", type=pathlib.Path, required=True, help="CSV: slug,title,authors,pdf_path")
    ap.add_argument("--home", type=pathlib.Path, required=True, help="thư mục chứa book-homes (SCAN2EBOOK_HOME)")
    ap.add_argument("--log-dir", type=pathlib.Path, required=True, help="thư mục log per-book per-pass")
    ap.add_argument("--lanes", type=int, default=4, help="số sách chạy song song (8 đã verify an toàn với 1 key)")
    ap.add_argument("--workers", type=int, default=24, help="worker OCR mỗi lane")
    ap.add_argument("--books", type=int, default=None, help="chỉ chạy N cuốn đầu CSV (benchmark)")
    ap.add_argument("--dpi", type=int, default=72, help="DPI render PDF (72 = native cho scan ~1024px)")
    ap.add_argument("--env-file", type=pathlib.Path, default=None, help="file KEY=VALUE chứa OPENROUTER_API_KEY (thay cho env)")
    ap.add_argument("--scan2ebook", default="scan2ebook", help="path tới binary scan2ebook (default: PATH)")
    args = ap.parse_args(argv)

    api_key = read_api_key(args.env_file)
    rows = load_books(args.csv, args.books)
    args.log_dir.mkdir(parents=True, exist_ok=True)

    runner = BatchRunner(args, api_key)
    q: queue.Queue = queue.Queue()
    for r in rows:
        q.put(r)

    print(f"BATCH {args.csv.name}: {len(rows)} cuốn, {args.lanes} lane × {args.workers} worker "
          f"= {args.lanes * args.workers} concurrent", flush=True)
    wall0 = time.monotonic()
    threads = [threading.Thread(target=runner.worker, args=(lane, q)) for lane in range(args.lanes)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = time.monotonic() - wall0

    print("\n===== KẾT QUẢ =====", flush=True)
    for slug, lane, label, secs, r429 in runner.results:
        print(f"  lane{lane} {slug}: {label} {secs:.0f}s 429={r429}", flush=True)
    # Sách còn trong queue khi stop (402) — phải liệt kê, không thì "tàng hình".
    not_started = []
    while True:
        try:
            not_started.append(q.get_nowait()["slug"])
        except queue.Empty:
            break
    if not_started:
        print(f"CHƯA CHẠY ({len(not_started)} cuốn, dừng vì 402): {', '.join(not_started)}", flush=True)
    done = [x for x in runner.results if x[2] == "DONE"]
    sum_secs = sum(x[3] for x in runner.results)
    print(f"wall-clock = {wall:.0f}s | tổng tuần tự = {sum_secs:.0f}s"
          + (f" | speedup = {sum_secs / wall:.2f}x" if wall else ""), flush=True)
    print(f"tổng 429 = {sum(x[4] for x in runner.results)}", flush=True)
    print(f"DONE {len(done)}/{len(runner.results)}", flush=True)
    warns = [x for x in runner.results if x[2] != "DONE"]
    if warns:
        print("Cần xem: " + ", ".join(f"{s}[{lb}]" for s, _, lb, _, _ in warns), flush=True)
        print("Gợi ý: WARN(no-epub) → chạy lại `scan2ebook all <slug> --yes` "
              "(0-todo tự bỏ pre-pass); WARN(init-fail) → xem log init; "
              "STOP(402) → nạp credit rồi chạy lại toàn bộ (cache = $0).", flush=True)
    return 0 if len(done) == len(runner.results) else 1


if __name__ == "__main__":
    sys.exit(main())
