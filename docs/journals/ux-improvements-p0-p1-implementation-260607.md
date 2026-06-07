# P0/P1 UX Improvements: Auto .env, Multi-Ext Images, File Sanitization, Init Command

**Date**: 2026-06-07 13:45
**Severity**: Medium (prep friction → workflow friction)
**Component**: CLI entry points, glob patterns, file naming, initialization
**Status**: Resolved

## What Happened

Implemented 4 high-value P0 fixes + 1 P1 friction reducer identified in UX assessment. Executed on `fix/page-order-and-retry` branch (commit f709884), code-reviewed, and discovered/fixed two safety issues before merge.

**P0 (4 fixes):**
1. `_load_dotenv()` in `cli.py:main()` — auto-parse `.env` from CWD→repo root, does NOT override `os.environ` (shell exports win)
2. Multi-ext image glob: `IMAGE_PATTERNS` const in `ocr.py` now PNG+JPG both cases (lower+upper); `all` subcommand uses it
3. `_slugify()` for epub filename: replace đ/Đ→d/D **before** NFKD normalize, then ascii kebab-case, "book" fallback
4. Docs sync: README + user-guide removed `source .env` ritual, documented `init`, natural-sort clarification

**P1 (1 feature):**
1. New `scan2ebook init <slug>` subcommand: mkdir inbox + optional `--from <dir>` import (natural-sort, copy2 to page_NNN.ext) + scaffold metadata.json (preserves existing)

## The Brutal Truth

This was a **pay-to-play fix**. F2 (JPG blindness) + F1 (source .env loop) were actual user friction — JPG scans → "0 pages" silently, then API charges for nothing because OCR sees empty inbox. The `init` command dramatically flattens the setup curve but introduced a new money leak: re-importing into a populated inbox orphaned stale `page_NNN` files that got OCR'd anyway.

Code review caught it. I was **furious** I almost shipped auto-delete behavior without asking — that's the kind of "convenience" that burns user trust when they realize old files disappeared.

## Technical Details

**Multi-ext pattern challenge:**
```python
IMAGE_PATTERNS = ["*.png", "*.PNG", "*.jpg", "*.JPG", "*.jpeg", "*.JPEG"]
```
macOS glob is case-sensitive; on case-insensitive filesystems, dict dedup handles rare literal double-match.

**Diacritic bug (nearly silent):**
The order matters. If you NFKD-normalize `đ` first, then try to replace it, it's a no-op because `ascii` codec with `ignore` already dropped the combining accent. Fix:
```python
def _slugify(title: str) -> str:
    s = title.replace("đ", "d").replace("Đ", "D")  # BEFORE NFKD
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^a-z0-9-]", "-", s.lower())
    return s or "book"
```

**Re-import guard (code review finding):**
```python
if any(p.match("page_[0-9]*.*") for p in inbox_path.iterdir()):
    print("Error: inbox already has page_NNN files. Clear them first.")
    sys.exit(2)
```
No silent deletion; user gets explicit instruction. ROI: prevents ~$0.05 per stale-but-orphaned page.

**Tie-break for same-stem multi-ext:**
Natural sort handles `page_5` vs `page_80` correctly, but `page_1.jpg` + `page_1.png` (same stem, different ext) needs stable ordering. Added ext-suffix to sort key to lock order independent of glob enumeration order.

## What We Tried

1. **First draft of `init --from`**: silently overwrite stale `page_NNN` files → rejected by code-review; replaced with guard
2. **Filename sanitization**: tried replacing `/:\*` with `_` later in pipeline → not enough, did it at name generation site in `_slugify()`
3. **DOT-ENV loading**: tested with `dotenv` library → chose stdlib `os.environ` + manual `.env` parser to preserve zero-deps philosophy

## Root Cause Analysis

**Why multi-ext was blind:** Design assumed PNG (personal scanning workflow); JPG is equally valid but glob pattern was single-case, single-ext. Simple oversight, high friction because failure mode is **silent** (0 pages, no error).

**Why diacritic replace failed:** Misunderstood NFKD decomposition; thought you could decompose-then-replace. Learned the hard way: decomposition happens character-by-character, combining marks are separate codepoints, and `ascii` codec with `ignore` drops them entirely. Must normalize *after* replace.

**Why `init --from` needed guard:** Convenience features that touch filesystem are money-safety surfaces when downstream cost real API dollars. "Delete old files to make room" sounds reasonable until user finds out OCR charged them for phantom pages. Guard → abort → instruction is correct call over auto-cleanup.

## Lessons Learned

1. **Normalize-then-replace is backwards.** Replace literal chars → normalize → encode. Order matters when combining marks involved.
2. **"Convenience" file operations are liability.** If script deletes, moves, or overwrites user files (especially in setup), it's touching money. Default to abort + instruction, not silent deletion.
3. **Silent failure is the worst UX.** `*.png` glob → 0 pages → pipeline runs, charges API, produces nothing. Should either (a) support both formats or (b) error loudly. Silent path is unforgivable.
4. **Deterministic ordering for glob results.** Same-stem files (scan_1.jpg + scan_1.png) from glob can enumerate in any order. Sort is not stable on tie, so add secondary key (ext) to lock order.

## Next Steps

1. Merge to main (all tests pass, ruff clean, code-review approved with concerns addressed)
2. **Document the diacritic edge case** in code comment (this will bite someone else)
3. **Consider F3 (output path)** if users report paths appearing in wrong places — low friction currently, but worth watching
4. Per re-evaluation: **usability 7.3 → 8.3/10**; prep friction (the bottleneck) dropped from ~11 manual steps to ~5

**Tests:** 33 passing (was 31; +2 for guard condition + tie-break sort).

---

**Unresolved:**
- F3 (default output path relic of `<inbox>/../output/<slug>`) — leave as-is; low ROI to standardize further
- P2.8 (one-command `all --smoke` with confirm gate) — nice-to-have, defer unless scanning becomes frequent
