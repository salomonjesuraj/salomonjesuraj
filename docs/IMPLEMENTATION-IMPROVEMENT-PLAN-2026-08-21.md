# Infusion Improvement Implementation Report

Date: 2026-08-21

## Summary

This report records the first implementation pass after the full repository review.
The completed work focuses on making local verification reproducible before larger UI,
typing, lint, and test-coverage upgrades. This is intentional: the repository already has
many services and dashboard surfaces, but the quality gates were not runnable from a
fresh checkout because test tools were configured but not installed by any project command.

## Changes Completed

### 1. Reproducible developer test setup

Added `requirements-dev.txt` with lightweight core developer tooling:

- `pytest`
- `pytest-asyncio`
- `ruff`
- `mypy`
- core runtime dependencies needed by the editable packages used in tests

The file deliberately avoids making `torch` and `transformers` part of the default test
setup. Those belong to `sentiment-engine`, which is a heavy optional service runtime and
should not be required for ordinary unit tests.

### 2. Venv-aware Makefile commands

Updated `Makefile` so quality commands use `.venv` tools instead of assuming global tools:

- `setup-dev`
- `lint`
- `format`
- `typecheck`
- `test`
- `test-all`
- `test-integration`
- `compile-check`
- `compose-check`

Because this repository is currently being worked on from Windows PowerShell where `make`
may not be installed, also added `scripts/dev.ps1` with the same task names.

The default editable install path now covers the core packages needed by the current tests:

- `libs/infusion-models`
- `libs/infusion-streams`
- `libs/infusion-common`
- `services/feature-engine`
- `services/api`
- `services/scanner`
- `services/archiver`

### 3. Modern Ruff configuration

Moved deprecated top-level Ruff lint settings into `[tool.ruff.lint]`.
This removes the Ruff deprecation warning without changing the selected lint rules.

### 4. Cache hygiene

Updated `.gitignore` to ignore local verification caches:

- `.pytest_cache/`
- `.ruff_cache/`
- `.mypy_cache/`

## Verification Results

These were the results from the verification pass:

| Check | Result |
|---|---|
| `python -m pytest tests/ -q` | Passed: 4 tests |
| `python -m pytest tests/unit/ -q` | Passed: 4 tests |
| `python -m pytest tests/integration/ -q` | No tests found |
| `python -m compileall -q libs services scripts tests` | Passed |
| `docker compose config --quiet` | Passed |
| Dashboard JS syntax check, excluding vendor bundle | Passed for 49 files |
| `ruff check .` before cleanup | Failed: 478 findings |
| `ruff format --check .` before cleanup | Failed: 179 files would be reformatted |
| `ruff format .` | Completed: 179 files reformatted, then 7 post-fix files reformatted |
| `ruff check . --fix` | Completed: 294 safe fixes applied |
| `ruff check . --fix --unsafe-fixes` | Completed: 134 additional fixes applied |
| Manual Ruff cleanup | Completed: remaining 79 findings fixed |
| `ruff format --check .` after cleanup | Passed: 246 files already formatted |
| `ruff check .` after cleanup | Passed: all checks |
| `mypy libs/ services/` | Failed: 1425 errors in 158 files |
| `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/dev.ps1 test` | Passed: 4 tests |
| `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/dev.ps1 compile-check` | Passed |
| `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/dev.ps1 compose-check` | Passed |

After the config change, `ruff check pyproject.toml` passes and the previous Ruff
deprecation warning is gone. Full-repo Ruff now passes: 478 initial findings were
reduced to zero through formatting, safe fixes, scoped script ignores, unsafe-fix
review, and targeted manual cleanup.

## Files Changed In This Pass

| File | Change |
|---|---|
| `.gitignore` | Added local cache ignores for pytest, Ruff, and mypy. |
| `Makefile` | Added venv-aware setup, test, lint, typecheck, compile, and Compose check targets. |
| `pyproject.toml` | Moved Ruff lint settings to `[tool.ruff.lint]`. |
| `requirements-dev.txt` | Added lightweight developer/test dependencies. |
| `scripts/dev.ps1` | Added Windows-native dev/test runner mirroring Makefile tasks. |
| `docs/IMPLEMENTATION-IMPROVEMENT-PLAN-2026-08-21.md` | Added this implementation report and full upgrade plan. |
| Python and Markdown files across the repo | Reformatted by Ruff. |
| Python files across the repo | Safe Ruff auto-fixes, reviewed unsafe fixes, and targeted manual lint cleanup applied. |

## Current Gaps

### Testing

The existing test suite is too small for the size of the system. There are only four
passing unit tests, and the `tests/integration` and `tests/load` folders contain no real
tests yet.

### Lint and formatting

Ruff formatting and lint now pass across the repository. The cleanup included import
ordering, unused variables, unnecessary f-strings, `try/except/pass` patterns,
`zip(strict=...)`, script-specific import bootstrapping exceptions, ambiguous variable
names, and bare exception handling.

### Typing

Mypy is configured as strict, but the codebase is not strict-typed yet. The current result
is 1425 errors across 158 files. The most common categories are:

- missing generic type arguments
- missing function annotations
- nullable values used without guards
- untyped third-party imports
- incompatible assignments in scanner/API calculation code

### UI

The dashboard has improved, but it still carries Classic and New shells in parallel.
This is useful for rollout safety, but it increases CSS/JS drift. The UI should converge
on a single primary shell, with stock-breakout discovery first and option-contract
confirmation second.

## Full Upgrade Plan

### Phase 1: Finish local quality foundation

1. Run `make setup-dev` from a clean checkout.
   - On Windows PowerShell, run `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/dev.ps1 setup-dev`.
2. Confirm `make test`, `make compile-check`, and `make compose-check` pass.
   - On Windows PowerShell, use `scripts/dev.ps1 test`, `scripts/dev.ps1 compile-check`, and `scripts/dev.ps1 compose-check`.
3. Keep `sentiment-engine` as an optional install path, not part of default setup.

### Phase 2: Formatting-only cleanup

Completed in this pass. Ruff formatting now reports 246 files already formatted.

### Phase 3: Ruff cleanup

Completed in this pass. Full-repo Ruff lint now passes.

1. Safe auto-fixes reduced the initial findings.
2. Reviewed unsafe fixes were applied.
3. Remaining findings were manually fixed.
4. `ruff check .` now passes.

### Phase 4: Backend test expansion

Add focused tests for:

- `/api/ticks` stock score and contract score separation
- `stock_breakout_score`
- `stock_breakout_tier`
- relative volume behavior when volume profile is unavailable
- option-chain states: `TRADE_READY`, `WAIT_CONTRACT`, `AVOID_CONTRACT`
- EBIE hard gates
- EBIE cache freshness states: `fresh`, `stale`, `never_cached`
- scanner suppression gates
- chart merge and aggregation edge cases

### Phase 5: Integration tests

Add Docker-backed integration tests that:

1. Start Redis and Postgres.
2. Seed a small symbol universe.
3. Push mock ticks through the pipeline.
4. Assert Redis stream output.
5. Assert API endpoints return valid payloads.
6. Assert the dashboard Nginx proxy resolves `/api/` and `/ws`.

### Phase 6: Mypy strategy

Do not try to fix all mypy errors in one pass. Move strictness module-by-module:

1. `libs/infusion-models`
2. `libs/infusion-streams`
3. `libs/infusion-common`
4. scanner state and verdict modules
5. API route payload builders
6. option strategy calculations
7. EBIE state and calibration modules

### Phase 7: UI consolidation

1. Make the New shell the primary dashboard.
2. Freeze Classic as fallback for one release.
3. Remove Classic after parity.
4. Keep `Breakout Radar` as the largest first screen surface.
5. Rename visible language around scores:
   - `Stock Score`
   - `Contract Score`
   - `RVol`
   - `Tier`
   - `Freshness`
   - `Next Action`
6. Move deep option-chain metrics into selected-stock detail.
7. Remove remaining user-facing ambiguity around `option_readiness` and `Conviction`.

### Phase 8: Frontend verification

1. Add a dashboard `package.json`.
2. Add JS lint/syntax scripts.
3. Add Playwright smoke tests:
   - dashboard loads
   - Classic/New toggle works while Classic exists
   - Breakout Radar renders
   - scanner renders
   - selected-stock detail opens
   - mobile viewport has no major overlap
4. Add screenshot baselines for desktop and mobile.

### Phase 9: Repository hygiene

1. Remove accidental nested empty folder `Infusion-Core-Architecture/` if confirmed unused.
2. Decide whether generated PDFs and `tmp/` outputs should stay in git.
3. Push the local branch after review.
4. Add CI so test/lint/type drift is visible on every change.

## Recommended Next Commit Order

1. Developer setup and config cleanup.
2. Formatting-only cleanup.
3. Ruff-safe auto-fixes.
4. Backend test expansion.
5. UI consolidation.
6. Frontend Playwright tests.
7. Mypy module-by-module hardening.
