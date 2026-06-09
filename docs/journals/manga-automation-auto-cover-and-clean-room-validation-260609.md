# Manga Automation — Auto-Cover Detection & Clean-Room Validation

**Date:** 2026-06-09 21:16
**Severity:** Medium (feature-complete, 2 operational gotchas surfaced)
**Component:** `scan2ebook manga` pipeline
**Status:** Shipped (validated on real books: Pluto vols 1–2 from mnd.vn)

---

## What Shipped

Three features added to the manga EPUB3 fixed-layout pipeline:

1. **`--auto-cover` flag** — opt-in front-cover detection via OpenRouter vision LLM
   - New module `manga_cover_detect.py` (167 LOC, pure-stdlib `urllib` only)
   - Reuses existing LLM infra (`context_prepass._encode_sample`, `_strip_json_fence`, `ocr._is_transient`)
   - Sends first `MAX_DETECT_PAGES=5` of filtered page list, gets `{"cover_index": N|null}` back
   - On null or error → fallback page 1, build succeeds (rc 0)
   - Manual `--cover-index` overrides LLM (user always wins)
   - **Default stays $0 / offline** — auto-cover strictly opt-in, no key required to build

2. **Auto series-title** (when `--series` + `--series-index` but no `--title`)
   - Sets `dc:title` = "Series NN" via `_derive_title`
   - Stored as `null` in metadata.json, derived at load-time (not frozen at import)
   - Covers rebuilds without re-detecting

3. **Path-form slug fix** — manga `slug` arg was `type=Path`
   - Was a latent `AttributeError` crash on path-form invocations (`manga /abs/book`)
   - Now accepts both slug-form and path-form

---

## Key Design Decision (Risk Elimination)

Extracted `epub3_fixed_layout.filtered_pages(img_dir, min_px)` as **single source of truth** for page order. This eliminates the risk that detect-time and build-time page lists drift (cover_index is 1-based on the filtered list — if they filtered differently, the detected cover would point at the wrong page).

Code-reviewer verified: **risk eliminated**, no test false-positives possible.

---

## Quality Gates

- **15 net-new tests**, 269 total pass, 1 pre-existing skip
- All LLM calls mocked at `_post_cover_once` boundary (zero network in tests)
- `ruff` clean
- code-reviewer: 0 Critical/High/Medium, 2 Low (both by-design: one null-coalescing pattern, one `max()` without explicit fallback type — acceptable for this domain)

---

## Clean-Room Validation on Real Books

Rebuilt both Pluto volumes (~390 MB `.mobi` each) from mnd.vn catalog Drive folder, fresh temp home, running `--auto-cover`. This closes the open item "auto cover detection" flagged in the prior Pluto pipeline-log (260609-1549-pluto.md):

| Vol | Detect Result | Analysis | Cost |
|-----|---|---|---|
| Vol 1 | Page **3** | ✓ Matched prior manual `--cover-index 3`. Model correctly distinguished front cover from back-cover (barcode/ISBN on back gave it away). | $0.0038 |
| Vol 2 | **null** → fallback 1 | ✓ Model correctly found NO real front cover; volume starts mid-story. Did NOT hallucinate one. Correct behavior. | $0.0059 |

**Both positive + null cases pass on real files.** Total detect cost <$0.01 for both vols. This validates the feature under adversarial conditions (back-cover barcode as a distractor for Vol 1, genuine absence of front cover in Vol 2).

---

## Operational Gotchas (Use→Log→Improve)

**Manga builds must pass `--author` or pre-seed metadata.json.**

Initial Pluto builds omitted `--author` → EPUB showed "Unknown Author" in reader. Not a pipeline bug (null author → Unknown is reasonable default), but a usage gotcha: **there is no auto-author equivalent to auto series-title.** Reader feedback via screenshot revealed this.

Quick fix: edited `author` in `scans/metadata.json` + rebuild-from-scans (no re-download, no LLM). Now saved to memory: mnd.vn id 25451 (Pluto author Urasawa Naoki) can pre-seed metadata at import time.

---

## Cross-Links

See also: **[Prose Pipeline — Run History & Automation Maturity](prose-pipeline-run-history-and-automation-maturity-260609.md)** — parallel track showing OCR prose pipeline manual-fix count 5→0, cost drop 8× after Gemini→qwen3.7-plus. Manga automation complements that: prose auto-detects internal page structure; manga auto-detects **cover + series layout**. Both follow the use→log→improve loop.

---

## Unresolved

- **Auto-author for manga** — seed from catalog at import, or prompt LLM? No auto-author today.
- **RTL spread pairing visual validation** — needs real-device check (iPad/iPhone landscape). Standing item.
- **MAX_DETECT_PAGES=5 sufficient?** — validated for Pluto (banner + back-cover in first 2 pages). Sources with more junk before real cover may need higher value. Not yet hit in practice.
- **Nothing pushed yet** — both repos (scan-to-ebook + my-ebook-store store-log 52169d8) have unpushed commits awaiting user OK.
