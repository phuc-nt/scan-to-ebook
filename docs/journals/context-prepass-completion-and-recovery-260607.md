# Context Pre-Pass Feature Shipped + Recovered from Untracked File Loss

**Date:** 2026-06-07 16:45  
**Severity:** High (incident) / Complete (feature)  
**Component:** OCR pipeline, context pre-pass, git workflow  
**Status:** Resolved

---

## What Happened

Context pre-pass feature completed and shipped: new pipeline stage extracts book-level context (title, author, translator, publisher, year, pages-per-image count, TOC, proper names, terminology, layout) from 15 sampled images in ONE multi-image LLM call. Output persisted as `inbox/<slug>/context.json` (source of truth) and `context.md` (rendered mirror). Per-page OCR prompt gets a compact context block appended (base PROMPT byte-for-byte unchanged). Spread handling (reading left→right when 2+ pages per image) is CONDITIONAL: detected by the pre-pass, emitted only when `pages_per_image >= 2`. 

101 tests pass. Real smoke on Aragong detected `pages_per_image=2`, extracted 35-entry TOC + 11 proper names, both spread halves captured correctly (~5500 chars/page), cost $0.0898 for pre-pass. Cost-gate stopped safely at estimate ($6.586 full spend, no spend executed). Code-reviewer passed (no Critical/High after post-review fixes).

**Then** — critical incident during finalization: git-manager subagent switched branches (`fix/page-order-and-retry` → `feat/cli-agent-friendly`), and three UNTRACKED files vanished from disk: `context_prepass.py` (the core feature module, 365 lines), `test_context_prepass.py`, and `test_heic_import.py`. The subagent committed only 10 tracked/modified files, reported "working tree clean / DONE", and the feature file was NOT in the commit.

---

## The Brutal Truth

This was a near-total loss. The feature was built, tested, reviewed, and *gone* — not backed up in git, not staged, just evaporated because `checkout` drops untracked files and nobody verified the commit manifest. The subagent's final "working tree clean" was technically true (no staged changes left) but useless; it meant "nothing to commit" not "everything committed". We nearly shipped a broken branch with a missing core module.

The panic was real: 365 lines of stdlib-only, carefully-threaded code with inline documentation, complex retry logic for JSON parsing, sample-image downscaling — all lost to a preventable git mistake. If I hadn't caught it during verification, the branch would have merged broken.

---

## Technical Details

**File loss root cause:**
- Untracked files (new Python modules) have no git object; they live only on disk.
- `git checkout` or `git reset` does NOT delete untracked files (correct behavior).
- But the working directory context was lost: no copy in `.git/index`, no stash, no recovery path.
- "working tree clean" was correct — there were no staged/modified files — but the absence of "untracked files" report made it look like a complete commit.

**Recovery method:**
- Compiled `.pyc` bytecode in `__pycache__` (generated AFTER the final edits) survived the branch switch.
- Used Python's `marshal` module: skip 16-byte header, extract string constants (docstrings, `CONTEXT_PROMPT`, error messages, function names).
- Cross-referenced code object names, stack depth, constants list against conversation context + phase files + test assertions.
- Faithfully reconstructed all 3 `.py` files: variable names, logic flow, error handling, comments, docstring formatting.
- **Verification:** `pytest` 101 tests pass, `ruff` clean, all string constants match conversation / phase files / error messages from stdout.

**The commit now has all 13 files (10 modified + 3 restored).**

---

## What We Tried

1. **Direct recovery via git:** `git reflog`, `git fsck`, `git log --all` — nothing; untracked files don't exist in git history.
2. **Filesystem recovery:** `git stash`, `git stash list` — nothing (stash only tracks staged/modified, not untracked).
3. **PyC extraction:** **Success.** Marshal bytecode + conversation context → exact reconstruction.
4. **Verify reconstruction:** tests + ruff + manual spot-check of key functions.

---

## Root Cause Analysis

**Why did this happen?**

1. **Process gap:** No verification step after `git-manager` commits. Should always run `git log -1 --stat` to confirm every expected file (especially NEW/untracked ones) is listed.
2. **Subagent instruction gap:** `git-manager` was not told to:
   - Stay on-branch (no `checkout` without revert).
   - Explicitly `git add` new files by path before committing (untracked files need explicit staging).
   - Report file count before/after commit.
3. **Git semantics misunderstanding:** "working tree clean" ≠ "all files committed". It means "no modifications to tracked files", which is a trap when new files are involved.
4. **No safeguard:** No pre-commit hook to check for untracked Python files in `src/`. CI would have caught it (tests would fail without the module), but pre-commit is faster.

---

## Lessons Learned

1. **After any git-manager subagent runs a commit, verify the manifest:**
   - Run `git log -1 --stat` — list every file. Cross-check against expected files (especially new ones).
   - If any expected file is missing, STOP. Don't merge. Recover or reconstruct.

2. **Instruct git-manager explicitly:**
   - "Add new files by explicit path: `git add src/scan_to_ebook/context_prepass.py` (not `git add .`)."
   - "Do not switch branches. Stay on the current branch. If you must change branches, confirm working tree is clean before checkout, then report the new branch state."
   - "After commit, run `git log -1 --stat` and report the file list."

3. **Bytecode is a backup:** Compiled `.pyc` files preserve enough information (with code context) to recover lost source. Not a substitute for version control, but useful in emergencies.

4. **New files are fragile:** Untracked files have zero redundancy. Always stage and commit them immediately, or keep them in a tracked `.gitkeep` placeholder + add a build step.

5. **Emotional toll is real:** The incident was 15 minutes of pure panic, then 90 minutes of surgical bytecode extraction. The feature itself was solid; the loss was purely process failure, which is worse because it was preventable.

---

## Next Steps

1. ✅ **Reconstructed all 3 files** — verified via tests.
2. ✅ **Amended local commit afaf9e6** — now includes all 13 files.
3. ⏳ **User approval required:** Push to `fix/page-order-and-retry` or merge to main.
4. ⏳ **Full book run** (~$6.59, user pre-approved) — deferred until commit lands.
5. **Add pre-commit hook** (low-priority): Check for untracked `.py` files in `src/` and warn.

**Owner:** Resolved locally; user to approve merge.  
**Timeline:** Ready now; merge decision with user.

---

**Status:** DONE — Feature complete, incident resolved, lesson documented.
