# Manga Pipeline Launch + Drive Folder Ordering Regression Fix

**Date:** 2026-06-09 18:30  
**Severity:** High (bug masked by test fixtures)  
**Component:** Manga pipeline, drive integration, image ordering  
**Status:** Resolved  

---

## What Happened

Shipped `scan2ebook manga <slug>` subcommand — a **distinct second pipeline** from the OCR-prose `all` path. Produces standard EPUB3 fixed-layout (pre-paginated) RTL manga ebooks directly from page images. NO OCR, NO pandoc, NO context pre-pass. Pure-stdlib runtime except CBR shell-out to unar/unrar.

**4 input forms** (all normalize to universal `scans/page_NNN.<ext>` contract):
1. Local image folder (reuses `pipeline._import_images`)
2. `.mobi`/`.azw3` — PDB image carve via `mobi_extract.py`
3. `.cbz`/`.cbr`/`.zip` archive via `archive_extract.py`
4. Google Drive file OR folder via `drive_input.py` (SSRF-safe URL rebuild from file-id; folder listing via undocumented embeddedfolderview scrape)

**New modules** (all <200 lines, pure stdlib): `manga_pipeline.py` (dispatch), `mobi_extract.py`, `archive_extract.py`, `drive_input.py`, `epub3_fixed_layout.py` (builder), `epub3_validate.py` (7-check structural validator). Extended `drive_download.py` for non-PDF + folder listing.

**Builder key decisions:** Viewport-sized XHTML per image. RTL spread cadence (cover & landscape = `page-spread-center`; portrait alternates right/left starting right) with manual `--spread-reset` override. Stable `dc:identifier` via `uuid5(NAMESPACE_URL, "scan2ebook:manga:"+slug)` so reader keeps identity across rebuilds. Extended metadata schema (series/series_index/subject; lang default ja, rtl default true). Dimension read without Pillow (JPEG SOF / PNG IHDR / GIF screen descriptor).

**Quality:** 250 tests pass (88 new manga), 1 skipped, ruff clean. Commit 882cc24 (21 files). plans/ gitignored.

---

## The Brutal Truth

Code-reviewer caught a **Critical bug** that was INVISIBLE in the existing test suite. Drive folder temp files were named by the random Drive file-id. `_import_images` natural-sorts by filename — so page order got **shuffled**. The bug was masked because existing tests used conveniently sortable mock IDs (`child0/child1/child2`) that happened to sort into enumeration order. Real world: random file-ids don't sort, images came out of order, and no visual QA caught it until code review.

The frustration: this bug was **load-bearing on the test fixture quality**, not the code. The code was correct; the test was too lenient. Lesson: sorting bugs hide behind "nice" test data.

---

## Technical Details

**The ordering bug:**
```python
# BROKEN: temp file named by Drive file-id
temp_file = f"/tmp/{file_id}.bin"  # file_id = "0Az2Bx9Qm4..." (random)
# _import_images natural-sorts filenames
# 0Az, 1Bx, 9Qm don't sort to enumeration order → pages scrambled
```

**The mask:**
Test fixtures used mock IDs `child0`, `child1`, `child2`. Natural sort on these happens to match enumeration order (0 < 1 < 2). Bug never triggered.

**The fix:**
```python
# Index-based temp naming preserves list_drive_folder order
temp_file = f"/tmp/dl_{idx:04d}.bin"  # 0000, 0001, 0002
# Natural sort: "dl_0000" < "dl_0001" < "dl_0002" ✓
```

**Regression test (adversarial):**
- Added opaque-ID test with deliberately mis-sortable IDs: `["1Bx", "0Az", "9Qm"]`
- Image widths used as fingerprints: 810px, 820px, 830px
- **Proves failure on buggy code:** pages arrive as [820, 810, 830] (shuffled)
- **Proves success on fix:** pages arrive as [810, 820, 830] (correct)
- Test: `test_folder_image_order_preserved_with_opaque_ids` in `test_drive_input.py`

**Also fixed H2/M2/M4:**
- H2: `parse_spread_reset` now warns on dropped non-numeric tokens (was silent empty set → wrong spread)
- M2: Builder warns on `min_px` image drops
- M4: Nav Start landmark guarded for ≥2 pages

---

## What We Tried

1. **First attempt:** Assumed test data was sufficient → code-reviewer flagged the mask immediately
2. **Diagnosis:** Ran test on real Drive folder IDs → pages shuffled
3. **Fix:** Index-based temp naming + adversarial regression test
4. **Verification:** ran the opaque-ID test against the buggy version to confirm failure→pass before accepting fix

---

## Root Cause Analysis

**Why did this hide?**

1. **Test fixture bias:** Mock data had a property (sortability) that the real data lacked (randomness). Tests passed because fixtures were too "clean."
2. **Silent failure mode:** No crash, no error — just wrong page order. Would only surface on visual inspection or reader complaint.
3. **False confidence:** Code-review would have passed on "tests all green" alone.

**Why drive file-ids?**

Design was: download each file to temp storage, then batch-import. Temp naming by file-id seemed natural (unique), but broke the assumption that `_import_images` could rely on filename ordering to preserve enumeration order.

---

## Lessons Learned

1. **Test fixtures with "nice" sortable properties can hide ordering bugs.** Use adversarial IDs (opaque, deliberately mis-sortable) for order-sensitive code. Generate them, don't hand-craft.
   
2. **When a consumer (like `_import_images`) relies on ordering, document the contract.** Temp file naming must PRESERVE enumeration order, not just be unique.

3. **"All tests green" ≠ "all scenarios covered."** Code review + "tests pass" is required, but not sufficient when test data coincidentally works.

4. **Batch operations need explicit index threads.** If loop `for idx, item in enumerate(list)`, use `idx` in names/keys. Don't let unrelated IDs (file-id, hash, UUID) replace the enumeration contract.

---

## Next Steps

1. ✅ **Fixed drive folder ordering** — index-based temp naming + adversarial regression test
2. ✅ **Fixed H2/M2/M4 safety issues** — code-reviewer approved
3. ✅ **All tests pass** (250 total, 88 new for manga; 1 skipped unrelated)
4. ✅ **Committed 882cc24** to `feat/manga-pipeline`
5. ⏳ **Visual QA required** (out of scope for CLI): RTL spread pairing direction needs real-device check (iPad/iPhone landscape) — cannot verify reader rendering from CLI
6. ⏳ **Deferred out-of-scope:** CBZ/.mobi output formats, Drive upload, EPUBCheck install

**Unresolved:**
- Drive embeddedfolderview scrape is undocumented & fragile; tolerant regex + manual-download fallback in place but needs monitoring
- RTL spread visual validation pending real-device testing

---

**Status:** DONE — Feature shipped, critical bug caught and fixed in code-review, regression test added to prevent re-occurrence.

---

## Addendum — First Real-World Run (Pluto vols 1+2), Two Production Bugs

After shipping, ran the pipeline for real on **Pluto** (Naoki Urasawa × Osamu Tezuka), 2 volumes pulled from the mnd.vn catalog Drive folder (`1jVTW-PYvuX2r_bcBgEuBzVhM_vMASO1U`, 2 children, ~390MB .mobi each). The use→log→improve loop surfaced two bugs that no synthetic test would have caught — both real-world artifacts of how files actually arrive.

### Bug A — Drive large-file virus-scan interstitial (Critical, download fully broken)

Both Pluto children failed to download (`không nhận dạng được loại file`). Root cause: files >~100MB cannot be virus-scanned by Drive, so `uc?export=download&id=` returns an **HTML "Virus scan warning" page** instead of bytes. The old code's retry stayed on `drive.google.com/uc` → got HTML again → gave up.

**Fix** (`drive_download._download_drive_bytes`): detect the interstitial (response isn't a known payload magic), parse the confirm token from the FORM field `<input name="confirm" value="t">`, then retry on the **usercontent host** `https://drive.usercontent.google.com/download?id=<ID>&export=download&confirm=<token>`. The `uuid` field is not required. SSRF invariant preserved — retry URL rebuilt from extracted file-id + fixed host constant; only the alnum token comes from HTML, never an href. Regression test `test_download_virus_scan_form_uses_usercontent_host` (real virus-scan form HTML, proven to fail on the old uc-host retry). Without this, NO large Drive file could ever download.

### Bug B — Cover hardcoded to page 1 (cover image wrong)

Device viewing (iPhone) showed no recognizable cover. Investigation by self-extracting Pluto vol 1's first carved pages: page_001 = scanlation group "Permission" banner (manhuavn.com), page_002 = **back** cover (barcode/ISBN), page_003 = the **real** PLUTO 01 front cover. The builder hardcoded `i == 1` as the cover-image → it marked the banner as the library thumbnail.

**Fix** (`epub3_fixed_layout.build`): added `cover_index` param (1-based, on the min_px-FILTERED list; default 1; clamps out-of-range to 1 with WARN). Controls the manifest `cover-image` property, OPF `<meta name="cover">`, nav `epub:type="cover"` landmark, and TOC "Cover" link — but does **not** reorder pages or touch spine cadence (banner stays in the book as the scan shipped it). Threaded through `manga_pipeline.build_manga` → CLI `--cover-index`. Regression test `test_cover_index_marks_chosen_page` (proven red on the `i==1` hardcode) + default + out-of-range clamp tests.

**Gotcha worth remembering:** `cover_index` is on the *filtered* list. If `min_px` drops an early page the offset shifts — so the carved page at index N must be visually confirmed. **Volumes can differ:** vol 1's true cover is at page_003, but vol 2's .mobi starts mid-story (page_004 is numbered "60") with NO front cover — its page_003 is just an Act-35 chapter splash (carries the PLUTO logo, so chosen as the least-bad cover). This is a source-content fact, not a pipeline bug.

### Outcome

Rebuilt both with distinct titles `Pluto 01` / `Pluto 02` (dc:title) + `--cover-index 3`; uploaded clean `Pluto 01.epub` / `Pluto 02.epub` to Drive (deleted the old ambiguously-named `Pluto N (manga test).epub` pair + 3 dev sample epubs). 254 tests pass (3 new cover_index + 1 new Drive virus-scan), 1 skipped.

**Lesson:** synthetic fixtures couldn't have caught either — the 100MB virus-scan threshold and the scanlation banner/back-cover prefix are properties of *real files from a real source*. The dual-repo use→log→improve loop is doing exactly its job: the first real book exposed two latent defects; both fixed at the source, both now regression-guarded.
