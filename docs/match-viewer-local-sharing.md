# Share the local DEV viewer with another tester

Version **1.7.0-beta.1**, unreleased. This uses the existing WSL database and synthetic match. Complete the accepted local pilot first; do not rerun database provisioning.

**The tester installs nothing.** Give them either approved DEV role and access to the test channel. They type `/viewer-pilot` from **Corpo Ref Bot - DEV** to get private referee controls. **Open viewer** opens the shared webpage; Discord sign-in is required for the webpage. All testers control the same saved sample, not separate matches.

Only the host runs the following commands. Keep the PC awake and connected. No router forwarding is needed. The original local website remains on port 8766; sharing uses a separate, restricted listener on port 8768. A [Cloudflare Quick Tunnel](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/) supplies a temporary HTTPS address for development testing.

## 1. Install the tunnel tool once

**Windows PowerShell. Estimated 1–3 minutes, depending on download speed; no GPU.** Skip if `cloudflared` is already installed. Accept the installer prompt if Windows asks. The package is listed in [Microsoft's WinGet repository](https://github.com/microsoft/winget-pkgs/tree/master/manifests/c/Cloudflare/cloudflared).

```powershell
Set-Location -LiteralPath 'C:\git\corpo-ch' -ErrorAction Stop
winget install --id Cloudflare.cloudflared --exact --source winget
```

After installation, open fresh PowerShell windows so they find the new command. Stop any previous foreground DEV bot with **Ctrl+C** before step 5. Do not run two bot sessions with the same token.

## 2. Keep Ubuntu and the existing database running

**Window 1, Windows PowerShell. Estimated 2–10 seconds; no GPU.** Leave this window at the Ubuntu prompt.

```powershell
Set-Location -LiteralPath 'C:\git\corpo-ch' -ErrorAction Stop
wsl.exe --distribution Ubuntu-24.04
```

**Window 2, Windows PowerShell. Estimated 2–20 seconds; no GPU.** This reuses the prepared database and accepts an already-running instance.

```powershell
& {
    $ErrorActionPreference = 'Stop'
    Set-Location -LiteralPath 'C:\git\corpo-ch'
    $branch = git branch --show-current
    if ($LASTEXITCODE -ne 0 -or $branch -ne 'jons-tree-branch') { throw 'Use jons-tree-branch.' }
    $status = wsl.exe --distribution Ubuntu-24.04 --user root --exec /usr/bin/supervisorctl -c /etc/supervisor/supervisord.conf status corpo-dev:mysql-dev
    $status_code = $LASTEXITCODE
    Write-Host ($status -join "`n")
    if ($status_code -eq 0 -and ($status -join ' ') -match '\sRUNNING\s') { return }
    if ($status_code -ne 3 -or ($status -join ' ') -notmatch '\sSTOPPED\s') { throw 'The prepared database needs attention. Report the status above.' }
    wsl.exe --distribution Ubuntu-24.04 --user root --exec /usr/bin/supervisorctl -c /etc/supervisor/supervisord.conf start corpo-dev:mysql-dev
    if ($LASTEXITCODE -ne 0) { throw 'The prepared database did not start.' }
}
```

## 3. Get the temporary HTTPS address

**Window 2, Windows PowerShell. Estimated 5–30 seconds to connect; no GPU.** Leave it running for the test.

```powershell
Set-Location -LiteralPath 'C:\git\corpo-ch' -ErrorAction Stop
cloudflared tunnel --url http://127.0.0.1:8768
```

Copy the `https://...trycloudflare.com` address printed in the window. It will not serve the viewer until step 4. If the tool reports an existing Cloudflare configuration conflict, stop and resolve that separately; do not overwrite another tunnel's configuration.

Open the **DEV application's Discord Developer Portal → OAuth2 → Redirects**. Add the copied address with **`/auth`** appended and save. For example, an address of `https://example-name.trycloudflare.com` requires `https://example-name.trycloudflare.com/auth`. Keep the existing localhost redirect. This host-only portal step is required again whenever the tunnel gets a new address.

## 4. Start the shared website

**Window 3, Windows PowerShell. Estimated 5–30 seconds; no GPU.** Paste only the HTTPS address at the prompt, without `/auth`. The check reads the owned database and private local inputs; run also verifies current DEV membership. Leave this window running.

```powershell
& {
    $ErrorActionPreference = 'Stop'
    Set-Location -LiteralPath 'C:\git\corpo-ch'
    $public_origin = (Read-Host 'Paste the HTTPS trycloudflare.com address').Trim().TrimEnd('/')
    $launcher = [ScriptBlock]::Create((Get-Content -LiteralPath '.\tests\staging_shared_web.ps1' -Raw))
    & $launcher -PublicOrigin $public_origin -Action check
    & $launcher -PublicOrigin $public_origin -Action run
}
```

## 5. Start the shared DEV bot

**Window 4, Windows PowerShell. Estimated 5–30 seconds, longer on a slow network; no GPU.** Paste the same HTTPS address. Leave this window running and wait for `READY: Use /viewer-pilot...`.

```powershell
& {
    $ErrorActionPreference = 'Stop'
    Set-Location -LiteralPath 'C:\git\corpo-ch'
    $public_origin = (Read-Host 'Paste the SAME HTTPS trycloudflare.com address').Trim().TrimEnd('/')
    $launcher = [ScriptBlock]::Create((Get-Content -LiteralPath '.\tests\staging_discord.ps1' -Raw))
    & $launcher -PublicOrigin $public_origin -Action check
    & $launcher -PublicOrigin $public_origin -Action run
}
```

In Discord, ensure the tester has **Ref** or **Corpo Collaborator**, can see the configured test channel, and can use application commands there. No website account preparation or administrator access is required. Use `/viewer-pilot` from this DEV bot, not another bot's tournament command.

Send the tester this instruction:

> In the bot-testing channel, run `/viewer-pilot` from Corpo Ref Bot - DEV. Use the private controls to pick a song and record its winner. Click **Open viewer** and sign in with your own Discord account to watch the webpage update. You don't need to install or configure anything. Everyone is using the same test match, so take turns making changes.

Sharing resumes the saved match. If it is already finalized, use **Reopen match**, then **Undo last action** as needed before selecting another song.

## 6. Verify and stop

**Browser/Discord, estimated 5–10 minutes; no GPU.** Test with someone on another network:

- Their private controls work without website login. Their webpage shows the same score after Discord login and refreshes automatically.
- A pick adds no point; recording a winner changes the correct score. Undo, corrected results, finalization and reopening appear on both browsers.
- Old controls refresh instead of overwriting another tester's newer action. Reopen `/viewer-pilot` when controls expire after 15 minutes of inactivity.
- An account without either approved role cannot use controls or view the match. Test removing both roles from a non-owner tester: bot actions are denied on the next action; the webpage clears on its next successful access-denial response after the short permission cache expires. A Discord outage shows stale/unavailable state rather than proving revocation.
- The public `/admin/`, `/api/` and other-match paths return 404. An unsigned-in request to `/match-viewer/local-viewer-pilot/` returns 401. Signing in alone does not grant access.

To stop, press **Ctrl+C** in Window 4 (bot), Window 3 (shared website), then Window 2 (tunnel), waiting for each prompt to return. Estimated **1–10 seconds per process**; no GPU. Public access closes with the tunnel. The existing database and saved match remain; use the [ordered local shutdown](match-viewer-staging-runtime.md#6-stop-the-two-staging-processes) if all testing is finished. Remove expired tunnel callbacks from the DEV portal when convenient.

Restarting a tunnel usually changes its address. Repeat steps 3–5 with that new address and callback. Restarting only the shared bot/web processes can reuse a tunnel that is still running. Existing owner-only commands without `-PublicOrigin` retain their original behavior.

## Scope and recovery

This is a small staff test of one synthetic match. It does not start normal tournament setup, workers, screenshot processing or Sheets exports. No database migration, production configuration edit or saved local viewer-gate change is required. The original owner must retain their approved role and local sample grant; use a different account for revocation tests. Confirmed removal of the owner disables the owned fixture and requires the existing [fresh-membership preparation](match-viewer-local-pilot.md#maintainer-setup-and-rollback) to restore access.

Web membership results are cached for at most ten seconds after a successful check, and five seconds after a failed check. Confirmed role/member loss removes only that tester's local grant. Network, malformed-metadata and database failures deny the current read without treating them as confirmed role removal. Grants and local account rows remain in the isolated database after shutdown, but the shared runtime always rechecks membership. No administrator bypass is available. Each bot action checks live membership; state tokens protect competing edits.

Cloudflare carries the HTTPS browser traffic. The runtime exposes only login, the fixed sample and its two viewer assets. The public hostname and forwarded HTTPS header must match; it binds only to loopback. Secure host-only cookies are separate from localhost cookies. Callback query strings are not logged by this runtime. The temporary URL is not an access credential.

The earlier 12-query measurement covers the existing viewer read, not shared-mode membership and fixture checks. Shared throughput and end-to-end Internet latency are unmeasured. Start with two or three testers. Automated checks cannot certify the real tunnel's headers, Discord portal callback, client permissions or remote browser behavior; complete the manual checks above before treating outside access as accepted.

Implementation validation on 2026-09-29: 389 guarded application tests passed on Ubuntu; Windows passed 388 with one expected Linux-only skip. This includes 40 new shared bot/web checks. The 171-test foundation suite passed with its platform skips. Both PowerShell helpers parsed, and their check modes passed against the prepared WSL database without connecting to Discord or opening a tunnel. The temporarily started database was returned to its stopped state. Independent review corrected web-server argument compatibility and malformed-membership error classification before this checkpoint.

| Symptom | Check |
|---|---|
| `cloudflared` not recognized | Open a fresh PowerShell window after successful installation. |
| Discord reports an invalid redirect | The current tunnel address plus `/auth` must be saved in this DEV application's OAuth2 redirects. |
| Tunnel error or 502 | Keep Window 3 running; confirm it started successfully and uses the same address. |
| Website 404 | Check the exact tunnel address in both commands and that the tunnel targets port 8768. |
| Website 403 or controls denied | Check the tester's active membership, approved role and channel permissions. |
| Website 503/stale | Check MySQL, owner fixture access and Discord connectivity. Preserve the sample; do not reprovision. |
| Bot already running | Stop the earlier foreground pilot and wait for its prompt before starting the shared mode. |
