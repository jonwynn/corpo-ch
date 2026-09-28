# Prepare development service credentials

Version **1.7.0-beta.1**, unreleased. This step prepares private inputs for the development bot and test spreadsheet. It does not start the website, bot, workers or exports. Production settings and viewer switches stay unchanged.

Use this guide on the prepared development PC. Its private folder is `%USERPROFILE%\CorpoCH\staging`, outside the repository and Windows app-private AppData storage. Only the current Windows account and SYSTEM have folder access. The credential files are local plaintext, not encrypted storage. A fresh clone does not include this folder.

| File | Purpose | Action now |
|---|---|---|
| `dev-credentials.env` | Two blank development credential fields | Enter the DEV bot token and OAuth client secret. |
| `staging-resources.json` | Development bot, server, channel and test spreadsheet IDs | Verify the IDs. For a replacement development application, update `discord_bot_id` to its Application ID. This inventory is not active application configuration or an access restriction. |
| `google-service-account.json` | Downloaded Google service-account key | Import the real key in step 2. No empty replacement file is provided. |
| `README.txt` | Private resource links and local paths | Reference only. |

## Configure the development Discord application

Use one dedicated development application for its Application ID, bot token and OAuth2 client secret. Installing the bot and assigning its server role do not save those credentials on the development PC; complete the private-file steps below as well.

In the Discord Developer Portal, enable **Guild Install** and **User Install**. The tournament commands use Guild Install; the existing screenshot context-menu commands also declare User Install. Use `applications.commands` for User Install and `bot` plus `applications.commands` for Guild Install. Install into the development server.

On the **Bot** page, enable **Server Members Intent**. The bot explicitly requests it and loads guild members and their roles. Leave Presence Intent and Message Content Intent off for the current code. Keep **Requires OAuth2 Code Grant** off for the bot installation flow; website login uses a separate authorization flow. See the [Discord Gateway documentation](https://docs.discord.com/developers/events/gateway#privileged-intents).

The full development permission profile follows the existing installation guidance and adds Read Message History for restoring match messages:

| Permission | Purpose |
|---|---|
| View Channels, Send Messages, Embed Links, Attach Files | Match messages, referee controls and evidence attachments. |
| Read Message History | Retrieve an existing match message after reconnect or restart. |
| Manage Roles | Assign tournament/group roles. Put the bot role above only the test roles it must assign. |
| Create Public Threads, Send Messages in Threads | Chart-path and screenshot-result threads. |
| Create Private Threads | Maintainer's full installation profile; no current caller has been verified to require it. |

This profile has permission value `378225675264`; it is broader than the five message/channel permissions needed for an initial referee-tool test. Administrator is not required. Check development-channel overrides after installation. Human testers also need Use Application Commands in that channel. [Discord permission reference](https://docs.discord.com/developers/topics/permissions)

For a private development bot, set **Installation > Install Link** to **None**, then **Bot > Public Bot** off. Use an explicit OAuth2 URL generated with the scopes and permissions above, selecting Guild Install. A Discord Provided Link instead uses saved default install settings. Leave **Interactions Endpoint URL** blank because the implementation receives interactions through its Gateway connection. The website OAuth callback will be supplied with the isolated staging launcher.

The bot's role is separate from the roles assigned to human referees. Record the human referee role IDs for staging setup, but do not add an extra field to `staging-resources.json`; its current format does not accept one. After applying `dbot.0006`, the guild administration form supports the existing **Discord Ref Role** and optional **Additional Discord Ref Roles**. A member with any configured active role in that guild can start a match; no administrator grant is needed. Foreign-guild and deleted roles are excluded.

After changing the selected roles, run the guild's **Update Discord Info** action in the isolated staging application, or wait for its configured guild-refresh job. A successful refresh stores the union of human members from those roles and removes stale referee membership. The website checks those stored memberships on its next request; it does not contact Discord on every viewer refresh. Changing role selection alone does not immediately refresh website access. The staging database must also contain an active tournament and an active bracket using the test channel as its score-log channel before `/tourney match` can start a match.

## 1. Enter the two Discord credentials

Open **Windows PowerShell** normally from the Start menu. Administrator access is not needed. Paste this complete block. Estimated duration: **1–5 seconds** to open Notepad++; no GPU. The example repository root is `C:\git\corpo-ch`; replace that path only if the checkout is elsewhere.

```powershell
& {
    $ErrorActionPreference = 'Stop'
    Set-Location -LiteralPath 'C:\git\corpo-ch'
    $staging_root = Join-Path $env:USERPROFILE 'CorpoCH\staging'
    $credential_file = Join-Path $staging_root 'dev-credentials.env'
    $editor = 'C:\Program Files\Notepad++\notepad++.exe'
    if (-not (Test-Path -LiteralPath $credential_file -PathType Leaf)) {
        throw 'The prepared private credential file is missing. Report this message.'
    }
    if (-not (Test-Path -LiteralPath $editor -PathType Leaf)) {
        throw 'Notepad++ was not found in its standard installation folder.'
    }
    & $editor $credential_file (Join-Path $staging_root 'staging-resources.json') (Join-Path $staging_root 'README.txt') '.\docs\match-viewer-staging.md'
}
```

Notepad++ opens four tabs. In [Discord Developer Portal](https://discord.com/developers/applications), open the **dedicated development application**. Compare its Application ID with `discord_bot_id` in the `staging-resources.json` tab. If this is a new or replacement application, replace only that ID's value, preserving its JSON quotation marks and the other fields. Save the inventory. Keep the existing guild, channel and spreadsheet IDs when those test destinations are unchanged. The README and guide tabs are references.

In `dev-credentials.env`, use these values from that same application:

1. **Bot token**: open the application's **Bot** page and put its token between the quotes after `BOT_TOKEN=`. Paste only the token, without a `Bot ` prefix or spaces.
2. **OAuth2 client secret**: put the client secret between the quotes after `BOT_SECRET=`. This is different from the bot token and the application's Public Key.
3. Press **Ctrl+S** in Notepad++ and close the credential tab.

For a newly created application, use its own credentials; the original Corpo application's credentials are not needed. If someone else manages the selected development application, obtain its credentials privately from that owner. Coordinate before resetting credentials or starting a second instance of the same application. Do not use production credentials. The [Discord OAuth2 documentation](https://docs.discord.com/developers/topics/oauth2) describes the client ID, client secret and authorization-code flow.

Keep these values out of terminal commands, chat, screenshots and the repository's `.env` file. The prepared file is not loaded by the application yet. A callback address will be supplied with the isolated staging launcher; do not change production OAuth redirects.

If the offline preflight passes but step 4 reports **Discord bot identity: credentials were rejected**, Discord rejected the saved `BOT_TOKEN`. The preflight checks file structure, not whether a credential works. Reopen the private file with the command above and replace only `BOT_TOKEN` with the token for the selected development application. The application ID, Public Key and OAuth2 client secret cannot replace it. If the current token is unavailable, use **Reset Token** on that application's Bot page, then save the new token; resetting invalidates the previous token for that application. See [Discord's credential instructions](https://docs.discord.com/developers/quick-start/getting-started#fetching-your-credentials). Save with **Ctrl+S**, close the credential tab and rerun step 4. Keep `BOT_SECRET` and the Google key unchanged unless their own checks require a correction.

## 2. Save the Google service-account key

The application exports through a **Google service account**, which is an account for software. Browser editor access to the spreadsheet does not authenticate that account.

Use a dedicated test Google Cloud project/service account with no access to production spreadsheets. If the owner already has one, ask for its JSON key through a private method. Otherwise, follow [gspread's service-account setup](https://docs.gspread.org/en/latest/oauth2.html#for-bots-using-service-account): enable the Google Sheets and Google Drive APIs in the test project, create the service account, and download a JSON key from its **Keys** tab. Do not grant project-wide Editor or Owner roles just to edit a spreadsheet. If account policy prevents creating keys, report that restriction instead of changing the policy.

Once the JSON file is downloaded, paste this block into **Windows PowerShell**. Estimated duration: **5–30 seconds**, including choosing the file; no GPU. A file picker opens. Select the downloaded service-account JSON and click **Open**. The block checks its basic structure and copies it into the private folder without overwriting an existing key. It contacts no Google service.

```powershell
& {
    $ErrorActionPreference = 'Stop'
    Set-Location -LiteralPath 'C:\git\corpo-ch'
    $staging_root = Join-Path $env:USERPROFILE 'CorpoCH\staging'
    if (-not (Test-Path -LiteralPath $staging_root -PathType Container)) {
        throw 'The prepared private staging folder is missing. Report this message.'
    }
    $destination = Join-Path $staging_root 'google-service-account.json'
    if (Test-Path -LiteralPath $destination) {
        throw 'A private Google key already exists. Leave it in place and continue to step 3.'
    }
    Add-Type -AssemblyName System.Windows.Forms
    $picker = New-Object System.Windows.Forms.OpenFileDialog
    $picker.Title = 'Choose the downloaded TEST Google service-account JSON'
    $picker.Filter = 'JSON files (*.json)|*.json'
    try {
        if ($picker.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) {
            throw 'No file selected. Nothing was copied.'
        }
        try {
            $key = Get-Content -LiteralPath $picker.FileName -Raw | ConvertFrom-Json
        } catch {
            throw 'The selected file could not be read as JSON. Its contents were not displayed.'
        }
        if ($key.type -ne 'service_account' -or -not $key.client_email -or -not $key.private_key) {
            throw 'The selected JSON is not a complete service-account key.'
        }
        [System.IO.File]::Copy($picker.FileName, $destination, $false)
        Write-Host 'PASS: Google key copied into the private staging folder.' -ForegroundColor Green
        Write-Host ('Share only the test spreadsheet with this account: ' + $key.client_email)
    } finally {
        $picker.Dispose()
        Remove-Variable key -ErrorAction SilentlyContinue
    }
}
```

Open the blank test spreadsheet using its link in the private `README.txt`. Click **Share**, add the service-account email printed above, choose **Editor**, and save. An email ending in `iam.gserviceaccount.com` identifies this software account. Do not share any production spreadsheet with it. After verifying the private copy, remove the extra downloaded key through File Explorer if it is no longer needed.

The existing exporter creates its named tabs when first used, including Match Data, Bans Data and Player Data. Leave the blank spreadsheet unchanged for now; headers and test rows will be verified during a later controlled export.

## 3. Check that the files are ready

Paste this block into **Windows PowerShell**. Estimated duration: **1–3 seconds** with the prepared Python environment; no GPU. The preflight reads the private inventory, validates its resource IDs and local file paths, and reports all missing credential inputs. It does not print tokens or private keys, load application settings, write files, or contact any service. It cannot verify the bot behind a token or spreadsheet sharing.

```powershell
& {
    $ErrorActionPreference = 'Stop'
    Set-Location -LiteralPath 'C:\git\corpo-ch'
    $staging_root = Join-Path $env:USERPROFILE 'CorpoCH\staging'
    $viewer_branch = git branch --show-current
    if ($LASTEXITCODE -ne 0 -or $viewer_branch -ne 'jons-tree-branch') {
        throw 'This folder is not on jons-tree-branch. Stop here.'
    }
    .\.venv\Scripts\python.exe -B -m tests.staging_preflight --resources (Join-Path $staging_root 'staging-resources.json')
    if ($LASTEXITCODE -ne 0) {
        throw 'Staging inputs are not ready. Complete the listed items and run this block again.'
    }
}
```

Inventory validity and credential readiness are separate results. A valid inventory can still produce `NOT READY` for blank Discord fields or a missing Google key, even after the bot is installed successfully. The check validates local structure; it cannot confirm that a token belongs to the inventory's application ID. Exit code `0` means the local inputs passed; code `2` means something is missing or invalid. Neither result starts an application or grants access to the destinations.

When the full check passes and sheet sharing is complete, report **“Credentials saved; test sheet shared.”** Do not paste the files. If something fails, send only the error message after checking it contains no credentials.

## 4. Verify development identities and access

This optional command contacts Discord and Google. It authenticates the saved bot token and Google service-account key, then reads metadata for the explicitly selected development channel, roles and spreadsheet. It does not start the bot or application, read spreadsheet cells, send messages, change a database, or export results. Google authentication uses read-only scopes; reported edit capabilities describe the account's access, not a successful test write. The OAuth client secret is still verified later through browser login.

Run this only after step 3 passes. Independently obtain the application, server, channel and test spreadsheet IDs from their intended development resources. The checker compares all four with the private inventory before reading credentials or contacting services. Do not automatically copy all expected IDs from that inventory: the comparison is intended to catch a wrong destination.

Find the application ID under **Developer Portal > General Information**. For Discord server, channel and role IDs, enable **User Settings > Advanced > Developer Mode**, then use the corresponding **Copy ID** action. The spreadsheet ID is the part of its address between `/d/` and `/edit`. Human role IDs are optional for this checker: press **Enter** without entering any to skip validation of specific roles. Supply all intended referee role IDs before the referee pilot.

Paste this block into **Windows PowerShell**. Estimated duration: **10–60 seconds**, including entering IDs; slow services may take longer. No GPU. It asks only for resource IDs, never secrets. Separate multiple human referee role IDs with commas.

```powershell
& {
    $ErrorActionPreference = 'Stop'
    Set-Location -LiteralPath 'C:\git\corpo-ch'
    $viewer_branch = git branch --show-current
    if ($LASTEXITCODE -ne 0 -or $viewer_branch -ne 'jons-tree-branch') {
        throw 'This folder is not on jons-tree-branch. Stop here.'
    }
    $staging_root = Join-Path $env:USERPROFILE 'CorpoCH\staging'
    $probe_arguments = @(
        '--resources', (Join-Path $staging_root 'staging-resources.json'),
        '--expected-bot-id', (Read-Host 'DEV application ID'),
        '--expected-guild-id', (Read-Host 'DEV server ID'),
        '--expected-channel-id', (Read-Host 'DEV test text-channel ID'),
        '--expected-spreadsheet-id', (Read-Host 'Blank test spreadsheet ID, not its full link')
    )
    $referee_roles = Read-Host 'Human referee role IDs, separated by commas, or Enter to skip'
    foreach ($referee_role in ($referee_roles -split ',')) {
        if (-not [string]::IsNullOrWhiteSpace($referee_role)) {
            $probe_arguments += @('--referee-role-id', $referee_role.Trim())
        }
    }
    .\.venv\Scripts\python.exe -B -m tests.staging_service_check @probe_arguments
    if ($LASTEXITCODE -ne 0) {
        throw 'Service verification is incomplete. Report the sanitized check output.'
    }
}
```

Success reports the expected bot identity, Server Members Intent configuration, channel metadata, human role existence when requested, Google authentication, the expected spreadsheet and edit-capability metadata. Failures stop the sequence and retain completed check labels. No response body or credential is printed. The transport rejects redirects, limits response size, uses bounded connection/read waits and does not automatically retry service operations.

| Result | Next action |
|---|---|
| Inventory does not match expected DEV resources | Correct the intended IDs before retrying; no credentials were read. |
| Discord identity rejected or mismatched | Check the new application's saved bot token; do not use the original application's token. |
| Server Members Intent missing | Enable it on the DEV application's Bot page. |
| Test channel or referee role unavailable | Check the server/channel IDs, bot membership and the human role IDs. |
| Google authentication failed | Check the downloaded test key and Windows clock. |
| Sheets or Drive access denied/unavailable | Enable the corresponding API in the service-account project and share only the test sheet with that account. |
| Google does not report edit permission | Give the service account Editor access to the test sheet and check file restrictions. |

A pass does not verify effective channel write permissions, individual human memberships, Gateway startup, the OAuth callback or export formatting/protected ranges. Those remain controlled staging checks. Role discovery alone does not configure the application's referee authorization.

After all read-only checks pass, continue with [local Linux runtime preparation](match-viewer-staging-runtime.md). The Windows pilot uses Ubuntu on WSL 2 for the complete service runtime; installing only a broker would leave native Windows Celery unsupported. The existing Windows preview and isolated checks remain available.

## Login changes available for staging

Discord login begins at the local `/auth/start` route. It binds a short-lived authorization attempt to the browser session and verifies the callback before exchanging its code. The existing `/auth` callback address stays in use. Restart login from the website after upgrading; an older authorization link without the new state value is rejected.

The login flow uses Django's database session backend and existing session table, with no new migration. Expired auxiliary login sessions are removed by the normal session cleanup procedure. Other session backends require separate support and verification before using this flow. The configured authorization URL must use Discord's HTTPS authorization endpoint and match the configured application ID and redirect URI.

Discord requests now have connection/read timeouts and sanitized failure messages. Expired credentials are refreshed before requesting identity or guild data; temporary failures leave stored credentials available for a later attempt. Disabled accounts cannot sign in or restore an authenticated session. These behaviors are covered by isolated tests; actual consent, callback registration, service availability and account permissions still need the controlled login pilot.

Browser and scheduled renewal coordinate on the same token row. Renewal holds that row while waiting for Discord, with 5-second connection and 15-second read timeouts; these are not a total request deadline. Match rows are not locked by this operation. The native MySQL tests verify the controlled overlap cases with mocked HTTP; the pilot must also check login latency and database timeout behavior under its own settings.

## Work required before service testing

Credential preparation and read-only verification precede runtime setup. The following implementation and configuration work must finish before starting the real application:

- A fresh staging database and media directory, an explicit local MySQL port, and a separate Redis/broker environment. The disposable MySQL checker is not a persistent staging installation. Do not copy the production database or reuse its queues.
- Enforced development application, guild, channel and spreadsheet destinations. `HOME_GUILD_ID` alone does not restrict bot activity. Normal startup restores stored matches and may synchronize commands; workers can publish stored submissions.
- Live verification of the new session-bound OAuth flow, expiry recovery and failure behavior against the development application. Isolated tests do not establish remote credential acceptance.
- A development human-referee role ID and test accounts, and registration of the exact staging OAuth callback. If the same development application already runs elsewhere, coordinate before starting another instance. A separate self-owned application does not require the original application's credentials.
- One explicit export to the test spreadsheet, with output inspected before any retry. Existing match and ban exports use separate requests; partial failure can leave output that a blind retry duplicates. Keep scheduled jobs off during the first export.

Do not use the README's normal startup commands to bypass this checkpoint. Continue with the [rollout checklist](match-viewer-rollout.md) after the isolated staging setup is reviewed.
