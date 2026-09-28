# Live match viewer contract

Application development version: **1.7.0-beta.1**, unreleased. Internal fixture and presentation contract: **1.0.0**. These version numbers serve different purposes.

The first release adds one read-only match viewer to the existing website for tournament staff. It uses Django templates and native fetch refreshes, with the approved navy, blue and coral layout. It shows recorded match progress. Continuous gameplay scores, note hits, combo and accuracy have no verified data source and are outside this release.

Stages 1–5 are implemented: contract/fixtures, CORP Cup rules and writers, the approved layout, scoped staff reads and refresh handling. Isolated tests, native local MySQL consistency checks and focused browser checks pass. Desktop appearance, real OAuth login and the restricted DEV Discord flow through finalization/reopening/undo are accepted locally. Detailed accessibility, deployment-specific checks and full bot/provider integration remain pending. The feature is disabled by default outside the explicit local pilot; see the [development notes](match-viewer-development.md) and [rollout checklist](match-viewer-rollout.md).

## CORP Cup rules

The supplied CORP Cup tournament rules establish these requirements:

| Stage | Setlist | Match length | Target | Match-tiebreaker score |
|---|---|---|---|---|
| Group | 11 songs | Best of 7 | First to 4 | 3–3 |
| Playoffs | 13 songs | Best of 9 | First to 5 | 4–4 |

There are four opening actions, in this order:

1. Higher seed bans a song.
2. Lower seed saves that song or bans a different song.
3. Lower seed bans a song.
4. Higher seed saves that song or bans a different song.

Deferral is prohibited. A save targets the opponent's immediately preceding ban. A saved song cannot be banned again, and a player cannot save their own ban. Each save cancels one effective ban while consuming an action, leaving **0, 2 or 4 effective bans** after all four actions. The target stays four or five regardless of the number of effective bans. Songs selected for additional opening bans must be distinct from all earlier banned or saved songs.

The implemented mapping is `num_players=2`, `num_bans=2`, `ban_ruleset="bansave"`, `pick_ruleset="loserpicks"`, `defer=False` and `num_rounds=7` or `9`. Here `total_bans=4` is the stored opening-action quota, not four guaranteed excluded songs. The additive `tb_ruleset="corp_cup"` distinguishes this profile from existing `bansave` tiebreakers, which expect a later ban. Migration `0030` registers the choice without converting existing brackets. Validate the available setlist and seed configuration before selecting it; disabled boss charts, separate reserved tiebreaker charts, BYOS or inverted seeds are unsupported.

**Bans occur only at the start of the match.** This rule clarification supersedes the extra bans in the earlier two-ban and zero-ban tiebreaker instructions. There is no tiebreaker ban action in this profile.

At the final match tie, six group-stage songs or eight playoff songs have been played:

| Effective opening bans | Unplayed, non-banned songs | Next action |
|---|---|---|
| 4 | 1 | The sole remaining song is the tiebreaker; no player is credited with choosing it. |
| 2 | 3 | The previous song's loser selects the tiebreaker from eligible songs. No additional ban. |
| 0 | 5 | The previous song's loser selects the tiebreaker from eligible songs. No additional ban. |

These counts include saved songs and are not dropdown-option counts. One saved, unplayed song can be selected. If both saved songs remain unplayed, neither can be selected as the tiebreaker. All additional bans are prohibited, so there is no saved-song ban-eligibility decision at this stage. Undo or corrected rounds require recomputing the eligible choices from surviving records.

The higher-seeded player selects the first song. After each recorded song result, the loser selects the next song, including a tiebreaker with multiple eligible choices. Picking does not alternate automatically: a player who loses consecutive songs picks again. The reference to CSC ruling for tied scores does not specify an adjudication algorithm and must not select the existing category-based `tb_ruleset="csc"`. The viewer waits for the referee-recorded winner; automated adjudication is outside scope.

The general rules require CH `v1.0.0.4080-final` and in-game result screenshots, and leave sportsmanship, restarts and rulings with referees. They do not add continuous telemetry or justify new enforcement controls in the viewer. Screenshot presence alone still cannot certify compliance.

[`tests/fixtures/corp_cup_rules.json`](../tests/fixtures/corp_cup_rules.json) records confirmed rule examples separately from the generic visual fixtures. [`match_rules.py`](../corpoch/match_rules.py) validates these rules without database access. Readers and writers share `validate_corp_cup_history`; invalid historical choices/results produce a viewer review state. Callers must validate stored opening actions before using the count-based `opening_actor` helper. Deployment approval still requires the manual gates below.

## Supported rules and legacy decisions

P1 and P2 below mean the validated, pinned display slots. Initial slots follow seed order, then `rev_seeds`; deferral never changes colors. The table retains legacy behavior identified during the baseline audit. Only the confirmed CORP profile is enabled for trusted live targets; legacy inconsistencies remain separate work.

| Situation | Existing behavior | Contract or required decision |
|---|---|---|
| Match target | `BracketRules.wins_needed` returns `ceil(num_rounds / 2)`. Ban quota is `num_players * num_bans`. | Reuse the target calculation for supported odd-best-of matches. Ban count does not set the win target. |
| Pilot configuration | Model validators allow 2–4 players and best-of 3–25, including even values. | CORP Cup specifies the two profiles above and loser picks. Other profiles stay unsupported until specified and tested. |
| First pick, no defer | Prompt and initial round use the high-seed slot. | Confirmed: higher seed picks first. |
| `loserpicks`, later ordinary round | Prompt and round creation use the previous round's loser. | Confirmed for CORP Cup: P1 wins → P2 picks; P2 wins → P1 picks. |
| `alternate`, later ordinary round | `picking_player` repeats the previous picker; `add_round` records the previous loser. | Referee must confirm alternating by actual picker. Fixture-only expectation: P1 picks R1 → P2 picks R2, regardless of R1 winner. Do not silently reconcile production behavior. |
| `deferban`, defer enabled | Opening ban order reverses. The first-pick prompt uses P2, but round creation stores P1. | Confirm whether P1 defers the ban and retains the first pick. Until confirmed, this profile cannot drive trusted chooser metadata. |
| `deferboth`, defer enabled | Opening ban order and first-pick prompt/record use P2. | Confirm P2 bans first and picks first; slots remain unchanged. |
| Opening `bansave` | Chooser is P1 at action counts 0 and 3, otherwise P2. Save controls appear at counts 1 and 3, subject to prior saves. | Confirmed CORP sequence is higher/lower/lower/higher, four actions, no defer. Candidate generation must reject previously banned or saved songs, except saving the immediately preceding opponent ban. |
| `single` tiebreaker | Remaining-chart filtering selects tiebreaker charts; picker/undo behavior still depends on shared branches. | Confirm chart count, selection ownership and undo. A sole candidate does not prove an automatic selection occurred. |
| `csc` tiebreaker | Round creation chooses a tiebreaker category from prior fret/strum counts, with no player picker. | Confirm the category rule, unique candidate requirement and undo before enabling. Label a verified generated choice Automatic selection. |
| `refdecide` tiebreaker | Chooser is null; candidates are unplayed, non-banned charts. | Confirm referee selection and undo; never credit a player with the pick. |
| `banpick` / `bansave` tiebreaker | Chooser branches depend on action/round counts and previous loser/current winner; ban-save can select the sole remaining chart. | CORP uses the previous loser for multiple choices and a neutral forced selection for one choice through `corp_cup`; existing modes remain separate. |
| Tiebreaker pickability | `pickable_tb` includes a truthy string-index expression instead of the intended ruleset comparison. | Correct only with an approved truth table and regressions for the enabled profile. |

Sources: [`BracketRules`](../corpoch/models/tournament.py), [`MatchAbstract.picking_player`, `add_round`, `setlist_remaining`](../corpoch/models/match.py), [rule labels](../corpoch/types.py), and [`DiscordMatchView`, `SongRoundSelect`, `PlayerRoundSelect`](../corpoch/dbot/view/reftool.py).

The fixture-only profile is two players, best-of seven, three opening bans per player, ordinary alternating picks, no deferral and no seed reversal. It establishes visual answers without approving production rules. The three illustrated checkpoints omit two opening actions between the first and second image states.

That six-action profile remains a generic design example, not the CORP Cup configuration. For CORP, the fourth opening action already creates blank R1 in the current workflow. Preserve the approved layout while showing the actual four-action sequence and explicit Ban/Save labels. A zero-effective-ban result still has opening history to display.

The mockup's P2-winning-R1/P2-picking-R2 sequence is also illustrative. Under CORP Cup loser picks, P1 must win R1 for P2 to pick R2. If P2 wins R1, P1 picks R2 again. Keep the layout and populate the score and picker from actual records; do not copy the mockup's example sequence into live rule behavior.

## Authoritative records and provenance

| Displayed information | Existing source | Required handling |
|---|---|---|
| Match and context | `Match.id`; `Match.group → Group.bracket → Bracket.tournament` | Match IDs are strings, not assumed UUID-only values. Scope all reads to the selected match and tournament. |
| Identity and seeds | `Match.players → GroupSeed.id/seed/player`; `TournamentPlayer.config` and `ch_name`; `Match.rev_seeds` | Join actions/results by TournamentPlayer ID. Resolve names safely without exposing raw config or alias lists. |
| Score | `MatchRound.winner_id` | Count only the two participants. Chart selection and screenshot totals award no points. |
| Target | `Match.group.bracket.ruleset.wins_needed` | Validate the supported profile; do not hardcode four or infer the target from bans. |
| Ban/save records | `MatchBan.num`, `player_id`, `chart_id`, `saved` | Order surviving rows by `(num, id)`; distinguish actions from effective exclusions. |
| Rounds and selections | `MatchRound.id`, `num`, `chart_id`, `picked_id`, `winner_id`, `loser_id` | Order by `(num, id)`; validate ownership and numbering before projection. Stored `picked` alone cannot prove historical chooser identity. |
| Chart title | `Chart.tournament_name` | Preserve configured title/speed/modifier text, escape it and apply selected-bracket visibility. |
| Finalization | `Match.complete`, `winner_id`, `loser_id`, `ended_on` | Distinguish finalized results from reaching the target and evidence/export flags. |
| Evidence/export | `MatchRound.screenshot`, `steg`; `Match.finished`, `submitted` | First release exposes presence and local administrative status only. Do not read files or decode screenshots on viewer requests. |
| Staff access | Active `DiscordUser`; selected `Tournament.guild.admins/referees` | Require authenticated, active account plus applicable stored membership or superuser. |

Model sources: [match](../corpoch/models/match.py), [tournament](../corpoch/models/tournament.py), [charts](../corpoch/models/charts.py), [user](../corpoch/models/misc.py), [guild](../corpoch/dbot/models.py).

Migration [`0030_match_provenance_corp_cup.py`](../corpoch/migrations/0030_match_provenance_corp_cup.py) adds two provenance fields only to the concrete official-match models:

- `MatchBan.action_phase`: `unknown`, `opening`, or `tiebreaker`.
- `MatchRound.selection_kind`: `unknown`, `player`, `referee`, or `automatic`.

It also adds `Match.action_revision` to invalidate delayed sporting actions after corrections. Tokens include the scoped match state; they do not grant staff access.

Default existing, imported and unverified rows to `unknown`; do not infer a historical backfill from current rules or timestamps. Capture action phase and `saved` on insertion. Capture chart, logical chooser and selection kind in one validated write. A referee choosing on a player's behalf records that player as the logical chooser.

Intentional selection undo clears attribution. Chart deletion retains selection kind so the viewer can say Chart unavailable. Unverified admin replacement of a chart, picker or action resets provenance to unknown; privileged verification requires explicit validation. Reordering alone does not change phase. These fields describe surviving records, not deleted-event history.

## Identity, score and panel rules

**Identity.** Initially order two valid, distinct participants by `(seed, GroupSeed ID)`, then apply `rev_seeds` once. Tied seeds need a setup warning even with deterministic display order. Pin both TournamentPlayer and GroupSeed IDs for refreshes. Renames, scores and numeric seed corrections preserve colors; assignment, group, GroupSeed-player link or reversal changes require an explicit Setup changed transition and one coordinated remap. Validate supplied pins after authorization; they cannot grant access. There is no permanent global P1/P2 history in the current schema.

Use the primary configured Clone Hero alias, otherwise the first configured alias. Missing or malformed aliases produce Name unavailable; a missing participant produces Player not assigned. Preserve the full name, including a bracketed prefix. `[LOS]` has no verified dedicated source or meaning, so the optional overline remains null. Never parse it into a team label automatically.

**Score.** Valid assigned players with no recorded winners show `0:0`. Missing/duplicate participants, outsider winners or ambiguous round numbering require an explicit review/unavailable state, not a fabricated zero. Shared score interfaces retain their signatures and valid results; `get_score` now counts P2 only when the winner matches P2. CORP history violations are flagged independently, preserving unambiguous recorded points for review.

Remaining wins are `max(target - recorded_wins, 0)`. Missing/unsupported rules produce Target unavailable and omit remaining-win arithmetic. A finalized match shows the final result instead of active need language. Screenshot metadata never overrides referee-recorded winners.

**Panels.** Both player panels stay in opening mode until a surviving selection is evidenced by a chart, winner or verified selection kind. A blank R1 created after the last opening action does not switch panels. Both panels switch together on the first selection. Each then shows that player's latest verified personal choice by round order, or No pick recorded. Legacy unknown attribution is explained separately; a referee/automatic pick is neutral.

Keep surviving opening actions in Match details after switching panels. Historical actions with unknown phase appear as Recorded bans and saves, not Initial bans. Current effective bans exclude every ban row for a chart with a surviving `saved=True` action. This exclusion set is separate from the action list. Missing charts or owners stay explicit; deleted actions cannot be reconstructed.

## State and status contract

Match state, panel mode, evidence status and connection status are independent. Every accepted snapshot rebuilds score, current selection, latest picks and history together. Corrections may reduce scores or remove rounds.

| Recorded state | Required display |
|---|---|
| No match selected | Select a match; no invented score or per-match polling. |
| Incomplete/unsupported setup | Explicit missing/invalid information; no unsafe high/low-player property access. |
| Partial opening actions, no rounds | Opening panels; valid `0:0`; waiting for first chart; no round tiles. |
| Opening quota reached, blank R1 | Opening panels; R1 Awaiting selection; valid `0:0`. |
| Selected round without winner | Latest-pick panels; current title and verified chooser; Awaiting recorded result; pending tile. |
| Result recorded, next round absent/blank | Count the point; retain latest picks; Waiting for next round / Awaiting selection. |
| Target reached, `complete=False` | Target reached — Awaiting finalization; do not fabricate finalization. |
| `complete=True`, `finished=False` | Match complete; evidence collection still pending in details. |
| `finished=True`, `submitted=False` | Match complete; recorded evidence-completion flag and Export not recorded in details. |
| `submitted=True` | Recorded as exported; this local flag does not prove Sheets contains later corrections. |
| Contradictory flags, score or final winner | Needs review with safe known information; no silent correction. |
| Undo/correction/deletion | Recompute from surviving records; remove deleted tiles; restore earlier picks or opening mode as appropriate. |
| Deleted chart with retained provenance | Chart unavailable, preserving a valid recorded result. Legacy missing-chart history may remain ambiguous. |
| Unsupported tiebreaker | Explicit unsupported/review state; no guessed chooser or automatic choice. |

`DiscordMatch.finished` is a bot-derived target check and is not the stored `Match.finished` evidence flag. `Match.ongoing` filters on the stored flag, so it must not control whether a selected completed match remains addressable. Sources: [`DiscordMatch`](../corpoch/dbot/cogs/tourneycmds.py) and [`MatchAbstract`](../corpoch/models/match.py).

## Presentation and access boundary

The integration is an additive Django page plus GET-only fragment using one presentation builder. Routes are `/match-viewer/`, `/match-viewer/<str:match_id>/` and `/match-viewer/<str:match_id>/state/`. The index lists up to 25 authorized matches per page. Existing [live views](../corpoch/views.py) and the overlay keep their rendering path; the viewer does not consume or extend the public API contract.

The internal presentation uses primitives, string IDs and explicit nulls/reasons:

| Field group | Required contents |
|---|---|
| Envelope | `contract_version`, `match_id`, equality digest; client-owned request generation and last successful response time. |
| Context/assignment | Safe tournament/bracket/group labels; validated slot pins and assignment identity. |
| Players | Slot, seed/player IDs, display name, nullable overline, seed, recorded wins, remaining wins, latest verified pick. |
| Rules | Best-of, target, player count, ban quota, rule identifiers and support status. Fixture support is not live-profile approval. |
| Match/round state | Panel mode, match state, current round ID, ordered rounds with title visibility, kind, picker, winner and pending/result state. |
| Actions/details | Surviving opening/unknown/tiebreaker actions, saved labels, effective exclusions, rule summary, quality issues, evidence/export flags. |
| Visibility | Per-context permitted information. Withheld chart titles and identifying metadata are absent, not hidden with CSS. |

The browser formats approved values and connection age. It does not calculate official scores, targets, effective bans or sporting turns. Freshness measures time since a validated response, not time since an action occurred. The equality digest is not a revision sequence.

Every index, page and fragment read requires an authenticated **active** account, then superuser or stored admin/referee membership in the selected tournament's guild. Check active status before any superuser bypass because the existing [authentication backend](../corpoch/auth.py) can return inactive users. `is_staff`, an assigned referee, a match ID or public API access alone grants nothing. Recheck account and stored scope each time without polling Discord.

For the pilot, withhold unrevealed chart titles even from staff unless a separate scoped permission decision changes that policy. Use the selected bracket's `revealed` value; another revealed bracket cannot release its titles. Apply this to panels, details, accessibility text, attributes and digest inputs. Show Chart withheld while preserving the fact that a selection exists.

Allowed details are match context, surviving actions/results, target/rules, data-quality notes, evidence availability and local export status. Exclude account IDs, mentions, alias lists, tokens, channel/message IDs, configuration/Sheets URLs, file paths, screenshot URLs and raw `steg`. Internal TournamentPlayer/GroupSeed IDs may identify slots; they are not Discord account IDs.

The public API is not the viewer contract: `MatchSerializerLight` omits rounds and rules, and `MatchBanSerializer` omits `saved`. `MatchRoundSerializer` and `MatchSerializer` now use explicit existing field lists so new provenance/revision fields are not exposed accidentally. See [serializers](../corpoch/api/serializers.py).

## Refresh, consistency and failure handling

Native fetch sends one request at a time, scheduled after the previous response or timeout. Defaults are two seconds for active matches, ten seconds for completed matches, an eight-second request timeout and retry waits of 2/4/8/10 seconds. Active data becomes stale after ten seconds without a validated success; completed data after twenty. This small controller replaces the planned HTMX mechanism only for the new viewer, to keep cancellation and pinned identity checks local without a CDN dependency. Measure costs before rollout.

Validate match ID, contract version and client generation before installing a response. Reject login HTML, malformed data and superseded/other-match responses. Normal match navigation aborts old requests. Hidden tabs pause polling and refresh on return; age remains honest.

An unchanged digest updates freshness without replacing the DOM. Changed state replaces the whole match region, including corrected lower scores. Preserve open Match details, focus and scroll while updating details contents. Announce meaningful changes politely, not every poll. Navigation and connection status stay outside the replaced region.

Timeout, 5xx and retryable failures preserve last-good content and its age. Failed first load shows Load failed / Retry, not `0:0`. Definitive deletion, session expiry or permission loss clears protected content and stops the relevant polling. Persistent contradictory records produce Needs review after bounded handling; they must not leave an apparently current, frozen score.

The reader materializes one selected-match snapshot, including account/scope, assignments, rules, actions, rounds, chart visibility and flags. MySQL uses transaction-scoped repeatable read before the first query, with non-locking reads and connection cleanup; nested or non-autocommit reads are rejected. Native local MySQL tests verify concurrent results, corrections, removed rounds and reassigned players, plus normal/failed cleanup. Other deployments still need checks against their own connection settings. `atomic()` under read committed, repeated IDs and retry loops do not establish consistency. Keep each deployment's MySQL gate off until its disposable tests pass.

Supported CORP writers commit each transition atomically and reject stale/duplicate callbacks after reloading the match. Locks exclude Discord, file storage, screenshot decoding, Sheets and rendering. Delayed upload/review/export/admin paths publish intended fields after revalidation. Relevant model save overrides now forward save options so scoped updates cannot restore stale sporting fields. Separate bracket/rules/chart/seed configuration changes are not all coordinated writers; freeze that configuration during active pilot matches.

Viewer reads must never save models, decode files, invoke providers, send messages or enqueue tasks. Query cost must depend on the selected match, not unrelated match volume. Measure query count, duration, payload size and concurrency before selecting production limits.

## Fixture format and expected checkpoints

[`tests/fixtures/match_viewer_cases.json`](../tests/fixtures/match_viewer_cases.json) holds hand-authored inputs and independent expected answers. The top-level fields are `contract_version`, `scope`, `defaults`, `cases` and `scenario_examples`.

Each case contains `case_id`, `family_ids`, `description`, `source`, `request` and `expected`. Source includes rules, players, reversal, actions, rounds, lifecycle, visibility and access. Actions carry `action_id/num/player_id/chart_id/chart_title/saved/action_phase`; rounds carry `round_id/num/chart_id/chart_title/picked_id/selection_kind/winner_id/screenshot_present/metadata_kind`. Production reads also supply context and a shared history-validation result. Setlist IDs and loser IDs used for validation remain internal.

Expected answers cover slot identity, panel mode, state, score/target/remaining wins, current round, history, opening action IDs, effective bans, latest picks, quality and access. `wins: null` means unavailable; `wins: [0, 0]` means known zero. Scenario examples describe later transport/browser/concurrency checks; their presence does not mean those systems were tested.

The main V01 answers under the fixture-only profile are:

| Checkpoint | Actions and rounds | Panels / score / history |
|---|---|---|
| Initial bans | Four of six opening actions; no rounds. P1: Magnolia, Pacesetter. P2: Opus, Vanguard. | Opening / `0:0` / no tiles; target four, each needs four. |
| Quota complete | Six opening actions; blank R1 exists. | Opening / `0:0` / R1 Awaiting selection. |
| First player pick | All six actions; P1 selects Unwritten in R1; no winner. | Latest picks / `0:0` / R1 Pending; P2 No pick recorded. |
| Both have picked | R1 winner P2; P2 selects Trinity in R2; R2 pending. | Latest picks / `0:1` / R1 P2 and R2 Pending; remaining wins four/three. |

The later states retain all six surviving opening actions in details. With two bans per player, action four would already create blank R1; do not use that quota for the no-round opening checkpoint.

## Acceptance families

Foundation checks validate fixture structure and answer consistency without Django or services. The following families also define later integration requirements; fixture coverage alone is not evidence that production behavior passes them.

| ID | Cases and acceptance criteria |
|---|---|
| V01 Approved states | Four checkpoints above; both panels switch together; one viewer; no preview annotations. |
| V02 Score/target | Best-of 3/5/7/9 → targets 2/3/4/5; valid zero versus unavailable; remaining wins clamped; unsupported/even rules and outsider winners are explicit. |
| V03 Identity | Reversal, deferral, duplicate names, renames, numeric seed corrections, supplied pins and changed assignments; colors remain attached to validated participants. |
| V04 Null/setup | Zero/one/three/four seed rows, duplicate players, null player/user/chart, invalid alias/rules; safe placeholders and unavailable values. |
| V05 Bans/saves | Saved-chart exclusions, duplicate actions, opening versus tiebreaker/unknown phase, removal/reordering and changed rules; no reconstructed history. |
| V06 Selection | Blank R1/next round, multiple personal picks, referee/automatic/unknown choices, deleted charts and duplicate numbering; correct phase and honest attribution. |
| V07 Corrections | Changed/cleared winner, removed round, selection undo, tiebreaker undo, final metadata, duplicate/delayed callbacks; no stale restoration or double advance. Exercise actual bot undo and delayed evidence/export/admin paths later. |
| V08 Lifecycle/rules | Target reached before finalization; complete before evidence; evidence before export; inconsistent flags; every enabled chooser/defer/tiebreaker profile through undo. |
| V09 Failure/reconnect | First failure; valid `2:1` then 500/timeout/malformed response; unchanged digest; corrected lower score after recovery; honest stale thresholds without false zero. |
| V10 Request order | Delayed A after switching to B, coalesced retry, tab resume and setup change; one flight, generation checks and coherent slots. |
| V11 Text/layout | Long names/titles, Unicode, bracketed/HTML-like input; 650/1024px desktop and 470/390/320px widths, 200% zoom; escaped text with no page overflow. |
| V12 Details/accessibility | Details stays open with fresh content across ten refreshes; focus recovery after deletion; keyboard/focus; dark default, saved Light/System, forced colors, blocked storage, reduced motion. |
| V13 Scope/privacy | Correct guild staff/superuser; disabled users including existing sessions; other guild, is_staff-only, assigned-ref-only, expiry/revocation, withheld/shared chart; deny and clear as required with no title/metadata leak. |
| V14 Evidence | Missing, empty, malformed, v6/v10/manual/dummy metadata, reversed arrays, shared aliases/defaults and contradictory flags; availability only, no performance zero or score override. |
| V15 Compatibility/cost | Bot prompts/actions/embeds, old overlay controls, API light/detail/schema, export inputs, additive upgrade/rollback and bounded selected-read cost. Record measured costs later. |

Optional screenshot performance metrics need separate player/alias/context binding and validity checks before exposure. Evidence presence does not prove file availability, validity or correct player binding. Stored max streak would not be a live combo.

Visual review compares each approved checkpoint against the reference at matching width, then checks narrow layouts and accessibility. Keep large names, seed badges, plain P1/P2 labels, two activity panels, centered score, dynamic target, current selection, round tiles and expandable details. Remove montage headings, example phase controls, `B · Center score`, `Example target` and the illustrative-preview footer. Preserve the unsplit-name fallback and do not add circle/square player markers.

## Implementation status and rollout gates

Foundation, model, template and controller checks remain separate evidence. The local preview serves memory fixtures without production services, credentials or database access. A fixture test is not a MySQL consistency, OAuth, live Discord or browser acceptance test.

The implementation includes the additive migration, CORP writers, staff reader, approved templates and native refresh controller. Isolated SQLite tests exercise migrations and model behavior; controller tests cover transport state. Local MySQL, OAuth and restricted DEV Discord evidence is recorded in the development notes. Complete detailed visual/accessibility and deployment-specific access, database and cost checks before expanding the viewer. Qualify screenshot/export and full bot operation separately before enabling those services. `MATCH_VIEWER_ENABLED`, `MATCH_VIEWER_POLLING_ENABLED` and `MATCH_VIEWER_MYSQL_VERIFIED` default off. Keep the old overlay available. Disable the viewer before rolling back writers; retain migration `0030` and its provenance instead of reversing it. Resume only with verified metadata or newly created matches.

Stop for operator review on access leaks, incorrect identity/color mapping, false picker/ban claims, inconsistent scores, unresolved write races or excessive read cost. The viewer never repairs official results, resubmits exports or publishes screenshots automatically.
