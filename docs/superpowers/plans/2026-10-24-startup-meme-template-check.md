# Startup Meme Template Check Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every startup validate external meme repositories and report per-repository progress, template counts, load outcomes, and a final availability summary.

**Architecture:** Keep the existing startup hook and background `MemeRepoManager.kickoff()` flow. Strengthen repository readiness around actual template `__init__.py` files, then add indexed logging inside the existing sequential `ensure_loaded()` loop without changing startup blocking behavior.

**Tech Stack:** Python >=3.9, NoneBot2, pytest, Ruff.

**Spec:** `docs/superpowers/specs/2026-10-24-startup-meme-template-check-design.md`

## Global Constraints

- Startup checks and downloads remain asynchronous/background work.
- Existing repositories are not updated when valid; missing or invalid repositories are downloaded again.
- Disabled, backend-less, and unconfigured managers remain no-ops.
- One repository failure must not prevent later repositories from being checked.

## Review Focus

- A directory containing unrelated files must not count as a valid meme repository; pinned by `test_invalid_repo_is_downloaded_again`.
- A downloaded archive without valid templates must fail cleanly and not be loaded; pinned by `test_downloaded_repo_without_templates_is_not_loaded`.
- A valid repository must skip downloading and report its template count; pinned by `test_ready_repo_skips_download_and_loads`.
- A failed repository must appear in the final summary without stopping peers; pinned by `test_startup_summary_reports_failed_repository`.
- Existing no-op configurations must stay unchanged; covered by the existing `test_disabled_or_unconfigured_manager_is_noop`.

---

### Task 1: Validate repositories and report startup results

**Files:**
- Modify: `nonebot_plugin_mantou_affection/wheel_memes.py:295-391`
- Test: `tests/test_wheel_memes.py:376-502`
- Modify: `README.md:148-154`

**Interfaces:**
- Consumes: Existing `ExtraMemeRepo`, `MemeRepoManager.memes_dir(repo)`, `backend.load_memes(path)`, and `kickoff()` startup flow.
- Produces: `MemeRepoManager._template_count(repo: ExtraMemeRepo) -> int`; readiness requires a count greater than zero. `ensure_loaded()` retains its `async ... -> None` signature and existing side effects.

- [x] **Step 1: Write failing repository-validation tests**

Add tests proving that a directory with only an unrelated file triggers download, a downloaded archive with no direct child template containing `__init__.py` is neither loaded nor considered ready, and two valid template directories produce a count of two through `_template_count(REPO)`.

- [x] **Step 2: Run validation tests and confirm the old behavior fails**

Run: `poetry run pytest tests/test_wheel_memes.py -k 'invalid_repo or without_templates or template_count' -v`
Expected: failure because unrelated content currently satisfies `_repo_ready()` and `_template_count` does not exist.

- [x] **Step 3: Implement template counting and stricter readiness**

Add `_template_count(repo)` counting only direct subdirectories of `memes_dir(repo)` that contain an `__init__.py` file. Make `_repo_ready()` return whether that count is positive. After `_download_repo()` returns, require the repository to be ready before calling `backend.load_memes`; otherwise raise `MemeGenerationError` for the existing per-repository exception handler.

- [x] **Step 4: Run validation tests and confirm they pass**

Run: `poetry run pytest tests/test_wheel_memes.py -k 'invalid_repo or without_templates or template_count or ready_repo or missing_repo' -v`
Expected: all selected tests pass.

- [x] **Step 5: Write failing progress and summary logging tests**

Attach a temporary Loguru sink to collect emitted messages, then assert indexed `[1/2]` and `[2/2]` messages, valid template counts, and a final summary identifying available and failed repositories. Include a two-repository case where the first fails and the second still loads.

- [x] **Step 6: Run logging tests and confirm they fail**

Run: `poetry run pytest tests/test_wheel_memes.py -k 'summary or progress' -v`
Expected: failure because indexed progress and final summary are not implemented.

- [x] **Step 7: Add indexed progress and final summary logging**

In `ensure_loaded()`, enumerate repositories from one and log each repository's check, download when needed, and successful load with its template count. Track loaded and failed names, continue after exceptions, and log a final `可用 X/Y` summary naming failures when present. Preserve `_loaded = True` after the complete pass and existing no-op early returns.

- [x] **Step 8: Document startup behavior**

Update the README external-repository paragraph to state that every startup checks all repositories in the background, valid repositories are not redownloaded, and logs show indexed results, template counts, and final availability.

- [x] **Step 9: Run the complete verification suite**

Run: `poetry run pytest && poetry run ruff check . && poetry run python -m compileall nonebot_plugin_mantou_affection`
Expected: 653 or more tests pass, Ruff reports no errors, compilation exits zero.

- [x] **Step 10: Commit the implementation and design records**

```bash
git add nonebot_plugin_mantou_affection/wheel_memes.py tests/test_wheel_memes.py README.md docs/superpowers/specs/2026-10-24-startup-meme-template-check-design.md docs/superpowers/plans/2026-10-24-startup-meme-template-check.md
git commit -m "feat: verify meme templates on startup"
```

## Ledger

- Task 1: Ruling: NoneBot logger output is not captured by `caplog` — use `capsys` against the same emitted stdout while preserving the required log assertions — if wrong, tests could miss Loguru sink changes.
- Task 1: Ruling: Loguru binds its sink to pytest's session-start `sys.stdout` wrapper, so neither `capsys` nor `capfd` see plugin logs — attach a temporary Loguru sink (`logger.add(messages.append, format="{message}")`) in the test and assert on collected messages — if wrong, log assertions would vacuously fail or pass without verifying the real logger path.
