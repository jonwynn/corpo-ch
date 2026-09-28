# Match viewer development

Version **1.7.0-beta.1** is unreleased. The staff website viewer, CORP Cup actions, approved layout, scoped reader and live refresh are implemented. Isolated checks pass; deployment and human acceptance are not complete. All viewer rollout switches default off.

| Stage | Current result |
|---|---|
| 1. Contract and fixtures | Defined in the [contract](match-viewer-contract.md), 35 independent viewer cases and separate CORP Cup rule examples. |
| 2. Rules and stored state | Explicit `corp_cup` profile, additive provenance/revision migration, validated action writers and guarded delayed publication. |
| 3. Layout | Navy, blue and coral viewer with large names, opening actions/latest picks, centered score, dynamic target, history and expandable details. |
| 4. Staff reader | Paginated selection, scoped GET pages/fragments, fresh account checks, chart redaction and shared history validation. |
| 5. Refresh | One request at a time, cancellation, pinned player slots, preserved details, stale/error states and access-loss clearing. |
| 6. Verification | Isolated Python/JavaScript checks and focused browser checks pass. Human visual acceptance and real service/database checks remain gates. |
| 7. Rollout | Not performed. Follow the [rollout checklist](match-viewer-rollout.md). |

## Inspect the viewer locally

Use PowerShell from the repository root with the existing application virtual environment. Estimated startup: 2–10 seconds; CPU only, no GPU. The process stays running until Ctrl+C.

```powershell
.\.venv\Scripts\python.exe -m tests.viewer_preview
```

Open [the simulated viewer](http://127.0.0.1:8765/?case=live_example). Its controls move between opening bans, the first pick and a later round, or simulate a failed refresh/access loss. The [initial fixture page](http://127.0.0.1:8765/?case=approved_opening) also links to individual cases. Use `--port` with the preview command if port 8765 is occupied.

The preview renders the actual templates and assets with in-memory example data. It opens a loopback HTTP listener, but does not connect to a database, Discord, OAuth, storage providers or Sheets. It does not exercise the production reader or authentication. Example controls are confined to this test server.

The original visual fixtures use a generic alternating-pick profile. Their P2-win/P2-pick sequence is illustrative, not CORP Cup behavior. CORP Cup uses higher seed first, then the previous song's loser. Both profiles share the same visual layout.

## Run isolated verification

Run each entry point in a fresh process. PowerShell, estimated 1–5 seconds; CPU only, no GPU:

```powershell
.\.venv\Scripts\python.exe -m tests.viewer_test_bootstrap
```

The foundation runner checks fixture answers and isolation boundaries without importing the application. Model checks are explicitly skipped by this runner.

PowerShell, estimated 5–30 seconds with installed dependencies; CPU only, no GPU. This application suite applies the real migration chain to a disposable SQLite database and deletes it afterward:

```powershell
$viewer_test_modules = @(
    "tests.test_model_test_bootstrap",
    "tests.model_test_corp_cup_rules",
    "tests.model_test_match_viewer",
    "tests.model_test_match_actions",
    "tests.model_test_match_bot",
    "tests.model_test_match_admin",
    "tests.model_test_admin_imports",
    "tests.model_test_match_publication",
    "tests.model_test_match_viewer_templates",
    "tests.model_test_match_viewer_views"
)
.\.venv\Scripts\python.exe -m tests.viewer_model_test_bootstrap @viewer_test_modules
```

Both bootstraps clear deployment environment variables, disable dotenv loading, provide dummy settings and restrict file writes to temporary storage. They reject external network/process operations and native deployment database clients. Provider/task seams are isolated in the relevant tests. Windows Django setup substitutes the known OS family for Celery's subprocess-based platform probe. These are regression guards, not an operating-system sandbox or proof of normal service startup.

PowerShell with Node.js available on PATH, estimated 1–5 seconds; CPU only, no GPU:

```powershell
node --test tests/match_viewer_refresh.test.js
```

The 12 refresh-controller checks cover one in-flight request, stale content, timeouts, retries, late responses, tab visibility, assignment changes and operator polling disable. They do not replace browser layout or DOM acceptance checks.

## Implementation boundaries

- [`match_rules.py`](../corpoch/match_rules.py) supplies shared pure configuration/history validation. [`match_actions.py`](../corpoch/match_actions.py) locks official matches, checks revision tokens and commits supported transitions together. Existing non-CORP profiles keep their separate behavior.
- Migration [`0030_match_provenance_corp_cup.py`](../corpoch/migrations/0030_match_provenance_corp_cup.py) adds official-match action phase, selection kind and revision fields. Existing provenance defaults to unknown; no historical attribution is invented.
- [`match_publication.py`](../corpoch/match_publication.py) rechecks delayed evidence/export publication and writes only intended fields. File decoding and external publishing stay outside match locks. A rejected upload can leave a stored file requiring manual cleanup.
- [`match_viewer_reader.py`](../corpoch/match_viewer_reader.py) reads one authorized match in a transaction. It validates CORP setup/history and passes safe primitives to [`match_viewer.py`](../corpoch/match_viewer.py). Chart titles require the selected bracket to be revealed and the chart to belong to that setlist.
- [`match_viewer_views.py`](../corpoch/match_viewer_views.py) serves `/match-viewer/`, `/match-viewer/<match_id>/` and its `/state/` fragment. Every read checks the stored active account and tournament guild role. Responses are private and uncached.
- [`match_viewer.js`](../corpoch/static/corpoch/match_viewer.js) uses native fetch for cancellation, single-request scheduling and pinned identity checks. This replaces the planned HTMX mechanism only for the new viewer and adds no CDN dependency. The existing overlay keeps its current rendering and refresh path.

The presentation digest compares content; it is not a sequence number. Browser generation checks reject superseded responses. Known results survive retryable failures, while access loss clears the match. Unknown scores remain unavailable instead of becoming zero. Screenshots contribute presence indicators, never live gameplay telemetry or automatic match points.

## Evidence and remaining limits

Local verification uses Windows, CPython 3.14.7, Django 6.0.8 and Celery 5.6.3. The combined guarded application run passed **171 tests in 23.320 seconds**, applied migrations through `corpoch.0030` and destroyed the test database afterward. Django reported no system-check issues. The foundation runner passed **44 executed tests**, with **9 explicit model-only skips**; the refresh controller passed **12 Node tests**.

Three additional admin regression checks pass after applying the maintainer's missing-import fixes. They exercise the actual admin methods with outbound task dispatch mocked; no Discord update is sent.

These tests cover the real SQLite schema, sporting transitions, delayed callbacks, admin/bot integration seams, privacy, templates and recorded-state projection. Selected-reader query counts remain bounded when unrelated matches are added. Production latency, concurrent load and browser payload budgets still need measurement.

Focused browser checks covered all three approved visual states, keyboard-opened details surviving first-pick/later-round updates, a 503 retaining the `0:1` score with stale/retry status, recovery to a corrected `0:0`, and a saved light theme surviving reload. A 390px window with a 375px content viewport had no horizontal overflow. These results do not cover every browser, zoom level or operating-system accessibility mode.

MySQL command-order tests verify the reader's intended repeatable-read transaction and connection cleanup through mocks. They do **not** prove MySQL snapshot consistency, row locking or concurrent writer behavior. The `MATCH_VIEWER_MYSQL_VERIFIED` gate remains off until a deployment-like disposable MySQL test passes.

OAuth login, live Discord callbacks, screenshot decoding/storage, Sheets publication and ordinary service startup remain manual integration gates. Hydra imports are delayed until its analysis operation; a missing submodule produces an explicit operation error. Actual Hydra execution is unverified. The declared Python minimum is not a tested compatibility guarantee; use the verified runtime until another version passes the same checks.

Historical migration `0001` requires `django-encrypted-json-fields==1.0.5`; that dependency is restored alongside the package used by current models. Fresh migrations and current encrypted-field roundtrips pass in isolated SQLite without changing historical migrations or deployment keys. Migration `0003` changed encryption fields without a conversion operation; conversion of old deployed credentials remains unverified and must be checked separately before upgrading such a database.

Human visual acceptance must compare the three approved states with the reference, then complete long-text, 320–470px width, desktop, 200% zoom and operating-system high-contrast checks. Forced-color CSS is present, but real OS high-contrast behavior remains unverified. The preview and automated checks do not approve those visual results automatically.

Keep bracket rules, chart/setlist configuration and seed assignments fixed during an active pilot match. Those separate admin/configuration writes are not all coordinated by the match action lock. See the rollout checklist for corrections, pause conditions and rollback.
