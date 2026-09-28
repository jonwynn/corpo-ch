# Match viewer development

Version **1.7.0-beta.1** is unreleased. The staff website viewer, CORP Cup actions, approved layout, scoped reader and live refresh are implemented. Isolated checks pass; deployment and human acceptance are not complete. All viewer rollout switches default off.

| Stage | Current result |
|---|---|
| 1. Contract and fixtures | Defined in the [contract](match-viewer-contract.md), 35 independent viewer cases and separate CORP Cup rule examples. |
| 2. Rules and stored state | Explicit `corp_cup` profile, additive provenance/revision migration, validated action writers and guarded delayed publication. |
| 3. Layout | Navy, blue and coral viewer with large names, opening actions/latest picks, centered score, dynamic target, history and expandable details. |
| 4. Staff reader | Paginated selection, scoped GET pages/fragments, fresh account checks, chart redaction and shared history validation. |
| 5. Refresh | One request at a time, cancellation, pinned player slots, preserved details, stale/error states and access-loss clearing. |
| 6. Verification | Isolated checks, user-run local verification and five native MySQL checks pass. General preview behavior is confirmed; detailed accessibility and staging integration remain gates. |
| 7. Rollout | Not performed. Follow the [rollout checklist](match-viewer-rollout.md). |

## 1. Open PowerShell

Open the Windows Start menu, type **Windows PowerShell**, and open it normally. Administrator access is not needed. If using Windows Terminal, choose a **PowerShell** tab, not Command Prompt.

Copy a complete code block below, paste it into PowerShell, and press **Enter**. If Windows Terminal asks about pasting several lines, confirm the paste. Copy the code inside the box, without the surrounding backticks. Lines beginning with `#` are explanations and can be pasted too.

These blocks use `C:\git\corpo-ch` as the checkout folder. That folder is the **repository root**: it contains `README.md`, `manage.py`, `tests`, and `corpoch`. If your checkout is elsewhere, replace only that folder path in each block.

To open both guides in **Notepad++**, paste this block into **PowerShell**. Estimated duration: **1–5 seconds**; no GPU. It uses the standard Notepad++ installation folder and opens each guide in its own editor tab.

```powershell
Set-Location -LiteralPath 'C:\git\corpo-ch' -ErrorAction Stop
& 'C:\Program Files\Notepad++\notepad++.exe' '.\docs\match-viewer-development.md' '.\docs\match-viewer-rollout.md'
```

The local checks use the prepared `.venv` folder, which contains this application's Python and dependencies. No activation command, execution-policy change, `.env` file, Discord account, or MySQL server is needed for steps 2–4. This is a verification guide for an existing prepared checkout; a fresh clone without its dependencies needs setup first.

## 2. Run all checks that work without external services

Paste this entire block into **PowerShell**. Estimated duration: **15–60 seconds** with the existing dependencies; CPU and temporary disk storage only, no GPU. Slower storage or background programs can increase the time. The block enters the repository, checks the branch and tools, and stops if any check fails. It does not switch branches, install software, or modify live match records.

```powershell
& {
    $ErrorActionPreference = 'Stop'
    Set-Location -LiteralPath 'C:\git\corpo-ch'

    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        throw 'Git was not found. Stop here and report this message.'
    }
    $viewer_branch = git branch --show-current
    if ($LASTEXITCODE -ne 0 -or $viewer_branch -ne 'jons-tree-branch') {
        throw 'This folder is not on jons-tree-branch. Stop here; do not reset or switch it.'
    }
    if (-not (Test-Path -LiteralPath '.\.venv\Scripts\python.exe')) {
        throw 'The prepared Python environment is missing. Stop here and report this message.'
    }
    $viewer_node_command = Get-Command node -ErrorAction SilentlyContinue
    $viewer_node = if ($viewer_node_command) {
        $viewer_node_command.Source
    } else {
        Join-Path $env:ProgramFiles 'nodejs\node.exe'
    }
    if (-not (Test-Path -LiteralPath $viewer_node)) {
        throw 'Node.js was not found. Stop here and report this message.'
    }

    Write-Host 'Repository and branch confirmed. Runtime versions:'
    .\.venv\Scripts\python.exe --version
    if ($LASTEXITCODE -ne 0) { throw 'Python could not start.' }
    & $viewer_node --version
    if ($LASTEXITCODE -ne 0) { throw 'Node.js could not start.' }
    git log -1 --oneline
    if ($LASTEXITCODE -ne 0) { throw 'Could not read the current commit.' }

    Write-Host 'CHECK 1 OF 3: foundation, isolation and local-command checks'
    .\.venv\Scripts\python.exe -m tests.viewer_test_bootstrap
    if ($LASTEXITCODE -ne 0) { throw 'Foundation checks failed. Stop here and report the error.' }
    & ([ScriptBlock]::Create((Get-Content -LiteralPath '.\tests\mysql_local_check_tests.ps1' -Raw)))

    Write-Host 'CHECK 2 OF 3: application checks with a temporary database'
    $viewer_test_modules = @(
        'tests.test_model_test_bootstrap',
        'tests.model_test_corp_cup_rules',
        'tests.model_test_match_viewer',
        'tests.model_test_match_actions',
        'tests.model_test_match_bot',
        'tests.model_test_match_admin',
        'tests.model_test_admin_imports',
        'tests.model_test_match_publication',
        'tests.model_test_match_viewer_templates',
        'tests.model_test_match_viewer_views',
        'tests.model_test_upstream_fixes',
        'tests.model_test_upstream_providers'
    )
    .\.venv\Scripts\python.exe -m tests.viewer_model_test_bootstrap @viewer_test_modules
    if ($LASTEXITCODE -ne 0) { throw 'Application checks failed. Stop here and report the error.' }

    Write-Host 'CHECK 3 OF 3: browser refresh logic'
    & $viewer_node --test tests/match_viewer_refresh.test.js
    if ($LASTEXITCODE -ne 0) { throw 'Refresh checks failed. Stop here and report the error.' }

    Write-Host 'PASS: All three local automated check groups passed.' -ForegroundColor Green
    Write-Host 'Next: run the preview and complete the visual checklist.'
}
```

The text will scroll while tests run. Wait until the normal PowerShell prompt returns. **Success is the final `PASS: All three local automated check groups passed.` message.** An error followed by the prompt is not a pass. Do not paste the next block while tests are still running.

Expected results for this version:

| Check | Successful result | What it verifies |
|---|---|---|
| Foundation | `Ran 66 tests` and `OK (skipped=9)` | 57 executed checks. The nine skips are deliberate: those checks require the separate application runner. |
| Application | `Ran 190 tests` and `OK` | Real model/migration behavior in a temporary SQLite database, plus rules, bot/admin/provider seams, presentation and access checks. |
| Refresh | `tests 12`, `pass 12`, `fail 0` | Request scheduling, timeouts, stale responses, retries and related browser logic. |

`Creating test database`, `Applying ... OK`, and `Destroying test database` are normal application-test messages. They refer to a generated temporary database, not your tournament database. Counts may increase in later commits; keep the commit line when reporting results.

Each Python runner starts a fresh process. The runners clear deployment variables, block dotenv reads, and restrict writes to temporary storage. They reject external network/process operations and deployment database clients. Service seams are mocked. These checks do not prove that live Discord, OAuth, storage or Sheets work, and the guards are not an operating-system sandbox.

## 3. Start and open the preview

Paste this block into **PowerShell** after step 2 succeeds. Estimated startup: **2–10 seconds**; CPU only, no GPU. This command intentionally keeps running until you press **Ctrl+C** in its PowerShell window.

```powershell
& {
    $ErrorActionPreference = 'Stop'
    Set-Location -LiteralPath 'C:\git\corpo-ch'
    $viewer_branch = git branch --show-current
    if ($LASTEXITCODE -ne 0 -or $viewer_branch -ne 'jons-tree-branch') {
        throw 'This folder is not on jons-tree-branch. Stop here.'
    }
    .\.venv\Scripts\python.exe -m tests.viewer_preview
    if ($LASTEXITCODE -ne 0) { throw 'The preview could not start. See the troubleshooting table below.' }
}
```

Success prints **`Example viewer: http://127.0.0.1:8765/?case=live_example`** and **`Only illustrative fixtures are served. Ctrl+C stops the preview.`** A missing PowerShell prompt at this point is normal: the preview is running. Leave that window open.

Open [the simulated viewer](http://127.0.0.1:8765/?case=live_example), or open a **second PowerShell window** and paste the block below. Estimated duration: **1–5 seconds**; no GPU. `Start-Process` opens the page in your normal browser; it does not start the preview server.

```powershell
Set-Location -LiteralPath 'C:\git\corpo-ch' -ErrorAction Stop
Start-Process 'http://127.0.0.1:8765/?case=live_example'
```

The preview renders the actual templates and styles with example data stored in memory. It connects to no database, Discord, OAuth, storage provider or Sheets destination. The buttons above the viewer are test controls; they are absent from the production viewer. The **Corpo CH** link is part of the production header, but the preview does not serve the rest of the website.

## 4. Check the viewer by eye

Keep the browser tab visible while testing. Updates normally arrive within two seconds; allow **up to 15 seconds** after a simulated failure because retries become less frequent. The complete checklist takes about **5–10 minutes**. Automated tests cannot confirm that the colors are distinguishable to you.

| Action | Expected result |
|---|---|
| Click **Initial bans** | Both panels show their opening bans, the score is `0:0`, and no rounds are recorded. |
| Click **First pick** | Both panels switch to latest picks. P1 has **Unwritten**, P2 has no pick yet, round 1 is pending, and the score stays `0:0`. |
| Open **Match details**, then click **Later round** | Details stay open and update. P1 won round 1, P2 picked **Trinity** for round 2, and the score is `1:0`. This follows CORP loser-pick order. |
| Click **Simulate failure** | The `1:0` score remains visible. A retry/stale message appears; failure must not turn the score into zero. |
| Click **First pick** again | Updates recover and show the corrected `0:0` example, with round 1 pending. |
| Click **Simulate access loss** | The player names and score disappear. Polling stops. To resume, click **Initial bans**, wait two seconds, then refresh the browser page. |
| Change **Appearance**, then refresh | The selected theme remains selected. Return to whichever theme you prefer afterward. |
| Resize the browser to a narrow window | Text wraps and the layout remains usable without horizontal scrolling. Check the [long-name example](http://127.0.0.1:8765/?case=long_names) too. |
| Open the browser menu and set **Zoom** to **200%**, then use **Tab** and **Enter** | Text and controls remain reachable, focus stays visible, and Match details opens by keyboard. **Ctrl+0** restores 100% zoom; use the menu to restore a different previous setting. |
| Test a Windows contrast theme if you use one | Names, scores, controls and focus remain readable. Restore your previous Windows setting afterward. |

Also compare the [original opening fixture](http://127.0.0.1:8765/?case=approved_opening) and its linked states with the approved design. The original P2-win/P2-pick fixture uses a generic alternating-pick profile for visual comparison; the interactive CORP example correctly shows P1 winning before P2 picks.

To stop the preview, return to its PowerShell window and press **Ctrl+C** once. Closing the browser alone does not stop it. Restart with step 3 whenever needed.

## Troubleshooting the local checks

| Message or symptom | Next action |
|---|---|
| `Cannot find path` | The repository is not at the listed folder. Locate the folder containing `manage.py` and use that path in `Set-Location`. Do not create an empty folder to bypass the error. |
| Wrong branch message | Stop and report it. The guide deliberately does not switch branches or discard work. |
| Missing Python environment, `ModuleNotFoundError`, or missing Node.js | Stop and report the exact message. Dependency setup is needed; no activation or execution-policy change will fix missing dependencies. |
| `FAILED`, `ERROR`, or no final `PASS` message | Keep the failing output and the commit line from the start. Report which check stopped. Do not proceed to deployment. |
| Browser says it cannot connect | Confirm the preview PowerShell window is still running and use the exact printed URL. |
| Port error such as `WinError 10048` | Another process is using port 8765. If you started that preview, press **Ctrl+C** in its original PowerShell window and restart it. Otherwise use the alternate port below. Do not terminate an unidentified process. An older preview process can still show files from before an update. |
| Colors or text are difficult to read | Record which state, browser width/zoom and theme you used, and share a screenshot of the example data. |

For an occupied port, paste this alternate block into **PowerShell**. Estimated startup: **2–10 seconds**; CPU only, no GPU. Leave it running and open [the alternate preview](http://127.0.0.1:8766/?case=live_example). Use port **8766** in any other preview links while this server is running.

```powershell
& {
    $ErrorActionPreference = 'Stop'
    Set-Location -LiteralPath 'C:\git\corpo-ch'
    $viewer_branch = git branch --show-current
    if ($LASTEXITCODE -ne 0 -or $viewer_branch -ne 'jons-tree-branch') {
        throw 'This folder is not on jons-tree-branch. Stop here.'
    }
    .\.venv\Scripts\python.exe -m tests.viewer_preview --port 8766
    if ($LASTEXITCODE -ne 0) { throw 'The alternate preview could not start. Report the error.' }
}
```

## 5. Run the prepared local MySQL checks

The development PC has a separate **MySQL 8.4.11** test instance in `%LOCALAPPDATA%\CorpoCH\mysql-test`. It listens only on `127.0.0.1:3307` while this command runs. It has no Windows service, automatic startup or firewall rule. Its data and Windows-encrypted credentials stay outside the repository. The test account is restricted to databases with the `corpo_viewer_validation_` prefix. The checker creates a new generated name and removes only the database it created.

Paste the whole block into **Windows PowerShell**, opened normally under the Windows account used for setup. No password entry, administrator access or execution-policy change is needed. Estimated duration: **20–90 seconds**; CPU/database I/O only, no GPU. Let it finish before closing the window.

```powershell
& {
    $ErrorActionPreference = 'Stop'
    Set-Location -LiteralPath 'C:\git\corpo-ch'
    & ([ScriptBlock]::Create((Get-Content -LiteralPath '.\tests\mysql_local_check.ps1' -Raw)))
}
```

The command prints the Windows account and expected test folder, verifies the branch, prepared files and configuration checksums; starts its own MySQL process; runs five tests in a new disposable database; then stops that process. It restores any previous MySQL test environment variables. **Success ends with `PASS: All five native MySQL checks passed and the local server stopped.`** A failed check also attempts to stop the owned server and reports any incomplete shutdown. No deployment configuration or viewer switch is changed.

| Message or situation | Next action |
|---|---|
| Final PASS message | Local MySQL verification is complete. The server has stopped. |
| Port 3307 is already in use | Stop and report the message. The command leaves the existing process alone. |
| Missing or inaccessible file/folder | Report the exact message, expected test folder and Windows account printed above it. The command identifies the item and stops before starting MySQL. Do not create empty replacement files. |
| Saved credentials cannot be opened, or a checksum does not match | Report the message; do not bypass the checks. Credentials require the Windows account used for setup. An unreadable credential file does not by itself prove an account mismatch. |
| Tests fail, cleanup fails or the server remains running | Keep the error and any reported database name/process ID for review. Do not delete the data folder or terminate an unidentified process. |
| Another PC or fresh clone | Arrange separate test-server setup first. The private instance and credentials are not included in Git. The [manual MySQL command](match-viewer-rollout.md#verify-mysql-before-approving-its-gate) supports another prepared local test server. |

## 6. Checks that require staging accounts

Steps 2–5 cover local verification. They do **not** complete deployment approval.

Real OAuth login, Discord referee actions, screenshot processing and Sheets export also require test accounts and destinations. Those are covered in the [staging checklist](match-viewer-rollout.md). Do not run the README's self-hosting or deployment migration commands merely to complete this local guide. All production viewer switches remain off until the separate rollout checks pass.

## Implementation boundaries

- [`match_rules.py`](../corpoch/match_rules.py) supplies shared pure configuration/history validation. [`match_actions.py`](../corpoch/match_actions.py) locks official matches, checks revision tokens and commits supported transitions together. Existing non-CORP profiles keep their separate behavior.
- Migration [`0030_match_provenance_corp_cup.py`](../corpoch/migrations/0030_match_provenance_corp_cup.py) adds official-match action phase, selection kind and revision fields. Existing provenance defaults to unknown; no historical attribution is invented.
- [`match_publication.py`](../corpoch/match_publication.py) rechecks delayed evidence/export publication and writes only intended fields. File decoding and external publishing stay outside match locks. A rejected upload can leave a stored file requiring manual cleanup.
- [`match_viewer_reader.py`](../corpoch/match_viewer_reader.py) reads one authorized match in a transaction. It validates CORP setup/history and passes safe primitives to [`match_viewer.py`](../corpoch/match_viewer.py). Chart titles require the selected bracket to be revealed and the chart to belong to that setlist.
- [`match_viewer_views.py`](../corpoch/match_viewer_views.py) serves `/match-viewer/`, `/match-viewer/<match_id>/` and its `/state/` fragment. Every read checks the stored active account and tournament guild role. Responses are private and uncached.
- [`match_viewer.js`](../corpoch/static/corpoch/match_viewer.js) uses native fetch for cancellation, single-request scheduling and pinned identity checks. This replaces the planned HTMX mechanism only for the new viewer and adds no CDN dependency. The existing overlay keeps its current rendering and refresh path.

The presentation digest compares content; it is not a sequence number. Browser generation checks reject superseded responses. Known results survive retryable failures, while access loss clears the match. Unknown scores remain unavailable instead of becoming zero. Screenshots contribute presence indicators, never live gameplay telemetry or automatic match points.

## Upstream production-fix review

The maintainer's [production-fix commit `a866682`](https://github.com/Jetsurf/corpo-ch/commit/a866682abe9db80bc195120051f1bdfd1d0381e1) was reviewed against this fork. Its changes were applied selectively to preserve the CORP action services and delayed-publication checks.

| Upstream change | Integration result |
|---|---|
| Missing admin imports | Already applied and covered by three isolated checks. |
| Channel/role admin search | Applied: search uses the stored `id` field rather than a display method. |
| Continue bot startup after a failed match restore | Applied with exception logging and removal of partially registered failed state. Other matches and background loops can start. |
| Qualifier modifier message | Applied the wording correction. |
| Qualifier export width | Applied the `A:O` update range and aligned new-sheet dimensions/header formatting with all 15 values. |
| Player roster export | Adapted: replace roster values in `A2:D` in one batch, clear obsolete values, preserve the header/other columns, and keep Discord IDs and names as literal text. `append_rows` with `OVERWRITE` still appends after the table and does not replace a roster. |
| Remove frozen headers / use semicolons in match hyperlink formulas | Deferred pending the test destination's layout and locale checks. These changes are not required by the viewer. |
| Legacy tiebreaker Back condition | Deferred. Its condition would discard some manually selected non-`bansave` rounds rather than clearing the selection. A pre-existing `pickable_tb` expression also blocks that legacy path. Both require a separate legacy-rule repair; CORP uses its independently tested locked undo path. |

The spreadsheet changes have isolated request/behavior checks; actual Sheets execution remains a staging gate. The replacement follows the Sheets API's [range update behavior](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/request#UpdateCellsRequest) and [atomic batch contract](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/batchUpdate). This review does not claim a full upstream merge or a repair of all legacy rule profiles.

## Evidence and remaining limits

The Python and Node commands in step 2 passed in Windows PowerShell **5.1.26100.9444**, using CPython 3.14.7, Django 6.0.8, Celery 5.6.3 and Node.js 24.19.0. The combined guarded application run passed **190 tests in 22.652 seconds**, applied migrations through `corpoch.0030` and destroyed its test database afterward. Django reported no system-check issues. The foundation runner passed **57 executed tests**, with **9 explicit model-only skips**; these include 13 MySQL-checker ownership/configuration tests using a fake driver. The refresh controller passed **12 Node tests**. The PowerShell blocks in both guides pass syntax parsing in Windows PowerShell 5.1. Deployment commands have not been executed.

The local MySQL helper also passes **21 isolated PowerShell preflight checks** for missing and inaccessible paths, invalid file types, path containment, sanitized credential errors and environment restoration. These checks use temporary fixtures and do not read the prepared instance or start MySQL. Step 2 includes them in its first check group.

A user-run verification on commit **`2e32fae`** also passed: **57 foundation checks with 9 expected skips**, **190 application tests in 18.173 seconds**, and **12 refresh tests with zero failures**. The nine model-dependent checks skipped by the foundation runner passed in the application run. Migrations through `0030`, the clean Django system check, temporary-database cleanup and the final local PASS message are recorded in that run. The preview started successfully, and its general behavior was reported as working as intended. This confirms the local verification workflow; it does not establish native MySQL, live-service or complete accessibility acceptance.

The application suite includes three admin regression checks for the maintainer's missing-import fixes. They exercise the actual admin methods with outbound task dispatch mocked; no Discord update is sent.

Sixteen further upstream regressions cover channel/role search, match restoration after a failure, and provider behavior. Provider checks execute production class definitions with mocked outbound operations; they do not validate ordinary provider-module startup, credentials or remote API acceptance.

These tests cover the real SQLite schema, sporting transitions, delayed callbacks, admin/bot integration seams, privacy, templates and recorded-state projection. Selected-reader query counts remain bounded when unrelated matches are added. Production latency, concurrent load and browser payload budgets still need measurement.

Focused browser checks covered all three approved visual states, keyboard-opened details surviving first-pick/later-round updates, a 503 retaining the `0:1` score with stale/retry status, recovery to a corrected `0:0`, and a saved light theme surviving reload. The CORP interactive example also refreshed from opening bans to `1:0` with P2's next pick. Access loss cleared the score and player names. A 390px window with a 375px content viewport had no horizontal overflow, including long player/chart names. These results do not cover every browser, zoom level or operating-system accessibility mode.

The five native checks now pass on **MySQL 8.4.11/InnoDB with mysqlclient 2.3.0**. They cover default-off gates, snapshot consistency during a concurrent result/next-round commit, competing actions, connection/isolation cleanup, and fresh staff/chart-visibility checks. The runner applied the real migration chain to its disposable database, reported no Django system-check issues, and removed that database. The prepared-instance wrapper passed in Windows PowerShell 5.1 with normal profile loading: **1.131 seconds for the five test bodies; 23.083 seconds for the full start/migrate/check/cleanup/stop command**. Occupied-port refusal, shutdown after a simulated checker failure, and restoration of all five prior environment values also passed. These checks establish the local test configuration; deployment-specific concurrency, connection settings and load still require verification. `MATCH_VIEWER_MYSQL_VERIFIED` remains off by default.

OAuth login, live Discord callbacks, screenshot decoding/storage, Sheets publication and ordinary service startup remain manual integration gates. Hydra imports are delayed until its analysis operation; a missing submodule produces an explicit operation error. Actual Hydra execution is unverified. The declared Python minimum is not a tested compatibility guarantee; use the verified runtime until another version passes the same checks.

Historical migration `0001` requires `django-encrypted-json-fields==1.0.5`; that dependency is restored alongside the package used by current models. Fresh migrations and current encrypted-field roundtrips pass in isolated SQLite without changing historical migrations or deployment keys. Migration `0003` changed encryption fields without a conversion operation; conversion of old deployed credentials remains unverified and must be checked separately before upgrading such a database.

General preview behavior has been confirmed locally. Detailed human visual acceptance still needs explicit coverage of the three approved states against the reference, long text, 320–470px width, desktop, 200% zoom and operating-system high contrast. Forced-color CSS is present, but real OS high-contrast behavior remains unverified. The general preview report and automated checks do not approve those individual cases automatically.

Keep bracket rules, chart/setlist configuration and seed assignments fixed during an active pilot match. Those separate admin/configuration writes are not all coordinated by the match action lock. See the rollout checklist for corrections, pause conditions and rollback.
