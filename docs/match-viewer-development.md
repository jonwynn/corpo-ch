# Match viewer development

Version **1.7.0-beta.1** is unreleased. The current milestone supplies the [contract](match-viewer-contract.md), independent fixtures and a guarded foundation test runner. A viewer page and model changes are not implemented.

## Run foundation tests

From the repository root, use PowerShell with an existing Python interpreter. Estimated duration: 1–5 seconds on a typical development machine; CPU only, no GPU. These tests use the Python standard library and need no dependency installation, bot, credentials or database service.

```powershell
python -m tests.viewer_test_bootstrap
```

If an existing virtual environment contains the intended interpreter, use PowerShell instead. Estimated duration: 1–5 seconds; CPU only, no GPU.

```powershell
.\.venv\Scripts\python.exe -m tests.viewer_test_bootstrap
```

Use a fresh process. The runner clears deployment environment variables before discovery, disables dotenv loading, supplies the same dummy settings for Django and direct `corpoch.settings` imports, and confines supported file writes to a temporary directory. It rejects application/native database imports, network/DNS operations and child processes. SQLite memory connections are allowed; no application schema is created.

Python audit and import hooks catch accidental I/O through the tested entry point. They are regression guards, not an operating-system security sandbox for untrusted code. Running test modules directly bypasses this entry point. The bootstrap tests deliberately attempt prohibited operations and require rejection before I/O.

The current suite must not import the application. Importing the package normally initializes Celery and may trigger other dependency startup. Later model tests must deliberately extend this boundary, retaining dummy settings and I/O guards, and verify isolated Django initialization before loading models. The prepared settings and empty URL configuration do not demonstrate that migrations or normal deployment startup work.

## Fixture meaning

`tests/fixtures/match_viewer_cases.json` contains complete source/answer pairs. The three design states and the intermediate blank-first-round checkpoint are hand-authored. They are not exports from a production match. Rule values illustrate expected output without approving a live sporting profile.

Fixture checks verify references, answer arithmetic and important distinctions: selection is not a point, unknown actions are not proven opening bans, neutral choices are not player picks, and removed rounds disappear. Transport, accessibility, concurrency and visual examples are requirements for later tests; listing them does not test those systems.

## Validation record and limits

The foundation runner was checked on Windows with CPython 3.14.7. Application dependency metadata was inspected without starting services. Installed versions include Django 6.0.8 and Celery 5.6.3; neither is exercised by the foundation suite. Other runtime combinations remain unverified.

The 2026-09-28 milestone passed 36 tests: 26 fixture checks and 10 bootstrap checks, using 35 match cases and 14 later-stage scenario examples. Independent review found two incorrect fixture answers; both were corrected and covered by negative checks before the final passing run. Syntax inspection, documentation links and Git whitespace checks also passed.

The repository has no configured project-wide formatter or linter command. The milestone uses the focused suite, syntax inspection and Git whitespace checks. Normal Django tests, migrations, MySQL transactions, OAuth, Discord, screenshots, exports and browser rendering remain later verification gates. The existing Python minimum-version declaration and missing Hydra initialization path need isolated application-runtime checks before deployment.

## Next development gate

A referee must identify the first tournament's two-player, odd-best-of rule profile and confirm ordinary picker order, deferral, ban/save sequence and tiebreaker behavior. Existing chooser paths disagree for some settings, so fixture expectations cannot authorize changing official results or referee controls.

After that confirmation, implement the minimum provenance fields and presentation layer with compatibility tests. Build the fixture layout against the same contract, then connect one match, add refresh/error handling, verify integration and prepare a limited staff rollout. Keep the existing overlay available throughout.
