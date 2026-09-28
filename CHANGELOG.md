# Changelog

## 1.7.0-beta.1 — Unreleased

### Added

- A staff-only website viewer contract covering recorded scores, stable player slots, bans and saves, chart selections, corrections, access and refresh behavior.
- Independent match-state fixtures for the approved blue-and-coral layout and later integration checks.
- CORP Cup rule examples for group/playoff targets, four opening ban/save actions, effective-ban counts and saved-song restrictions at match tiebreakers.
- A foundation test runner that uses temporary storage, supplies test-only settings and rejects application imports, deployment dotenv files, network access and child processes during the suite.
- Isolated CORP Cup calculations for opening-only bans, higher-seed first pick, subsequent loser picks, eligible tiebreaker songs and corrected or removed round results. These helpers are not connected to production writers.
- A pure presentation builder for validated player slots, recorded scores, action provenance, latest picks, lifecycle labels and chart visibility. It accepts scoped snapshots; it does not query models or grant website access.
- An explicit guarded Django test runner, boundary checks and encrypted-field regression tests using disposable SQLite storage.

### Changed

- Aligned package and application development versions. The upstream baseline at `51d8836` identifies itself as `1.6.0`; its package metadata still said `1.5.4`. No published tag or release was changed.
- Added fork development and validation documentation while preserving existing setup instructions and contributor credits.
- Restored `django-encrypted-json-fields==1.0.5`, required by historical migration `0001`. Fresh migrations now complete without editing migration history or replacing the encryption package used by current models.

### Delivery status

This milestone supplies the contract, isolated rules and presentation helpers. It does not add a viewer route, database migration or production match-rule change. Fresh migrations and current encrypted-field roundtrips pass in isolated SQLite. MySQL behavior, old encrypted-data conversion, match writes, bot integration and browser behavior remain unverified.
