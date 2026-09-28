# Prepare development service credentials

Version **1.7.0-beta.1**, unreleased. This step prepares private inputs for the development bot and test spreadsheet. It does not start the website, bot, workers or exports. Production settings and viewer switches stay unchanged.

Use this guide on the prepared development PC. Its private folder is `%USERPROFILE%\CorpoCH\staging`, outside the repository and Windows app-private AppData storage. Only the current Windows account and SYSTEM have folder access. The credential files are local plaintext, not encrypted storage. A fresh clone does not include this folder.

| File | Purpose | Action now |
|---|---|---|
| `dev-credentials.env` | Two blank development credential fields | Enter the DEV bot token and OAuth client secret. |
| `staging-resources.json` | Development bot, server, channel and test spreadsheet IDs | Leave unchanged. This inventory is not active application configuration or an access restriction. |
| `google-service-account.json` | Downloaded Google service-account key | Import the real key in step 2. No empty replacement file is provided. |
| `README.txt` | Private resource links and local paths | Reference only. |

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

Notepad++ opens four tabs. Edit only `dev-credentials.env`; the other tabs are references. In [Discord Developer Portal](https://discord.com/developers/applications), open the **dedicated development application**. Compare its Application ID with `discord_bot_id` in the `staging-resources.json` tab. Use these values from that same application:

1. **Bot token**: put the token between the quotes after `BOT_TOKEN=`.
2. **OAuth2 client secret**: put the client secret between the quotes after `BOT_SECRET=`. This is different from the bot token and the application's Public Key.
3. Press **Ctrl+S** in Notepad++ and close the credential tab.

If the existing token is not visible, obtain it privately from the development bot's owner. Coordinate before resetting a token or client secret: a reset can interrupt the existing development bot. Do not use production credentials. The [Discord OAuth2 documentation](https://docs.discord.com/developers/topics/oauth2) describes the client ID, client secret and authorization-code flow.

Keep these values out of terminal commands, chat, screenshots and the repository's `.env` file. The prepared file is not loaded by the application yet. A callback address will be supplied with the isolated staging launcher; do not change production OAuth redirects.

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

Paste this block into **Windows PowerShell** after saving both credentials and the Google key. Estimated duration: **1–3 seconds**; no GPU. It checks local file contents without printing tokens or the private key. It does not verify credentials with Discord or Google, identify the bot behind a token, or confirm spreadsheet sharing.

```powershell
& {
    $ErrorActionPreference = 'Stop'
    Set-Location -LiteralPath 'C:\git\corpo-ch'
    $staging_root = Join-Path $env:USERPROFILE 'CorpoCH\staging'
    try {
        $credential_text = Get-Content -LiteralPath (Join-Path $staging_root 'dev-credentials.env') -Raw
        foreach ($field in @('BOT_TOKEN', 'BOT_SECRET')) {
            if ($credential_text -notmatch ('(?m)^' + $field + '="[^"\r\n\s]+"\s*$')) {
                throw ('Fill ' + $field + ' between its quotes in Notepad++, save, and retry.')
            }
        }
        $google_file = Join-Path $staging_root 'google-service-account.json'
        if (-not (Test-Path -LiteralPath $google_file -PathType Leaf)) {
            throw 'The Google service-account JSON is missing. Complete step 2.'
        }
        try {
            $key = Get-Content -LiteralPath $google_file -Raw | ConvertFrom-Json
        } catch {
            throw 'The private Google key could not be read as JSON. Its contents were not displayed.'
        }
        if ($key.type -ne 'service_account' -or -not $key.client_email -or -not $key.private_key) {
            throw 'The private JSON is not a complete service-account key.'
        }
        Write-Host 'PASS: Local credential fields and Google key structure are present.' -ForegroundColor Green
        Write-Host ('Confirm test-sheet Editor access for: ' + $key.client_email)
        Write-Host 'No service connection, bot startup or export was performed.'
    } finally {
        Remove-Variable credential_text, key -ErrorAction SilentlyContinue
    }
}
```

When this passes, report **“Credentials saved; test sheet shared.”** Do not paste the files. If something fails, send only the error message after checking it contains no credentials.

## Work required before service testing

Credential preparation is the current manual checkpoint. The following implementation and configuration work must finish before starting the real application:

- A fresh staging database and media directory, an explicit local MySQL port, and a separate Redis/broker environment. The disposable MySQL checker is not a persistent staging installation. Do not copy the production database or reuse its queues.
- Enforced development application, guild, channel and spreadsheet destinations. `HOME_GUILD_ID` alone does not restrict bot activity. Normal startup restores stored matches and may synchronize commands; workers can publish stored submissions.
- Session-bound OAuth `state` validation before live staff login, plus bounded request/error handling and expiry/recovery checks. The current isolated fixes cover missing or rejected login data; they do not establish this full login boundary.
- Coordination with the development bot's owner before starting another instance, a development referee-role ID and test accounts, and registration of the exact staging OAuth callback.
- One explicit export to the test spreadsheet, with output inspected before any retry. Existing match and ban exports use separate requests; partial failure can leave output that a blind retry duplicates. Keep scheduled jobs off during the first export.

Do not use the README's normal startup commands to bypass this checkpoint. Continue with the [rollout checklist](match-viewer-rollout.md) after the isolated staging setup and login changes are reviewed.
