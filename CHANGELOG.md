# Changelog

## 1.7.0-beta.1 — Unreleased

### Added

- A staff-only website viewer contract covering recorded scores, stable player slots, bans and saves, chart selections, corrections, access and refresh behavior.
- Independent match-state fixtures for the approved blue-and-coral layout and later integration checks.
- CORP Cup rule examples for group/playoff targets, four opening ban/save actions, effective-ban counts and saved-song restrictions at match tiebreakers. Bans occur only during opening; ordinary and direct tiebreaker song-pick order remain unresolved.
- A foundation test runner that uses temporary storage, supplies test-only settings and rejects application imports, deployment dotenv files, network access and child processes during the suite.

### Changed

- Aligned package and application development versions. The upstream baseline at `51d8836` identifies itself as `1.6.0`; its package metadata still said `1.5.4`. No published tag or release was changed.
- Added fork development and validation documentation while preserving existing setup instructions and contributor credits.

### Delivery status

This milestone supplies the contract and isolated test foundation. It does not add a viewer route, database migration or production match-rule change. CORP Cup opening bans and tiebreaker candidates are documented; ordinary and direct tiebreaker pick order need confirmation before changing production chooser behavior. Fixture checks do not verify Django, the bot, a production database or browser behavior.
