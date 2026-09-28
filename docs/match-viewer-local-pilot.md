# Try the local sample match

Version **1.7.0-beta.1**, unreleased. Complete the [local login setup](match-viewer-staging-runtime.md) first. These commands operate the prepared WSL sample; they do not provision a fresh clone.

The current desktop appearance has been accepted. The next integration check is the [restricted DEV Discord session](match-viewer-discord-pilot.md), which uses the same sample and viewer. Detailed narrow-screen, zoom and high-contrast review remains separate.

The sample uses the real MySQL database, match actions, staff access checks and viewer. Its players and charts are fictional. The website remains read-only. Picks and results below change only `local-viewer-pilot` in the isolated staging schema. No Discord messages, game server, background worker or spreadsheet exports are involved.

Keep **Window 1 at its Ubuntu prompt** and run commands in **Window 2, normal Windows PowerShell**. Keep the two local staging processes running using the existing start instructions. Open [the sample match](http://127.0.0.1:8766/match-viewer/local-viewer-pilot/) in the browser where Discord sign-in succeeded. The local home page also has an **Open sample match viewer** link.

## Watch picks and results

Each block below is for **Windows PowerShell**. Estimated duration: **2–10 seconds per block**, plus the refresh interval: **2 seconds** for an active match or **10 seconds** after completion; no GPU. Run one block at a time and observe the browser before continuing. The helper obtains a current state token and refuses an intervening change instead of overwriting it. It reads the DEV application ID from the prepared private resource inventory, not from production settings.

1. Inspect the current sample. This does not change any match records.

```powershell
& {
    Set-Location -LiteralPath 'C:\git\corpo-ch' -ErrorAction Stop
    & ([ScriptBlock]::Create((Get-Content -LiteralPath '.\tests\staging_sample.ps1' -Raw))) -Action inspect
}
```

2. Pick the next available chart for the player whose turn it is. From the opening state, P1 picks first. The score stays **0–0** until a winner is recorded.

```powershell
& {
    Set-Location -LiteralPath 'C:\git\corpo-ch' -ErrorAction Stop
    & ([ScriptBlock]::Create((Get-Content -LiteralPath '.\tests\staging_sample.ps1' -Raw))) -Action pick
}
```

3. Record P1 as the winner of the selected chart. From the first round, the score becomes **1–0** and P2 becomes the next picker. Open **Match details** before running this block to check that it stays open during refresh.

```powershell
& {
    Set-Location -LiteralPath 'C:\git\corpo-ch' -ErrorAction Stop
    & ([ScriptBlock]::Create((Get-Content -LiteralPath '.\tests\staging_sample.ps1' -Raw))) -Action win-p1
}
```

4. Run the **pick** block again. P2's panel now shows its latest pick and round number. The earlier opening actions remain in Match details. Under the confirmed loser-picks rule, this P2 selection follows a P1 win; the old preview's illustrated score is not the rule source.

5. To undo the most recent sample action, run this block. Undoing a selected chart clears that selection; undo again removes the pending round and its preceding recorded result. Undo cannot remove the sample's four opening bans.

```powershell
& {
    Set-Location -LiteralPath 'C:\git\corpo-ch' -ErrorAction Stop
    & ([ScriptBlock]::Create((Get-Content -LiteralPath '.\tests\staging_sample.ps1' -Raw))) -Action undo
}
```

Other supported actions are `win-p2` and `finalize`. Finalization is allowed only after a player reaches four recorded wins; it does not mark screenshot evidence or spreadsheet export complete. Invalid sequencing returns an error and preserves the match. There is no automatic reset: the sample retains its current state across restarts.

If the page says **Automatic updates are off**, reload it after each action. That is the manual checkpoint. A maintainer enables `serve-viewer-live` only after database and access verification; ordinary `serve` remains login-only.

## What to review

- Player names, blue/coral separation, score and chart names are readable at your normal window size and zoom.
- First selection changes both player panels from opening actions to latest picks without awarding a win.
- A recorded result updates the score, remaining wins, current selection and round tiles together.
- Expanded details survive automatic refresh and contain current data.
- Undo can lower a score without swapping player colors or leaving an old winner tile behind.

The local role check is a **snapshot of Discord membership at preparation time**. It uses the official [Get Guild Member endpoint](https://docs.discord.com/developers/resources/guild#get-guild-member). The website reloads its stored local permissions on each request, but no bot is running to synchronize later Discord role changes. To remove this pilot account's local access immediately, use **normal Windows PowerShell**, estimated **2–10 seconds**, no GPU:

```powershell
& {
    Set-Location -LiteralPath 'C:\git\corpo-ch' -ErrorAction Stop
    & ([ScriptBlock]::Create((Get-Content -LiteralPath '.\tests\staging_sample.ps1' -Raw))) -Action revoke
}
```

The next viewer request is denied. Restoring access requires rerunning preparation with a fresh DEV membership check. This command does not remove any Discord role. To stop the website and private MySQL server, use [the existing ordered shutdown steps](match-viewer-staging-runtime.md#6-stop-the-two-staging-processes).

## Maintainer setup and rollback

`python -m staging.pilot` uses the same private `--config` and independently supplied `--expected-bot-id` as the web launcher. The `prepare` command also requires `--credentials-file`, `--guild-id`, repeated `--referee-role-id`, and the exact `--account-name` shown after sign-in. It confirms the DEV bot identity and current human role before creating any fixture or permission record. A retry validates ownership and preserves gameplay. It refuses existing unrelated tournament/match data or a changed fixture. The synthetic tournament is inactive, with no channel, message, score-log, game file or export destination configured.

After native MySQL verification and successful `inspect`, change only the local Supervisor web command from `serve` to `serve-viewer`, then to `serve-viewer-live` after manual rendering/access checks. Retain the original profile outside the repository. Stop the website before its private MySQL process, reload only the `corpo-dev` group, then start MySQL before the website. Supervisor group updates restart the group's members. These explicit modes validate the owned database and sample graph before enabling viewer gates; they never read bot credentials into the web process.

Rollback changes that web command back to `serve` and reloads the same local group. It disables viewer access without removing OAuth sessions, fixture history, permissions or migrations. `revoke` separately removes only the fixture owner's local referee grant. Existing production configuration and all ordinary viewer defaults remain unchanged.

Single-request query counts and durations printed by `inspect` describe this sample only. Broader concurrency, unrelated-match-volume measurements under deployment settings, multi-user role synchronization, real bot actions, uploads, exports and operating-system accessibility acceptance remain later rollout checks.
