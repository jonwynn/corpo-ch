# Changelog

## 1.7.0-beta.1 — Unreleased

### Added

- A staff-only website viewer contract covering recorded scores, stable player slots, bans and saves, chart selections, corrections, access and refresh behavior.
- Independent match-state fixtures for the approved blue-and-coral layout and later integration checks.
- CORP Cup rule examples for group/playoff targets, four opening ban/save actions, effective-ban counts and saved-song restrictions at match tiebreakers.
- A foundation test runner that uses temporary storage, supplies test-only settings and rejects application imports, deployment dotenv files, network access and child processes during the suite.
- Isolated CORP Cup calculations for opening-only bans, higher-seed first pick, subsequent loser picks, eligible tiebreaker songs and corrected or removed round results. These helpers are not connected to production writers.
- A pure presentation builder for validated player slots, recorded scores, action provenance, latest picks, lifecycle labels and chart visibility. It accepts scoped snapshots; it does not query models or grant website access.
- An explicit guarded Django test runner and boundary checks. Database migration validation is blocked by the missing historical `encrypted_json_fields` dependency.

### Changed

- Aligned package and application development versions. The upstream baseline at `51d8836` identifies itself as `1.6.0`; its package metadata still said `1.5.4`. No published tag or release was changed.
- Added fork development and validation documentation while preserving existing setup instructions and contributor credits.

### Delivery status

This milestone supplies the contract, isolated rules and presentation helpers. It does not add a viewer route, database migration or production match-rule change. Guarded Django initialization and pure calculations are tested; migration execution, database writes, bot integration and browser behavior remain unverified. The initial migration imports a legacy encryption package absent from current requirements. Resolve that dependency and pass the isolated migration gate before adding schema or referee-write changes.
