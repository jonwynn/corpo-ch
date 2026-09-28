# Changelog

## 1.7.0-beta.1 — Unreleased

### Added

- An offline staging preflight that reads an explicit private resource inventory, checks local credential inputs without displaying secrets, and reports missing fields separately from valid resource configuration. It does not start or contact services.
- A staff-only website viewer contract covering recorded scores, stable player slots, bans and saves, chart selections, corrections, access and refresh behavior.
- Independent match-state fixtures for the approved blue-and-coral layout and later integration checks.
- CORP Cup rule examples for group/playoff targets, four opening ban/save actions, effective-ban counts and saved-song restrictions at match tiebreakers.
- A foundation test runner that uses temporary storage, supplies test-only settings and rejects application imports, deployment dotenv files, network access and child processes during the suite.
- Shared CORP Cup calculations and history validation for opening-only bans, higher-seed first pick, subsequent loser picks, eligible tiebreaker songs and corrected or removed round results.
- A pure presentation builder for validated player slots, recorded scores, action provenance, latest picks, lifecycle labels and chart visibility. It accepts scoped snapshots; it does not query models or grant website access.
- An explicit guarded Django test runner, boundary checks and encrypted-field regression tests using disposable SQLite storage.
- An explicit `corp_cup` profile and additive migration `0030` for official-match action phase, selection kind and action revisions. Existing records retain unknown provenance; existing brackets are not converted.
- Atomic CORP action writers for assignment, opening bans/saves, selection, results, finalization and undo. Revision checks reject stale or duplicate bot/admin actions.
- Scoped evidence/export publication that rechecks delayed results and updates only intended fields. Screenshot decoding, file storage and external publishing stay outside match locks.
- Isolated migration, rule, bot/admin seam and publication regression tests, including stale callbacks and corrections.
- The approved navy, blue and coral viewer with large full player names, stable slots, opening bans/saves, latest picks, centered score, dynamic targets, round history and expandable details.
- A staff-only match selector, selected-match page and GET fragment, with fresh account/role checks, private cache headers, bounded reads and selected-bracket chart redaction. Rollout, polling and MySQL verification switches default off.
- Native browser refresh with one request at a time, cancellation, pinned identity checks, stale/retry handling, preserved details and access-loss clearing. Existing overlay refresh is unchanged.
- Dark, light and system appearance choices, saved theme selection, narrow layouts and forced-color styles.
- A loopback fixture preview with simulated updates/failures, read-side regression tests, 12 refresh-controller tests and a staged rollout/rollback checklist.
- An opt-in local MySQL checker that creates its own disposable database for eight concurrency/access tests. Thirteen no-server checks cover its configuration and cleanup boundaries; deployment-specific database verification remains a rollout gate.
- A PowerShell command for the prepared private MySQL test instance. It verifies the branch, paths and checksums, refuses occupied ports, runs the eight checks, stops only its own server and restores previous session variables. Credentials stay Windows-encrypted outside the repository.

### Changed

- Discord login starts locally with an expiring, browser-bound authorization attempt. Callbacks consume the attempt before contacting Discord; missing, mismatched, expired and replayed state return a manual retry page. The flow uses the existing database session table, with no new migration.
- Discord token requests have connection/read timeouts, no automatic HTTP redirects and sanitized errors. Browser requests renew expired tokens; scheduled refresh retains credentials after temporary or configuration failures and removes only an explicitly invalid grant.
- Token renewal and callback writes coordinate on the stored token row. Competing browser and scheduled refreshes reload the latest credentials before deciding whether another exchange is needed.
- Discord authentication rejects disabled accounts and no longer suppresses database failures while returning a user. Login recovery preserves unrelated session values and avoids automatic authorization loops.
- Added private development credential preparation instructions for Notepad++, a downloaded Google service-account key and test-sheet sharing. Resource IDs and credentials remain outside the repository; isolated service configuration and actual OAuth verification remain required before rollout.
- Discord login recovers from missing callback/session data, rejected OAuth responses and deleted stored tokens without undefined-variable errors. Successful callbacks store the token before updating OAuth session values.
- Corrected saved-ban spreadsheet updates to include the seventh Saved column for bansave rules while retaining six-column updates for ordinary bans and existing match-result ranges.
- The prepared local MySQL command prefers `%USERPROFILE%\CorpoCH\mysql-test`, outside Windows app-private AppData redirection. Existing AppData setups remain supported only when the preferred folder is absent; ambiguous locations and access errors stop before startup.
- Local MySQL preflight errors identify the missing, inaccessible or incorrectly typed item and show the expected folder and Windows account. Credential-load errors give a specific recovery message without exposing passwords. The local guide includes 32 isolated checks for folder selection, diagnostics and their safety boundaries.
- Added the PowerShell command to open the local development and rollout guides in Notepad++.
- Incorporated verified fixes from the maintainer's [production-fix commit `a866682`](https://github.com/Jetsurf/corpo-ch/commit/a866682abe9db80bc195120051f1bdfd1d0381e1): channel/role admin search, per-match bot startup recovery, qualifier validation wording and 15-column qualifier export. Existing CORP action and publication checks remain in place.
- Replaced append/delete player roster export with a single batch that replaces roster values, clears stale entries, and preserves the header and unrelated columns. Player names and Discord IDs are written as literal text.
- Added complete PowerShell copy-and-paste instructions for entering the repository, checking prerequisites, running all local checks, inspecting the preview, and recognizing success or failure. MySQL and service-dependent checks remain separate.
- Restored missing imports in Discord user refresh and tournament administration from the maintainer's patch. Focused tests cover task dispatch, missing staff membership and qualifier visibility without contacting Discord.
- Updated the interactive preview to demonstrate CORP Cup loser-pick order, retaining the original design fixtures for comparison and adding a long-name example.
- Aligned package and application development versions. The upstream baseline at `51d8836` identifies itself as `1.6.0`; its package metadata still said `1.5.4`. No published tag or release was changed.
- Added fork development and validation documentation while preserving existing setup instructions and contributor credits.
- Restored `django-encrypted-json-fields==1.0.5`, required by historical migration `0001`. Fresh migrations now complete without editing migration history or replacing the encryption package used by current models.
- Routed the explicit CORP profile through shared validated writes and retained separate legacy rule paths. CORP admin edits invalidate uncertain attribution and stale finalized results.
- Preserved model save options for scoped writes and excluded unrelated winners from the shared P2 score count.
- Replaced automatic official-match serializer field expansion with explicit existing field lists, keeping new internal provenance/revision fields out of the public API.
- Deferred Hydra imports until chart analysis is requested; an unavailable submodule now reports an operation error instead of blocking unrelated provider imports.

### Delivery status

The backend and viewer milestones are implemented but unreleased. The guide's automated-check block passes in Windows PowerShell 5.1: 252 application tests using the real SQLite migration chain through `0030`, 78 executed foundation tests with 9 explicit skips, and 12 Node controller tests. Application checks include OAuth session binding, token recovery, disabled-account rejection and saved-ban export corrections. Foundation checks include 21 offline staging preflight cases. Focused browser checks cover the approved states, details preservation, failed refresh/recovery, access loss, saved appearance and long text in a narrow viewport.

A user-run verification on `2e32fae` confirmed 190 application tests, 57 executed foundation tests with 9 explicit skips, 12 Node controller tests and successful preview startup. General preview behavior was reported as working as intended; detailed accessibility acceptance remains pending.

The original five native MySQL checks passed on MySQL 8.4.11/InnoDB with mysqlclient 2.3.0. The PowerShell 5.1 wrapper completed startup, migrations, checks, database removal and shutdown in 23.083 seconds with normal profile loading. Occupied-port refusal, cleanup after a simulated checker failure and environment restoration also pass.

The prepared instance was relocated outside AppData after confirming Windows app-private redirection. Its requested and physical paths now match, and the development-app rerun passed in 20.971 seconds. A standalone PowerShell run also passed all five tests in 1.165 seconds for the test bodies, removed the disposable database and stopped the server. Local setup is verified from both environments; deployment settings and viewer gates remain unchanged.

The expanded eight-check native suite passes, including OAuth state consumption and token/callback concurrency with mocked Discord responses. Test bodies took 1.512 seconds; the disposable database was removed and the local server stopped. The private inventory preflight reports its three missing credential inputs without contacting services.

Deployment-specific MySQL checks, OAuth/Discord/provider execution, old encrypted-data conversion, operating-system high contrast and final human visual acceptance remain rollout gates. All viewer switches remain off by default. No deployment has been performed.
