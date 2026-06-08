#!/usr/bin/env python3
"""OCR model benchmark: 5 Chinese vision models vs Gemini 3.1 Pro baseline.

Chạy 20 trang đầu của `bench-ocr` qua 6 model (baseline + 5 ứng viên TQ), chấm
mỗi ứng viên so với baseline Gemini (ground-truth) bằng:
  - SequenceMatcher ratio (độ giống ký tự, 0..1)
  - delta số ký tự có dấu tiếng Việt (sai dấu / bỏ dấu)
  - delta số dòng (mất dòng / gộp dòng)
  - cost thực (token usage) + latency

Tái dùng `scan_to_ebook.ocr.ocr_page` (model-agnostic). Output: per-model markdown
trong out/<model>/page_NNN.md + report.md tổng hợp. KHÔNG đụng pipeline/code repo.
"""
from __future__ import annotations

import difflib
import json
import os
import sys
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# import ocr từ repo (src layout)
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
from scan_to_ebook import ocr  # noqa: E402

# Slug + nhãn corpus đổi qua env để chạy nhiều bộ test (DRY). Mặc định = bench-ocr.
BENCH_SLUG = os.environ.get("BENCH_SLUG", "bench-ocr")
BENCH_CORPUS = os.environ.get(
    "BENCH_CORPUS", "Trường Học Đờn Bà - André Gide/Bùi Giáng, scan PDF"
)
BENCH_DIR = Path.home() / "scan2ebook" / BENCH_SLUG
PAGES_DIR = BENCH_DIR / "work-bench" / "scans-20"
# out tách theo slug để không đè kết quả run trước.
OUT_DIR = Path(__file__).resolve().parent / ("out" if BENCH_SLUG == "bench-ocr" else f"out-{BENCH_SLUG}")

BASELINE = "google/gemini-3.1-pro-preview"
# (model_id, in $/M, out $/M) — giá verify từ OpenRouter live API 2026-06-08.
CANDIDATES = [
    ("qwen/qwen3.7-plus", 0.40, 1.60),
    ("qwen/qwen3.6-plus", 0.325, 1.95),
    ("minimax/minimax-m3", 0.30, 1.20),
    ("z-ai/glm-4.6v", 0.30, 0.90),
    ("baidu/ernie-4.5-vl-424b-a47b", 0.42, 1.25),
]
BASELINE_PRICE = (2.5, 10.0)


def _load_dotenv() -> None:
    """Nạp .env từ repo root (mirror cli._load_dotenv, không ghi đè biến sẵn có)."""
    env = REPO / ".env"
    if not env.is_file():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def _diacritic_count(text: str) -> int:
    """Đếm ký tự có dấu tiếng Việt (combining mark hoặc chữ Latin có dấu).

    Heuristic: ký tự Latin mà NFD tách ra combining mark → có dấu. Dùng để đo
    mức bảo toàn dấu: ứng viên bỏ dấu sẽ có count thấp hơn baseline rõ rệt.
    """
    n = 0
    for ch in text:
        if not ch.isalpha():
            continue
        decomp = unicodedata.normalize("NFD", ch)
        if len(decomp) > 1 and any(unicodedata.combining(c) for c in decomp[1:]):
            n += 1
        elif ch in "đĐ":
            n += 1
    return n


def _run_model(model: str, pages: list[Path], api_key: str) -> dict:
    """OCR toàn bộ pages qua 1 model (song song). Trả per-page text + tokens + lat."""
    out: dict[str, dict] = {}
    total_in = total_out = 0
    lat_sum = 0.0
    fails = 0

    def _one(p: Path):
        try:
            text, meta = ocr.ocr_page(api_key, model, p, retries=2, max_tokens=12000)
            usage = meta.get("usage", {})
            return p.name, text, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0), meta.get("latency_s", 0.0), None
        except Exception as exc:  # noqa: BLE001
            return p.name, None, 0, 0, 0.0, str(exc)[:200]

    with ThreadPoolExecutor(max_workers=5) as ex:
        futs = {ex.submit(_one, p): p for p in pages}
        for fut in as_completed(futs):
            name, text, ti, to, lat, err = fut.result()
            if err:
                fails += 1
                print(f"  [{model}] {name}: FAIL {err}", file=sys.stderr)
                out[name] = {"text": "", "err": err}
            else:
                out[name] = {"text": text, "err": None}
                total_in += ti
                total_out += to
                lat_sum += lat
                print(f"  [{model}] {name}: ok in={ti} out={to} {lat:.1f}s", file=sys.stderr)
    return {"pages": out, "in": total_in, "out": total_out, "lat": lat_sum, "fails": fails}


def main() -> int:
    _load_dotenv()
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print("OPENROUTER_API_KEY chưa set", file=sys.stderr)
        return 1
    pages = sorted(PAGES_DIR.glob("page_*.jpg"), key=ocr.natural_sort_key)
    if not pages:
        print(f"Không thấy trang trong {PAGES_DIR}", file=sys.stderr)
        return 1
    print(f"Benchmark {len(pages)} trang × {len(CANDIDATES)+1} model", file=sys.stderr)

    all_models = [(BASELINE, *BASELINE_PRICE)] + CANDIDATES
    results: dict[str, dict] = {}
    for model, pin, pout in all_models:
        print(f"\n=== {model} ===", file=sys.stderr)
        t0 = time.time()
        r = _run_model(model, pages, api_key)
        r["price_in"], r["price_out"] = pin, pout
        r["cost"] = r["in"] / 1e6 * pin + r["out"] / 1e6 * pout
        r["wall"] = time.time() - t0
        results[model] = r
        # save per-page md
        mdir = OUT_DIR / model.replace("/", "__")
        mdir.mkdir(parents=True, exist_ok=True)
        for name, pg in r["pages"].items():
            (mdir / (Path(name).stem + ".md")).write_text(pg["text"] or "", encoding="utf-8")

    # score candidates vs baseline
    base = results[BASELINE]["pages"]
    report = ["# OCR Model Benchmark — 5 model TQ vs Gemini 3.1 Pro baseline", ""]
    report.append(f"- Corpus: 20 trang đầu `{BENCH_SLUG}` ({BENCH_CORPUS})")
    report.append(f"- Baseline (ground-truth): `{BASELINE}`")
    report.append(f"- Chấm: SequenceMatcher ratio vs baseline + delta ký tự có dấu + delta số dòng")
    report.append("")
    report.append("## Tổng hợp")
    report.append("")
    report.append("| Model | Char-sim vs baseline | Δdấu (avg/trang) | Δdòng (avg) | Fails | Cost 20tr | $/M in,out | Latency tổng |")
    report.append("|-------|----------------------|------------------|-------------|-------|-----------|------------|--------------|")

    # baseline row
    b = results[BASELINE]
    base_diac = sum(_diacritic_count(p["text"] or "") for p in base.values())
    report.append(
        f"| `{BASELINE}` (baseline) | 1.000 | — | — | {b['fails']} | ${b['cost']:.4f} | {b['price_in']},{b['price_out']} | {b['wall']:.0f}s |"
    )

    detail = {}
    for model, _, _ in CANDIDATES:
        r = results[model]
        sims, ddiac, dline = [], [], []
        for name, bpg in base.items():
            btext = bpg["text"] or ""
            ctext = r["pages"].get(name, {}).get("text", "") or ""
            sims.append(difflib.SequenceMatcher(None, btext, ctext).ratio())
            ddiac.append(_diacritic_count(ctext) - _diacritic_count(btext))
            dline.append(ctext.count("\n") - btext.count("\n"))
        avg_sim = sum(sims) / len(sims)
        avg_diac = sum(ddiac) / len(ddiac)
        avg_line = sum(dline) / len(dline)
        detail[model] = {"sim": avg_sim, "ddiac": avg_diac, "dline": avg_line}
        report.append(
            f"| `{model}` | {avg_sim:.3f} | {avg_diac:+.1f} | {avg_line:+.1f} | {r['fails']} | ${r['cost']:.4f} | {r['price_in']},{r['price_out']} | {r['wall']:.0f}s |"
        )

    report.append("")
    report.append("## Đọc số liệu")
    report.append("- **Char-sim**: 1.0 = giống baseline hoàn toàn. Cao = OCR gần Gemini.")
    report.append("- **Δdấu**: âm = bỏ/mất dấu so baseline (xấu); ~0 = giữ dấu tốt.")
    report.append("- **Δdòng**: âm = gộp/mất dòng; dương = tách dư.")
    report.append("- Char-sim KHÔNG phải accuracy tuyệt đối (baseline cũng có thể sai); nó đo độ ĐỒNG THUẬN với model tốt nhất hiện tại.")
    report.append("")
    report.append("> So tay: xem `out/<model>/page_001.md` cạnh `out/google__gemini-3.1-pro-preview/page_001.md`.")

    rname = "report.md" if BENCH_SLUG == "bench-ocr" else f"report-{BENCH_SLUG}.md"
    rpath = Path(__file__).resolve().parent / rname
    rpath.write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"\n✓ report: {rpath}", file=sys.stderr)
    print(json.dumps({"detail": detail, "costs": {m: results[m]["cost"] for m, *_ in all_models}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
