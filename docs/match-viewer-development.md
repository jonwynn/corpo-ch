# Match viewer development

Version **1.7.0-beta.1** is unreleased. The current milestone supplies the [contract](match-viewer-contract.md), independent fixtures, pure rule and presentation helpers, and guarded test runners. The viewer page, model changes and production integration are not implemented.

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

The foundation suite still prohibits application imports. Model-only checks are explicitly skipped by this runner. Importing the package normally initializes Celery and may trigger other dependency startup, so application checks require the separate entry point below.

## Run isolated rule and presentation checks

Use PowerShell from the repository root with the existing application virtual environment. Estimated duration: 2–10 seconds; CPU only, no GPU. This command initializes Django with test settings and runs pure calculations plus isolation checks. Django skips the unused database; this command does not validate migrations.

```powershell
.\.venv\Scripts\python.exe -m tests.viewer_model_test_bootstrap tests.test_model_test_bootstrap.ViewerModelBootstrapBoundaryTests tests.model_test_corp_cup_rules tests.model_test_match_viewer
```

The explicit application runner permits model imports while retaining temporary storage, dummy settings, network/process guards and blocked provider/task/native database imports. During Django setup on Windows only, it supplies the known OS family to Celery's platform check, which otherwise launches a subprocess. That test-only substitution does not validate normal Celery startup.

The pure helpers are not connected to views or writers:

- [`match_rules.py`](../corpoch/match_rules.py) validates CORP Cup opening actions and derives legal next selections from completed rounds. Inputs use higher/lower sporting order, independently of pinned display colors.
- [`match_viewer.py`](../corpoch/match_viewer.py) accepts a scoped primitive snapshot and returns allowlisted presentation values. The future reader must establish actual staff access, validate the rule profile and materialize a consistent snapshot. No model query or write occurs in the builder.

Production snapshots must not use `fixture_only` to bypass profile validation. Assignment pins include participant/seed pairs, group and reversal; legacy pair-only fixture pins do not detect group or reversal changes. The presentation digest includes only returned data and is an equality check, not a sequence number. Withheld chart IDs and titles are excluded from output.

## Fixture meaning

`tests/fixtures/match_viewer_cases.json` contains complete source/answer pairs. The three design states and the intermediate blank-first-round checkpoint are hand-authored. They are not exports from a production match. Rule values illustrate expected output without approving a live sporting profile.

Fixture checks verify references, answer arithmetic and important distinctions: selection is not a point, unknown actions are not proven opening bans, neutral choices are not player picks, and removed rounds disappear. Transport, accessibility, concurrency and visual examples are requirements for later tests; listing them does not test those systems.

## Validation record and limits

Validation used Windows, CPython 3.14.7, Django 6.0.8 and Celery 5.6.3 in the existing environment. The foundation suite does not import Django or Celery. The explicit application runner verifies guarded Django initialization; other runtime combinations and normal deployment startup remain unverified.

The initial 2026-09-28 milestone passed 36 tests: 26 fixture checks and 10 bootstrap checks, using 35 match cases and 14 later-stage scenario examples. Independent review found two incorrect fixture answers; both were corrected and covered by negative checks before the final passing run. Syntax inspection, documentation links and Git whitespace checks also passed.

The CORP Cup corpus adds two numeric profiles, four opening sequences and eight tiebreaker examples in `tests/fixtures/corp_cup_rules.json`. It records opening-only bans, higher-seed first pick and subsequent loser picks. The foundation suite passes **44 tests**, with five model-only checks explicitly skipped. Separate rule tests cover confirmed selection behavior, corrections and invalid histories; presentation tests compare all 35 independent viewer fixtures and exercise privacy, access, identity and lifecycle boundaries. No production sporting-rule behavior is changed or exercised.

The combined guarded application check passes **49 tests**: 15 rule tests, 30 presentation tests and four bootstrap boundary checks. Django skips the unused database. Independent review verified corrections for malformed results appearing as zero, inconsistent round history, results after a decisive win, missing action owners and withheld charts leaking through another record. Separate CORP snapshots cover both next-picker outcomes without altering the original design fixtures.

The repository has no configured project-wide formatter or linter command. Validation uses focused tests, syntax inspection and Git whitespace checks. MySQL transactions, OAuth, Discord, screenshot processing, exports and browser rendering remain later verification gates. The existing Python minimum-version declaration and missing Hydra initialization path also need application-runtime checks before deployment.

## Fresh database gate

[`0001_initial.py`](../corpoch/migrations/0001_initial.py) imports `encrypted_json_fields.fields`. [`requirements.txt`](../requirements.txt) now retains the required `django-encrypted-json-fields==1.0.5` dependency alongside `django-fernet-encrypted-fields`, which current models use. The inspected wheel matches the SHA256 published by [PyPI](https://pypi.org/project/django-encrypted-json-fields/1.0.5/). No historical migration or deployment encryption setting was changed.

The following PowerShell command applies the existing migrations and runs database and boundary checks using temporary SQLite storage. Estimated duration: 3–15 seconds; CPU only, no GPU. It does not connect to a deployment database. Install the declared requirements in the intended development environment first.

```powershell
.\.venv\Scripts\python.exe -m tests.viewer_model_test_bootstrap
```

The repaired gate passes **nine tests**, applies the existing migration history through `corpoch.0029` and `dbot.0005`, verifies no pending loaded migrations, and destroys the disposable database afterward. Checks include current encrypted credential roundtrips, absence of plaintext values in raw storage and historical field reconstruction without legacy encryption-key lookup. The foundation suite now passes 44 tests with nine explicit model-only skips.

This verifies fresh database creation on the recorded Python/Django runtime. It does not prove MySQL behavior or conversion of credentials encrypted before migration `0003`. That historical migration changes the field definition without a data-conversion operation, and the two packages use different key configuration and value encoding. Existing deployment credentials were not accessed or re-encrypted.

The next stage integrates the proposed `corp_cup` mode, minimum provenance fields and coordinated writers, preserving existing tournament profiles. Then build the approved fixture layout, connect one selected match, add refresh/error handling, verify integration and prepare a limited staff rollout. Keep the existing overlay available throughout.
