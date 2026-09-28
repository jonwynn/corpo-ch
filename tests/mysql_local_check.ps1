# Runs native checks against the separately prepared, private local MySQL instance.
# Invoke from the repository root. This script does not install or configure MySQL.

$previous_error_preference = $ErrorActionPreference
$ErrorActionPreference = 'Stop'
$server_process = $null
$shutdown_process = $null
$failure_message = $null
$shutdown_message = $null
$checks_passed = $false
$current_step = 'checking local prerequisites'
$environment_names = @(
    'MYSQL_TEST_HOST', 'MYSQL_TEST_PORT', 'MYSQL_TEST_USER',
    'MYSQL_TEST_PASSWORD', 'MYSQL_PWD'
)
$previous_environment = @{}
foreach ($environment_name in $environment_names) {
    $previous_environment[$environment_name] = [Environment]::GetEnvironmentVariable(
        $environment_name, 'Process'
    )
}

function stop_local_check {
    param([string]$message)

    $failure = New-Object System.InvalidOperationException($message)
    $failure.Data['corpo_local_check_message'] = $true
    throw $failure
}

function read_private_item {
    param(
        [string]$item_path,
        [string]$item_label
    )

    try {
        return Get-Item -LiteralPath $item_path -Force -ErrorAction Stop
    }
    catch [Management.Automation.ItemNotFoundException], [Management.Automation.DriveNotFoundException] {
        stop_local_check "$item_label was not found: $item_path. Check that local MySQL setup is complete for the Windows account shown above."
    }
    catch [UnauthorizedAccessException], [Security.SecurityException] {
        stop_local_check "Windows denied access to $item_label at $item_path. Check this account's access to the private MySQL folder."
    }
    catch {
        stop_local_check "Could not inspect $item_label at $item_path ($($_.Exception.GetType().Name))."
    }
}

function validate_private_root {
    param([string]$private_root)

    $root_item = read_private_item $private_root 'The private MySQL folder'
    if (-not $root_item.PSIsContainer) {
        stop_local_check "The private MySQL folder is a file instead of a directory: $private_root."
    }
    if (($root_item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        stop_local_check 'Private MySQL paths must not contain symbolic links or junctions.'
    }
}

function validate_private_path {
    param(
        [string]$private_root,
        [string]$candidate_path,
        [bool]$is_directory = $false,
        [string]$item_label = 'A required private MySQL item'
    )

    if ([string]::IsNullOrWhiteSpace($candidate_path) -or
        -not [IO.Path]::IsPathRooted($candidate_path)) {
        stop_local_check 'A private instance path is missing or is not absolute.'
    }
    $resolved_path = [IO.Path]::GetFullPath($candidate_path).TrimEnd('\')
    $root_prefix = $private_root.TrimEnd('\') + '\'
    if (-not $resolved_path.StartsWith($root_prefix, [StringComparison]::OrdinalIgnoreCase)) {
        stop_local_check 'A private instance path falls outside the prepared MySQL folder.'
    }
    $path_item = read_private_item $resolved_path $item_label
    if ([bool]$path_item.PSIsContainer -ne $is_directory) {
        $expected_type = if ($is_directory) { 'directory' } else { 'file' }
        stop_local_check "$item_label must be a $expected_type`: $resolved_path."
    }
    $current_path = $resolved_path
    while ($current_path.Length -ge $private_root.Length) {
        if (($path_item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            stop_local_check 'Private MySQL paths must not contain symbolic links or junctions.'
        }
        if ($current_path.Equals($private_root, [StringComparison]::OrdinalIgnoreCase)) {
            break
        }
        $current_path = [IO.Path]::GetDirectoryName($current_path)
        $path_item = read_private_item $current_path 'A private MySQL parent folder'
    }
    return $resolved_path
}

function read_private_credential {
    param([string]$credential_path)

    try {
        return Import-Clixml -LiteralPath $credential_path -ErrorAction Stop
    }
    catch {
        $credential_name = [IO.Path]::GetFileName($credential_path)
        stop_local_check "The saved credential file '$credential_name' could not be opened. It may belong to another Windows account or be damaged. Check the account shown above and repair the private test credentials if needed."
    }
}

function get_mysql_listeners {
    # Query all addresses so an IPv6 or wildcard listener cannot be overlooked.
    return @(Get-NetTCPConnection -State Listen -ErrorAction Stop | Where-Object {
        $_.LocalPort -eq 3307
    })
}

function test_owned_listener {
    param([int]$process_id)

    $listeners = @(get_mysql_listeners)
    if ($listeners.Count -eq 0) {
        return $false
    }
    if (@($listeners | Where-Object { $_.OwningProcess -ne $process_id }).Count -gt 0) {
        stop_local_check 'Port 3307 belongs to another process. It will not be contacted or stopped.'
    }
    if (@($listeners | Where-Object { $_.LocalAddress -ne '127.0.0.1' }).Count -gt 0) {
        stop_local_check 'The prepared server is not listening only on the expected loopback address.'
    }
    return $true
}

try {
    if (-not (Get-Command git -ErrorAction SilentlyContinue) -or
        -not (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue)) {
        stop_local_check 'Git and Windows TCP connection inspection must be available.'
    }
    $repository_root = (Get-Location).ProviderPath
    $git_root = git rev-parse --show-toplevel 2>$null
    if ($LASTEXITCODE -ne 0 -or
        -not [IO.Path]::GetFullPath($git_root).Equals(
            [IO.Path]::GetFullPath($repository_root), [StringComparison]::OrdinalIgnoreCase
        )) {
        stop_local_check 'Open the repository root before running this command.'
    }
    $current_branch = git branch --show-current
    if ($LASTEXITCODE -ne 0 -or $current_branch -ne 'jons-tree-branch') {
        stop_local_check 'The repository must be on jons-tree-branch. Do not reset or switch it.'
    }
    $python_path = Join-Path $repository_root '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python_path -PathType Leaf)) {
        stop_local_check 'The prepared Python environment is missing.'
    }
    if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        stop_local_check 'Windows LOCALAPPDATA is unavailable.'
    }

    $current_step = 'verifying the prepared private MySQL instance'
    $private_root = [IO.Path]::GetFullPath(
        (Join-Path $env:LOCALAPPDATA 'CorpoCH\mysql-test')
    ).TrimEnd('\')
    Write-Host ("Windows account: {0}" -f [Security.Principal.WindowsIdentity]::GetCurrent().Name)
    Write-Host "Private MySQL folder: $private_root"
    validate_private_root $private_root
    $metadata_path = validate_private_path $private_root (Join-Path $private_root 'instance.json') $false 'The instance.json metadata file'
    $configuration_path = validate_private_path $private_root (Join-Path $private_root 'server.ini') $false 'The server.ini configuration file'
    $runner_path = validate_private_path $private_root (Join-Path $private_root 'runner.clixml') $false 'The runner.clixml credential file'
    $administrator_path = validate_private_path $private_root (Join-Path $private_root 'admin.clixml') $false 'The admin.clixml credential file'
    $metadata = Get-Content -LiteralPath $metadata_path -Raw | ConvertFrom-Json
    if ($metadata.purpose -ne 'corpo-match-viewer-local-tests' -or
        $metadata.version -ne '8.4.11' -or $metadata.port -ne 3307) {
        stop_local_check 'The private MySQL metadata does not match the prepared validation instance.'
    }
    $server_path = validate_private_path $private_root $metadata.server_path $false 'The mysqld.exe server executable'
    $data_directory = validate_private_path $private_root $metadata.data_directory $true 'The MySQL data directory'
    if ([IO.Path]::GetFileName($server_path) -ne 'mysqld.exe' -or
        $metadata.server_sha256 -notmatch '^[0-9a-fA-F]{64}$' -or
        (Get-FileHash -LiteralPath $server_path -Algorithm SHA256).Hash -ne $metadata.server_sha256) {
        stop_local_check 'The prepared MySQL server filename or SHA256 verification failed.'
    }
    if ($metadata.configuration_sha256 -notmatch '^[0-9a-fA-F]{64}$' -or
        (Get-FileHash -LiteralPath $configuration_path -Algorithm SHA256).Hash -ne $metadata.configuration_sha256) {
        stop_local_check 'The prepared MySQL configuration SHA256 verification failed.'
    }
    $administrator_client = validate_private_path $private_root (
        Join-Path ([IO.Path]::GetDirectoryName($server_path)) 'mysqladmin.exe'
    ) $false 'The mysqladmin.exe shutdown executable'
    $runner_credential = read_private_credential $runner_path
    $administrator_credential = read_private_credential $administrator_path
    if ($runner_credential -isnot [Management.Automation.PSCredential] -or
        $runner_credential.UserName -ne 'corpo_viewer_check' -or
        $administrator_credential -isnot [Management.Automation.PSCredential] -or
        $administrator_credential.UserName -ne 'root') {
        stop_local_check 'The private credentials do not match the prepared test accounts.'
    }
    if (@(get_mysql_listeners).Count -gt 0) {
        stop_local_check 'Port 3307 is already in use. No existing server will be attached to or stopped.'
    }

    $current_step = 'starting the owned local MySQL server'
    Write-Host 'Starting the prepared local MySQL server on 127.0.0.1:3307.'
    $server_process = Start-Process -FilePath $server_path -ArgumentList @(
        ('--defaults-file="{0}"' -f $configuration_path), '--no-monitor',
        ('--datadir="{0}"' -f $data_directory), '--bind-address=127.0.0.1',
        '--port=3307', '--mysqlx=OFF'
    ) -WorkingDirectory $private_root -WindowStyle Hidden -PassThru
    $ready_deadline = [DateTime]::UtcNow.AddSeconds(45)
    $server_ready = $false
    while ([DateTime]::UtcNow -lt $ready_deadline) {
        $server_process.Refresh()
        if ($server_process.HasExited) {
            stop_local_check 'The prepared MySQL server exited before becoming ready. Review its private server log.'
        }
        if (test_owned_listener $server_process.Id) {
            $server_ready = $true
            break
        }
        Start-Sleep -Milliseconds 250
    }
    if (-not $server_ready) {
        stop_local_check 'The prepared MySQL server did not become ready within 45 seconds.'
    }

    $current_step = 'running the five isolated MySQL checks'
    $env:MYSQL_TEST_HOST = '127.0.0.1'
    $env:MYSQL_TEST_PORT = '3307'
    $env:MYSQL_TEST_USER = $runner_credential.UserName
    $env:MYSQL_TEST_PASSWORD = $runner_credential.GetNetworkCredential().Password
    [Environment]::SetEnvironmentVariable('MYSQL_PWD', $null, 'Process')
    Write-Host 'Running five checks in a newly generated, disposable validation database.'
    & $python_path -m tests.mysql_viewer_check --allow-create-test-database
    if ($LASTEXITCODE -ne 0) {
        stop_local_check 'The native MySQL checks did not pass. Review the check output above.'
    }
    $checks_passed = $true
}
catch {
    if ($_.Exception.Data['corpo_local_check_message']) {
        $failure_message = $_.Exception.Message
    }
    else {
        $failure_message = "Local MySQL validation failed while $current_step ($($_.Exception.GetType().Name))."
    }
}
finally {
    try {
        if ($null -ne $server_process) {
            $server_process.Refresh()
            if (-not $server_process.HasExited) {
                if (test_owned_listener $server_process.Id) {
                    Write-Host 'Stopping only the MySQL server started by this command.'
                    $env:MYSQL_PWD = $administrator_credential.GetNetworkCredential().Password
                    $shutdown_process = Start-Process -FilePath $administrator_client -ArgumentList @(
                        '--no-defaults', '--no-login-paths', '--protocol=TCP', '--host=127.0.0.1',
                        '--port=3307', '--user=root', '--connect-timeout=5',
                        '--shutdown-timeout=15', 'shutdown'
                    ) -WorkingDirectory $private_root -WindowStyle Hidden -PassThru
                    if (-not $shutdown_process.WaitForExit(20000)) {
                        $shutdown_message = "The shutdown helper is still running (PID $($shutdown_process.Id))."
                    }
                    elseif ($shutdown_process.ExitCode -ne 0) {
                        $shutdown_message = 'The MySQL shutdown command reported a failure.'
                    }
                }
                else {
                    $shutdown_message = 'The owned server has no verified listener; shutdown was not sent.'
                }
                if (-not $server_process.WaitForExit(20000)) {
                    $shutdown_message = "The owned MySQL server is still running (PID $($server_process.Id)). No process was force-stopped."
                }
            }
            if (@(get_mysql_listeners).Count -gt 0) {
                $shutdown_message = 'Port 3307 is still occupied. No other process was contacted or stopped.'
            }
        }
    }
    catch {
        $shutdown_message = "Shutdown could not be verified. Check the owned server PID $($server_process.Id); no process was force-stopped."
    }
    finally {
        foreach ($environment_name in $environment_names) {
            [Environment]::SetEnvironmentVariable(
                $environment_name, $previous_environment[$environment_name], 'Process'
            )
        }
        $runner_credential = $null
        $administrator_credential = $null
        $ErrorActionPreference = $previous_error_preference
    }
}

if ($failure_message -or $shutdown_message -or -not $checks_passed) {
    if ($failure_message) { Write-Host $failure_message -ForegroundColor Red }
    if ($shutdown_message) { Write-Host $shutdown_message -ForegroundColor Red }
    throw 'Local MySQL validation is incomplete. Deployment settings and viewer gates were not changed.'
}
Write-Host 'PASS: All five native MySQL checks passed and the local server stopped.' -ForegroundColor Green
Write-Host 'Deployment settings, saved configuration, and viewer gates were not changed.'
