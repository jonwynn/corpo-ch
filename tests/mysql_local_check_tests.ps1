# Exercises local MySQL preflight failures without reading the private instance or starting a server.
# Invoke from the repository root with Windows PowerShell 5.1.

$test_error_preference = $ErrorActionPreference
$ErrorActionPreference = 'Stop'
$test_results = @{ count = 0 }
$test_source_path = Join-Path (Get-Location).ProviderPath 'tests\mysql_local_check.ps1'
$test_source = Get-Content -LiteralPath $test_source_path -Raw
$parse_tokens = $null
$parse_errors = $null
$test_syntax = [Management.Automation.Language.Parser]::ParseInput(
    $test_source, [ref]$parse_tokens, [ref]$parse_errors
)
if ($parse_errors.Count -gt 0) {
    throw 'The local MySQL command has PowerShell syntax errors.'
}

$helper_names = @(
    'stop_local_check', 'read_private_item', 'validate_private_root',
    'validate_private_path', 'read_private_credential'
)
$helper_functions = @($test_syntax.FindAll({
    param($syntax_node)
    $syntax_node -is [Management.Automation.Language.FunctionDefinitionAst] -and
        $helper_names -contains $syntax_node.Name
}, $true))
if ($helper_functions.Count -ne $helper_names.Count) {
    throw 'The expected production preflight helpers were not found.'
}
foreach ($helper_function in $helper_functions) {
    . ([ScriptBlock]::Create($helper_function.Extent.Text))
}

function assert_test_condition {
    param([bool]$condition, [string]$message)

    if (-not $condition) { throw $message }
}

function expect_preflight_failure {
    param([ScriptBlock]$action, [string]$expected_text, [string]$expected_path = '')

    $caught_failure = $null
    try { & $action | Out-Null }
    catch { $caught_failure = $_ }
    assert_test_condition ($null -ne $caught_failure) 'The expected preflight failure did not occur.'
    assert_test_condition (
        [bool]$caught_failure.Exception.Data['corpo_local_check_message']
    ) 'The failure was not a classified preflight diagnostic.'
    assert_test_condition (
        $caught_failure.Exception.Message.Contains($expected_text)
    ) "The failure did not identify the expected condition: $expected_text"
    assert_test_condition (
        $caught_failure.Exception.Message.Contains($expected_path)
    ) 'The diagnostic omitted the failing path.'
    assert_test_condition (
        -not $caught_failure.Exception.Message.Contains('fixture-secret-do-not-display')
    ) 'The diagnostic exposed the original exception message.'
    $test_results.count++
}

$temporary_parent = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')
$fixture_name = 'corpo_mysql_preflight_' + [Guid]::NewGuid().ToString('N')
$fixture_root = [IO.Path]::GetFullPath((Join-Path $temporary_parent $fixture_name))
$fixture_created = $false
try {
    assert_test_condition (-not (Test-Path -LiteralPath $fixture_root)) 'The fixture directory already exists.'
    [IO.Directory]::CreateDirectory($fixture_root) | Out-Null
    $fixture_created = $true
    $private_fixture = Join-Path $fixture_root 'mysql-test'

    expect_preflight_failure { validate_private_root $private_fixture } 'The private MySQL folder was not found:' $private_fixture
    [IO.Directory]::CreateDirectory($private_fixture) | Out-Null
    validate_private_root $private_fixture
    $test_results.count++

    foreach ($fixture_file_name in @('instance.json', 'server.ini', 'runner.clixml', 'admin.clixml')) {
        $fixture_file = Join-Path $private_fixture $fixture_file_name
        expect_preflight_failure {
            validate_private_path $private_fixture $fixture_file $false $fixture_file_name
        } "$fixture_file_name was not found:" $fixture_file
    }

    $valid_file = Join-Path $private_fixture 'valid.json'
    [IO.File]::WriteAllText($valid_file, '{}')
    assert_test_condition (
        (validate_private_path $private_fixture $valid_file $false 'Fixture file') -eq $valid_file
    ) 'The existing file did not validate.'
    $test_results.count++
    expect_preflight_failure { validate_private_root $valid_file } 'is a file instead of a directory:'
    expect_preflight_failure {
        validate_private_path $private_fixture $valid_file $true 'Fixture data'
    } 'Fixture data must be a directory:'
    $valid_directory = Join-Path $private_fixture 'data'
    [IO.Directory]::CreateDirectory($valid_directory) | Out-Null
    assert_test_condition (
        (validate_private_path $private_fixture $valid_directory $true 'Fixture data') -eq $valid_directory
    ) 'The existing directory did not validate.'
    $test_results.count++
    expect_preflight_failure {
        validate_private_path $private_fixture $valid_directory $false 'Fixture config'
    } 'Fixture config must be a file:'
    expect_preflight_failure {
        validate_private_path $private_fixture (Join-Path $private_fixture '..\outside.json')
    } 'falls outside the prepared MySQL folder'
    expect_preflight_failure {
        validate_private_path $private_fixture 'relative.json'
    } 'is not absolute'

    & {
        function Get-Item { throw [UnauthorizedAccessException]::new('fixture-secret-do-not-display') }
        expect_preflight_failure {
            read_private_item $valid_file 'Fixture credentials'
        } 'Windows denied access to Fixture credentials'
    }
    & {
        function Get-Item { throw [Security.SecurityException]::new('fixture-secret-do-not-display') }
        expect_preflight_failure {
            validate_private_root $private_fixture
        } 'Windows denied access to The private MySQL folder'
    }
    & {
        function Get-Item { throw [Management.Automation.DriveNotFoundException]::new('fixture-secret-do-not-display') }
        expect_preflight_failure {
            read_private_item $valid_file 'Fixture drive item'
        } 'Fixture drive item was not found:'
    }
    & {
        function Get-Item { throw [IO.IOException]::new('fixture-secret-do-not-display') }
        expect_preflight_failure {
            read_private_item $valid_file 'Fixture read'
        } '(IOException)' $valid_file
    }
    & {
        function Get-Item {
            return [pscustomobject]@{ PSIsContainer = $true; Attributes = [IO.FileAttributes]::ReparsePoint }
        }
        expect_preflight_failure {
            validate_private_root $private_fixture
        } 'must not contain symbolic links or junctions'
        expect_preflight_failure {
            validate_private_path $private_fixture $valid_directory $true
        } 'must not contain symbolic links or junctions'
    }
    & {
        function Import-Clixml { throw [Security.Cryptography.CryptographicException]::new('fixture-secret-do-not-display') }
        expect_preflight_failure {
            read_private_credential (Join-Path $private_fixture 'runner.clixml')
        } "The saved credential file 'runner.clixml' could not be opened. It may belong to another Windows account or be damaged."
    }

    # Exercise the complete failure/finally path under an empty, temporary profile.
    $fixture_repository = Join-Path $fixture_root 'repository'
    $fixture_python_directory = Join-Path $fixture_repository '.venv\Scripts'
    [IO.Directory]::CreateDirectory($fixture_python_directory) | Out-Null
    [IO.File]::WriteAllText((Join-Path $fixture_python_directory 'python.exe'), '')
    $fixture_environment_names = @(
        'LOCALAPPDATA', 'MYSQL_TEST_HOST', 'MYSQL_TEST_PORT', 'MYSQL_TEST_USER',
        'MYSQL_TEST_PASSWORD', 'MYSQL_PWD'
    )
    $original_environment = @{}
    foreach ($fixture_environment_name in $fixture_environment_names) {
        $original_environment[$fixture_environment_name] = [Environment]::GetEnvironmentVariable(
            $fixture_environment_name, 'Process'
        )
    }
    Push-Location -LiteralPath $fixture_repository
    try {
        & {
            function git {
                Set-Variable -Name LASTEXITCODE -Value 0 -Scope 1
                if ($args[0] -eq 'branch') { return 'jons-tree-branch' }
                return (Get-Location).ProviderPath
            }
            function Get-NetTCPConnection { throw 'The preflight test must not inspect listeners.' }
            function Start-Process { throw 'The preflight test must not start processes.' }
            foreach ($fixture_environment_name in $fixture_environment_names) {
                [Environment]::SetEnvironmentVariable(
                    $fixture_environment_name, 'fixture-secret-do-not-display', 'Process'
                )
            }
            $env:LOCALAPPDATA = Join-Path $fixture_root 'empty-profile'
            $script_failure = $null
            $captured_output = @()
            try {
                & ([ScriptBlock]::Create($test_source)) 6>&1 | ForEach-Object { $captured_output += [string]$_ }
            }
            catch { $script_failure = $_ }
            assert_test_condition (
                $null -ne $script_failure -and
                $script_failure.Exception.Message.StartsWith('Local MySQL validation is incomplete.')
            ) 'The full command did not report its controlled preflight failure.'
            $output_text = $captured_output -join "`n"
            assert_test_condition ($output_text.Contains('Windows account:')) 'The command omitted account context.'
            assert_test_condition ($output_text.Contains('Private MySQL folder:')) 'The command omitted folder context.'
            assert_test_condition ($output_text.Contains('The private MySQL folder was not found:')) 'The command omitted the missing-folder diagnostic.'
            assert_test_condition (-not $output_text.Contains('fixture-secret-do-not-display')) 'The command exposed an environment value.'
            foreach ($fixture_environment_name in $fixture_environment_names | Where-Object { $_ -ne 'LOCALAPPDATA' }) {
                assert_test_condition (
                    [Environment]::GetEnvironmentVariable($fixture_environment_name, 'Process') -eq 'fixture-secret-do-not-display'
                ) "The command changed the prior $fixture_environment_name value."
            }
            $test_results.count++
        }
    }
    finally {
        Pop-Location
        foreach ($fixture_environment_name in $fixture_environment_names) {
            [Environment]::SetEnvironmentVariable(
                $fixture_environment_name, $original_environment[$fixture_environment_name], 'Process'
            )
        }
    }
    Write-Host "PASS: $($test_results.count) local MySQL preflight checks passed without starting a server."
}
finally {
    if ($fixture_created) {
        $cleanup_path = [IO.Path]::GetFullPath($fixture_root)
        if ([IO.Path]::GetDirectoryName($cleanup_path) -ne $temporary_parent -or
            [IO.Path]::GetFileName($cleanup_path) -ne $fixture_name -or
            $fixture_name -notmatch '^corpo_mysql_preflight_[0-9a-f]{32}$') {
            throw 'The fixture cleanup path failed validation; no files were removed.'
        }
        Remove-Item -LiteralPath $cleanup_path -Recurse -Force
    }
    $ErrorActionPreference = $test_error_preference
}
