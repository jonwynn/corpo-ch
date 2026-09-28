# Changelog

## 1.7.0-beta.1 — Unreleased

### Added

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

### Changed

- Aligned package and application development versions. The upstream baseline at `51d8836` identifies itself as `1.6.0`; its package metadata still said `1.5.4`. No published tag or release was changed.
- Added fork development and validation documentation while preserving existing setup instructions and contributor credits.
- Restored `django-encrypted-json-fields==1.0.5`, required by historical migration `0001`. Fresh migrations now complete without editing migration history or replacing the encryption package used by current models.
- Routed the explicit CORP profile through shared validated writes and retained separate legacy rule paths. CORP admin edits invalidate uncertain attribution and stale finalized results.
- Preserved model save options for scoped writes and excluded unrelated winners from the shared P2 score count.
- Replaced automatic official-match serializer field expansion with explicit existing field lists, keeping new internal provenance/revision fields out of the public API.
- Deferred Hydra imports until chart analysis is requested; an unavailable submodule now reports an operation error instead of blocking unrelated provider imports.

### Delivery status

The backend and viewer milestones are implemented but unreleased. The combined guarded application suite passes 171 tests using the real SQLite migration chain through `0030`; the foundation suite passes 44 executed tests with 9 explicit skips, and 12 Node controller tests pass. Focused browser checks cover the approved states, details preservation, failed refresh/recovery, saved appearance and a narrow viewport.

Real MySQL concurrency, OAuth/Discord/provider execution, old encrypted-data conversion, operating-system high contrast and final human visual acceptance remain rollout gates. All viewer switches remain off by default. No deployment has been performed.
