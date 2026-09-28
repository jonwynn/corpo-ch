# Match viewer rollout and rollback

Version **1.7.0-beta.1** is unreleased. No deployment has been performed. Start with an isolated staging environment and a small staff pilot after the checks below pass. The existing overlay remains the fallback.

## Before staging

- Run the [isolated verification commands](match-viewer-development.md) and record the commit, runtime versions and results. The current tested runtime is Windows/CPython 3.14.7/Django 6.0.8; another deployment runtime needs its own checks.
- Use a disposable MySQL database and test Discord guild, OAuth application, storage and Sheets destination. Do not point staging checks at live tournament records or channels.
- Back up the staging database and referenced media using the deployment's established backup procedure. Keep backups outside the repository and verify that they restore. Preserve encryption keys securely; do not replace deployment keys to make migration checks pass.
- Check existing encrypted credentials before and after the staging upgrade. Historical migration `0003` did not convert old encrypted values; successful fresh SQLite migration does not prove old credential readability.
- Keep all three viewer switches false initially. Install the declared dependencies and verify normal Django/Celery/bot startup separately from the guarded test runtime. Initialize Hydra only if chart analysis is required; that helper's actual execution remains a separate check.

## Apply the additive schema in staging

Use PowerShell in the staging checkout with its approved virtual environment and staging configuration. Estimated duration: 5–60 seconds for a small staging database; CPU/database I/O only, no GPU. Larger schemas, locks or slow storage can take longer. Inspect the planned operations before running the second command.

```powershell
.\.venv\Scripts\python.exe manage.py migrate --plan
.\.venv\Scripts\python.exe manage.py migrate
```

Migration `0030` adds `Match.action_revision`, `MatchBan.action_phase`, `MatchRound.selection_kind` and the explicit CORP choice. Existing action/selection provenance stays unknown. It does not convert a bracket's rules or infer historical choices. Record the applied migration state and verify existing matches still open through their existing interfaces.

Use PowerShell in the same staging environment. Estimated duration: 2–30 seconds for local static storage; CPU/storage I/O only, no GPU. Remote storage can take longer.

```powershell
.\.venv\Scripts\python.exe manage.py collectstatic --noinput
```

Verify that the deployment serves `corpoch/match_viewer.css` and `corpoch/match_viewer.js`. Restart the relevant staging processes using their existing process manager; these commands do not start or restart the website, bot or workers.

## Verify MySQL before approving its gate

The SQLite suite and mocked SQL checks do not prove MySQL concurrency. The opt-in checker runs five native tests against a fresh database on a local test server. These native tests have not yet been executed.

Prerequisites: a dedicated local MySQL test server with InnoDB, the declared `mysqlclient` dependency installed, and a test account allowed to create, use and drop disposable databases. Use a literal loopback IP address; `localhost` and remote hosts are rejected. Do not use a production server or deployment credentials.

Run in PowerShell from the repository root. Estimated test duration: 15–90 seconds after the server and driver are available; CPU/database I/O only, no GPU. Server setup time is separate. The credential prompt avoids putting the password in shell history. Connection values exist only in this PowerShell session and its child process; the `finally` block removes them afterward.

```powershell
$viewer_test_credentials = Get-Credential -Message 'Local disposable MySQL test account'
try {
    $env:MYSQL_TEST_HOST = '127.0.0.1'
    $env:MYSQL_TEST_PORT = '3306'
    $env:MYSQL_TEST_USER = $viewer_test_credentials.UserName
    $env:MYSQL_TEST_PASSWORD = $viewer_test_credentials.GetNetworkCredential().Password
    .\.venv\Scripts\python.exe -m tests.mysql_viewer_check --allow-create-test-database
    if ($LASTEXITCODE -ne 0) { throw 'MySQL verification did not pass. Review its output before continuing.' }
} finally {
    Remove-Item Env:MYSQL_TEST_HOST, Env:MYSQL_TEST_PORT, Env:MYSQL_TEST_USER, Env:MYSQL_TEST_PASSWORD -ErrorAction SilentlyContinue
    Remove-Variable viewer_test_credentials -ErrorAction SilentlyContinue
}
```

The checker supplies dummy settings, blocks deployment `.env` and service imports, and creates only `corpo_viewer_validation_<generated-id>`. It refuses to reuse an existing schema. Normal completion, including test failures, removes only the database it successfully created. If a worker cannot stop or cleanup fails, it retains that database and reports its exact name for local review. It does not alter deployment settings or enable viewer gates. Native test failure tracebacks can include local server/account diagnostics; review logs before sharing them.

The five checks cover default-off gates, a coherent reader snapshot during a concurrent score/next-round update, two competing actions with the same token, connection/isolation cleanup, and fresh staff/hidden-chart checks. A passing run reports five tests and successful database removal. Exit status is `0` for success, `1` for test failures, `2` for setup/cleanup errors and `130` for interruption.

Record MySQL/driver versions and results. Before enabling a deployment, also exercise its own configuration:

1. Repeat snapshot/interleaving checks around corrected winners, removed rounds and reassigned players. Recheck disabled accounts, revoked roles and bracket reveal changes on the next request.
2. Force an SQL/body failure and confirm cleanup. Verify nested/non-autocommit reads are rejected and connection isolation does not reach unrelated work. The native checker uses short-lived connections; other deployment connection settings need their own verification.
3. Record query count, response duration, fragment bytes and load at the intended pilot viewer count. Compare the selected match with unrelated match volume. No production capacity or latency target has been measured yet.

Stop on mixed snapshots, stale writes, leaked chart data, unexpected locking or unbounded query growth. Do not mark `MATCH_VIEWER_MYSQL_VERIFIED` true in deployment configuration until this evidence is reviewed.

## Configure and verify the staff pilot

Create a new staging match under the explicit CORP Cup profile:

- Two distinct players with positive, distinct seeds in the selected group/tournament.
- Group stage: 11 available charts, best of 7. Playoffs: 13 available charts, best of 9.
- `num_bans=2`, `ban_ruleset=bansave`, `pick_ruleset=loserpicks`, `tb_ruleset=corp_cup`, no deferral or reversed seeds.
- No BYOS or separately reserved tiebreaker chart. Every setlist chart must be available; boss charts require the corresponding active/bannable configuration.

Keep rules, chart/setlist configuration and seed assignments fixed during an active pilot match. These separate configuration writers are not all covered by the match lock. Pause the pilot for a configuration correction, make it deliberately, then recheck history and assignments before resuming. Renaming a player must preserve their pinned slot.

After MySQL verification, configure `MATCH_VIEWER_ENABLED=true`, `MATCH_VIEWER_MYSQL_VERIFIED=true` and initially `MATCH_VIEWER_POLLING_ENABLED=false` through the deployment's normal environment management. Values must be exactly `true`; defaults are false. Restart/reload the website as required by that deployment.

Verify `/match-viewer/` and a selected match with a stored guild admin, guild referee and active superuser. Deny anonymous, inactive, other-guild, `is_staff`-only and assigned-referee-only accounts. Test real OAuth/session behavior. Check missing matches and unrevealed/shared charts. Inspect page source and network responses for withheld titles, raw configuration, screenshot paths and account details.

Enable polling only after those checks pass. Exercise the following with real supported referee actions:

- Four opening actions, with zero, one and two saves; first high-seed selection; both loser-pick outcomes; pending and recorded results.
- Automatic sole-chart tiebreaker, multiple-choice tiebreaker and saved-song exclusions; undo and corrected winners.
- Finalization before screenshots/export, later evidence completion and export status. A viewer score must never come from screenshot points.
- A second referee tab, delayed callback, stale admin form, role revocation and disabled account. Access loss must clear the visible match on the next validated response.
- Temporary failure, timeout, recovery to a lower corrected score, hidden-tab resume and switching matches. Expanded details must stay open with fresh contents.

Run actual Discord screenshot upload, decode/review and test Sheets export. Correct or remove the round during a delayed upload/export and confirm stale data is not restored. An external operation can finish before local rejection; inspect the test destination for duplicate/stale output. A rejected upload can leave an unreferenced file. Identify it against current database references and remove only confirmed test artifacts using the deployment's normal cleanup process.

Complete human visual acceptance: compare the three approved states, large full names, blue/coral separation, long text, desktop and 320–470px widths, 200% zoom, keyboard focus and saved themes. Verify real operating-system high contrast. Record exceptions before expanding the pilot.

## Rollback

Disable `MATCH_VIEWER_POLLING_ENABLED` and `MATCH_VIEWER_ENABLED` in the deployment configuration, then restart/reload the web process. An already open viewer stops when it receives the changed gate/access response. Use the existing overlay while investigating.

Keep migration `0030` and its data. Reversing it would discard provenance and revisions. Disabling viewer flags does not revert CORP writers or change existing match rules. Before rolling back writer code, stop CORP sporting writes and delayed uploads/exports, or retain the current coordinated writer implementation. Do not run older writers against active `corp_cup` matches and assume equivalent behavior.

Resume only after the affected matches have verified history, or start new matches with the supported writers. Do not reconstruct deleted action history or automatically resubmit screenshots/exports. Preserve the incident snapshot and staging evidence for review without publishing credentials or private match data.
