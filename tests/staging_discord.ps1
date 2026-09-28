param(
    [ValidateSet('check', 'run')]
    [string]$Action = 'check'
)

$ErrorActionPreference = 'Stop'
$repository_root = (Get-Location).Path
$viewer_branch = git branch --show-current
if ($LASTEXITCODE -ne 0 -or $viewer_branch -ne 'jons-tree-branch' -or
    -not (Test-Path -LiteralPath (Join-Path $repository_root 'staging\discord_pilot.py') -PathType Leaf)) {
    throw 'Open the repository root on jons-tree-branch before running this command.'
}
$private_directory = Join-Path $env:USERPROFILE 'CorpoCH\staging'
try {
    $inventory = Get-Content -LiteralPath (Join-Path $private_directory 'staging-resources.json') -Raw | ConvertFrom-Json
} catch {
    throw 'The prepared private resource inventory could not be read.'
}
foreach ($field in @('discord_bot_id', 'discord_guild_id', 'discord_test_channel_id')) {
    if ([string]$inventory.$field -notmatch '^[1-9][0-9]{16,18}$') {
        throw 'The private inventory needs valid DEV application, server and test-channel identifiers.'
    }
}
$credential_path = Join-Path $private_directory 'dev-credentials.env'
if ($inventory.credentials_file -ne $credential_path -or
    -not (Test-Path -LiteralPath $credential_path -PathType Leaf)) {
    throw 'The expected private DEV credential file is missing or the inventory points elsewhere.'
}
$linux_home = wsl.exe --distribution Ubuntu-24.04 --exec printenv HOME
if ($LASTEXITCODE -ne 0) { throw 'Ubuntu could not report its home directory.' }
$linux_home = ([string]$linux_home).Trim()
if ($linux_home -notmatch '^/home/[a-z_][a-z0-9_-]*$') { throw 'Use the prepared normal Linux account.' }
$linux_repository = wsl.exe --distribution Ubuntu-24.04 --exec wslpath -a $repository_root
if ($LASTEXITCODE -ne 0) { throw 'The repository path could not be resolved in Ubuntu.' }
$linux_credentials = wsl.exe --distribution Ubuntu-24.04 --exec wslpath -a $credential_path
if ($LASTEXITCODE -ne 0) { throw 'The private credential path could not be resolved in Ubuntu.' }
$pilot_arguments = @(
    '--distribution', 'Ubuntu-24.04', '--cd', ([string]$linux_repository).Trim(),
    '--exec', "$linux_home/CorpoCH/staging/venv/bin/python", '-B', '-m', 'staging.discord_pilot',
    '--config', "$linux_home/CorpoCH/staging/web/config.json",
    '--expected-bot-id', [string]$inventory.discord_bot_id,
    '--guild-id', [string]$inventory.discord_guild_id,
    '--channel-id', [string]$inventory.discord_test_channel_id,
    '--credentials-file', ([string]$linux_credentials).Trim(), $Action
)
if ($Action -eq 'run') {
    Write-Host 'Starting the DEV-only Discord pilot. Leave this window open; press Ctrl+C here to stop it.'
    Write-Host 'After READY appears, use /viewer-pilot in the prepared DEV test channel.'
}
wsl.exe @pilot_arguments
if ($LASTEXITCODE -ne 0) { throw 'The DEV pilot stopped with an error. Report only its sanitized output.' }
