# Test Discord controls against the local viewer

Version **1.7.0-beta.1**, unreleased. Complete the [local sample checkpoint](match-viewer-local-pilot.md) first. The local pilot has passed manual review of desktop and narrow layouts, 200% zoom, Windows high contrast and private Discord controls. See the [verified checkpoint](#verified-local-checkpoint) for its scope.

This checkpoint lets the signed-in sample owner pick charts and record winners in Discord while watching the existing website update. It uses the existing referee callbacks and match writers. The local database still contains the same fictional players and songs. Its opening bans remain fixed; it does not reset prior testing.

The launcher connects **Corpo Ref Bot - DEV** using the prepared private credentials. It registers only `/viewer-pilot` in the configured DEV server, preserving other commands. Controls appear only after the owner invokes that command in the configured test channel, and only that owner can see and use them. It does not run the normal `/tourney match` setup, Redis, Celery workers, a scheduler, screenshot processing or Sheets exports.

## 1. Keep the website running

Leave **Window 1 at the Ubuntu prompt**. Keep the local MySQL and website processes running using the [existing start instructions](match-viewer-staging-runtime.md#5-start-the-website-and-test-login). Open [the sample viewer](http://127.0.0.1:8766/match-viewer/local-viewer-pilot/) in the browser where you signed in.

Open a **third Windows PowerShell window** for the Discord pilot. This window stays occupied while the pilot runs. Continue to use the second PowerShell window for other checks if needed.

## 2. Check the local setup

Paste this entire block into **Window 3, Windows PowerShell**. Estimated duration: **2–10 seconds**; no GPU. It checks the owned database, stored referee access, private credential structure and actual Discord control construction. It does not connect to Discord, register commands or send messages.

```powershell
& {
    Set-Location -LiteralPath 'C:\git\corpo-ch' -ErrorAction Stop
    & ([ScriptBlock]::Create((Get-Content -LiteralPath '.\tests\staging_discord.ps1' -Raw))) -Action check
}
```

Expected result: `PASS: Owned sample, local access, database, credential structure and Discord controls are ready.` A failure leaves the viewer's match state unchanged. Keep the sanitized error text; do not share tokens, credential files or raw Discord responses.

## 3. Start the DEV session

Paste this block into **Window 3, Windows PowerShell**. Typical startup estimate: **5–30 seconds**; several slow requests can take longer. Startup depends on Discord and network availability; no GPU. The command keeps running until stopped. Do not launch another copy or run the normal bot with the same DEV token at the same time.

```powershell
& {
    Set-Location -LiteralPath 'C:\git\corpo-ch' -ErrorAction Stop
    & ([ScriptBlock]::Create((Get-Content -LiteralPath '.\tests\staging_discord.ps1' -Raw))) -Action run
}
```

Wait for:

```text
READY: Use /viewer-pilot in the DEV test channel. Controls are private to the sample owner.
```

The client verifies the authenticated bot identity before connecting to the gateway. It then verifies the DEV server, text channel, required channel permissions and current referee membership before registering its command. It requires this bot to belong only to the prepared DEV server. It refuses startup if those conditions do not hold.

## 4. Exercise the real Discord controls

In Discord, open the prepared **bot-testing channel** and select `/viewer-pilot` from **Corpo Ref Bot - DEV**. This is a separate test command; do not use another bot's `/tourney match` command. Allow **3–10 minutes** for this manual review; no GPU.

1. Confirm that only you can see the control response. It should name **Blue Player** and **Coral Player**, with the same score as the website.
2. If the current round has no chart, use the chart menu. From the opening state, Blue Player/P1 picks first. The website should show the selection within roughly two seconds and keep its score at 0–0.
3. Open **Match details** in the website. Choose **Blue Player** in the Discord winner menu. The score becomes 1–0, the next picker becomes Coral Player/P2, and Match details should stay open.
4. Choose another chart in Discord. P2's latest-pick panel should update without adding another point.
5. Use **Undo last action**. The selected chart clears. Undo again removes the pending round and previous recorded win. Another undo clears the first pick and returns the sample to its opening state. Initial bans cannot be removed in this checkpoint.

These score examples assume an opening 0–0 sample. If earlier testing changed it, the controls load its saved state. Use the visible undo control one action at a time to review or return to the opening state; no automatic reset occurs.

At four recorded wins, **Finalize result** becomes available. Finalization preserves the difference between a recorded match result and completed screenshot/export work. Reopening is supported; uploads and exports remain unavailable.

Controls expire after 15 minutes of inactivity. Reopen `/viewer-pilot` after expiration or a restart. Old controls cannot overwrite a newer match state. If a request fails after a click, inspect the website and reopen the command before retrying: the database action may have succeeded before Discord's response failed.

## 5. Stop only the DEV session

In **Window 3, Windows PowerShell**, press **Ctrl+C**. Estimated shutdown: **1–10 seconds**; no GPU. Keep the window open until the command returns. This stops the foreground pilot; the website and private MySQL server remain running. If it does not return, report the visible output instead of starting another copy.

The guild command remains listed after shutdown, but the stopped pilot does not answer it. Restarting the same command reuses that DEV command without deleting unrelated commands. The local match remains available in the website. Use the [ordered website/database stop instructions](match-viewer-staging-runtime.md#6-stop-the-two-staging-processes) when all testing is finished.

To open this guide in **Notepad++**, paste into **Windows PowerShell**. Estimated duration: **1–3 seconds**; no GPU.

```powershell
& {
    $ErrorActionPreference = 'Stop'
    Set-Location -LiteralPath 'C:\git\corpo-ch'
    $editor = 'C:\Program Files\Notepad++\notepad++.exe'
    if (-not (Test-Path -LiteralPath $editor -PathType Leaf)) {
        throw 'Notepad++ was not found in its standard installation folder.'
    }
    & $editor (Join-Path (Get-Location).Path 'docs\match-viewer-discord-pilot.md')
}
```

## Boundaries and remaining checks

Each command and component checks the expected application/server/channel, exact sample owner, fresh DEV referee membership and stored local access. Writes recheck ownership and the captured match state under the existing match lock. A confirmed missing member or removed approved role revokes this owner's local sample grant; a temporary Discord failure rejects the action without revoking it. Restoring revoked local access requires the [preparation step](match-viewer-local-pilot.md#maintainer-setup-and-rollback) with a fresh membership check. This is not continuous synchronization of all server members.

The ordinary web runtime remains unchanged. Discord message IDs stay in memory; no channel/message destination, active tournament, administrator privilege or export destination is added to the sample. The client deliberately bypasses the normal bot startup and its queues. Passing this checkpoint qualifies the reused referee callbacks through this limited gateway session; it does not qualify full bot startup/restoration, arbitrary tournament setup, screenshots, multi-user staff operation or export retries.

### Verified local checkpoint

Manual acceptance on 2026-09-28 covers the single-owner synthetic sample:

| Check | Result |
|---|---|
| DEV-channel controls and website updates | Passed in the local pilot. |
| Win target, finalization and reopening/undo | Passed in the actual DEV channel. |
| Restart of the prepared setup and resumed controls | Passed without repeating provisioning. |
| Private Discord response visibility | Confirmed during hands-on review. |
| Desktop and narrow browser layouts | Accepted in the tested local environment. |
| 200% browser zoom and Windows high contrast | Reported working during hands-on review. |

These are local manual results, not certification of every browser, viewport or contrast theme. No precise viewport dimensions, browser version or contrast-theme name were recorded for the final review. They do not approve production deployment.

The next stage is a separate DEV staff rollout: select its host and responsible operator, then verify multi-user access, role revocation, connection settings and load for that environment using the [rollout checklist](match-viewer-rollout.md). Preserve this sample as the accepted baseline; it remains restricted to its owner.

Screenshot processing, Sheets exports and ordinary bot startup remain separate integration work. They are not required to display recorded picks and winners in this local viewer.
