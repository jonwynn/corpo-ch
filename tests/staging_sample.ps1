param(
    [ValidateSet('inspect', 'pick', 'win-p1', 'win-p2', 'undo', 'finalize', 'revoke')]
    [string]$Action = 'inspect'
)

$ErrorActionPreference = 'Stop'
$repository_root = (Get-Location).Path
$viewer_branch = git branch --show-current
if ($LASTEXITCODE -ne 0 -or $viewer_branch -ne 'jons-tree-branch' -or
    -not (Test-Path -LiteralPath (Join-Path $repository_root 'staging\pilot.py') -PathType Leaf)) {
    throw 'Open the repository root on jons-tree-branch before running this command.'
}
$inventory_path = Join-Path $env:USERPROFILE 'CorpoCH\staging\staging-resources.json'
try {
    $inventory = Get-Content -LiteralPath $inventory_path -Raw | ConvertFrom-Json
} catch {
    throw 'The prepared private resource inventory could not be read.'
}
$expected_bot_id = [string]$inventory.discord_bot_id
if ($expected_bot_id -notmatch '^[1-9][0-9]{16,18}$') {
    throw 'The resource inventory needs the independently confirmed DEV application ID.'
}
$linux_home = wsl.exe --distribution Ubuntu-24.04 --exec printenv HOME
if ($LASTEXITCODE -ne 0) { throw 'Ubuntu could not report its home directory.' }
$linux_home = ([string]$linux_home).Trim()
if ($linux_home -notmatch '^/home/[a-z_][a-z0-9_-]*$') {
    throw 'Use the prepared normal Linux account.'
}
$linux_repository = wsl.exe --distribution Ubuntu-24.04 --exec wslpath -a $repository_root
if ($LASTEXITCODE -ne 0) { throw 'The repository path could not be resolved in Ubuntu.' }
$linux_repository = ([string]$linux_repository).Trim()
$base_arguments = @(
    '--distribution', 'Ubuntu-24.04', '--cd', $linux_repository,
    '--exec', "$linux_home/CorpoCH/staging/venv/bin/python", '-B', '-m', 'staging.pilot',
    '--config', "$linux_home/CorpoCH/staging/web/config.json",
    '--expected-bot-id', $expected_bot_id
)

if ($Action -eq 'revoke') {
    wsl.exe @base_arguments revoke
    if ($LASTEXITCODE -ne 0) { throw 'Local sample access could not be revoked. Report the output.' }
    return
}
$inspection = @(wsl.exe @base_arguments inspect)
if ($LASTEXITCODE -ne 0) {
    $inspection | Write-Host
    throw 'Sample inspection failed. Keep both staging processes running and report the output.'
}
if ($Action -eq 'inspect') {
    $inspection | Write-Host
    return
}
$token_lines = @($inspection | Where-Object { $_ -match '^\{' })
if ($token_lines.Count -ne 1) { throw 'The sample did not supply one current action token.' }
try {
    $snapshot = $token_lines[0] | ConvertFrom-Json
} catch {
    throw 'The sample action token could not be read.'
}
if ($snapshot.match -ne 'local-viewer-pilot' -or $snapshot.action_token -notmatch '^[0-9a-f]{64}$') {
    throw 'The current state does not belong to the prepared sample match.'
}
wsl.exe @base_arguments --expected-state $snapshot.action_token $Action
if ($LASTEXITCODE -ne 0) { throw 'The sample action did not complete. Report the output; do not repeat it blindly.' }
