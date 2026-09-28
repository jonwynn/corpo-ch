# Prepare the local Linux staging environment

Version **1.7.0-beta.1**, unreleased. Complete the [read-only service check](match-viewer-staging.md#4-verify-development-identities-and-access) first. That check verifies credentials and resource metadata; it does not configure the application or start a runtime.

The local checkpoint runs the website and a fresh MySQL staging database in **Ubuntu 24.04 on WSL 2**. Browser login with the development Discord application is verified. Ordinary `serve` keeps match-viewer gates off, and signing in grants no staff or referee access. A separately prepared [sample-match pilot](match-viewer-local-pilot.md) supports the verified account with automatic refresh. The Discord bot, workers, scheduler, dedicated game server and spreadsheet exports are deferred. The Windows fixture preview and disposable MySQL checker remain separate tools.

The bot connects directly to Redis in `CorpoDbot.__init__`, and Celery handles background work. [Celery does not support native Windows](https://docs.celeryq.dev/en/stable/faq.html#windows). Running only Redis in Linux would leave the worker on an unsupported platform. WSL supplies a Linux runtime without adding Docker or replacing the application's existing components.

Steps 1–3 prepare Ubuntu. Steps 4–6 operate an instance that a maintainer has already prepared and checked; they do not provision a fresh clone. The final section describes that preparation. WSL installation may need administrator approval, a Windows restart and interactive Linux account creation. A successful local startup does not establish that browser consent and callback handling work; complete the login check separately.

## 1. Install Ubuntu on WSL

Use the same Windows account that owns the repository and prepared private credentials. Save open work before starting. Open the Start menu, type **Windows PowerShell**, right-click it and choose **Run as administrator**. Approve the Windows prompt.

Paste this block into **administrator Windows PowerShell**. Estimated duration: **5–15 minutes**, plus any restart; downloads, Windows updates or restricted connectivity can take longer. No GPU is used. The repository path is an example; change it only if the checkout is elsewhere.

```powershell
Set-Location -LiteralPath 'C:\git\corpo-ch'
wsl.exe --install -d Ubuntu-24.04
```

Wait for installation to finish. If Windows requests a restart, restart through **Start > Power > Restart**, then continue below. If the command reports a download, administrator, virtualization or installation error, record its text and stop before trying unrelated system changes. Do not unregister an existing Linux distribution.

The pinned distribution name and command follow [Ubuntu's WSL installation guide](https://documentation.ubuntu.com/wsl/latest/howto/install-ubuntu-wsl2/). See also [Microsoft's WSL installation requirements](https://learn.microsoft.com/en-us/windows/wsl/install).

## 2. Create the Linux account

After any required restart, open **Windows PowerShell normally**, without administrator mode. Paste this block. Estimated duration: **1–5 minutes** for first launch and account setup; later launches usually take seconds. No GPU is used.

```powershell
Set-Location -LiteralPath 'C:\git\corpo-ch'
wsl.exe --distribution Ubuntu-24.04
```

Ubuntu may ask for a new Linux username and password. Choose a simple lowercase username and enter the password twice. **No characters appear while typing the password; this is normal.** This Linux account is separate from the Windows account and the Discord credentials. Keep its password private. If account creation completed during installation, Ubuntu opens its terminal directly.

At the Linux prompt, type `exit` and press **Enter** to return to PowerShell. This takes less than a second and uses no GPU. [Microsoft's Linux account setup instructions](https://learn.microsoft.com/en-us/windows/wsl/setup/environment#set-up-your-linux-username-and-password)

If Ubuntu is reported as uninstalled after restarting, report the installation output before repeating setup. If it opens as root, continue to the verification block, which will stop with an account-setup message.

## 3. Verify the runtime prerequisite

Paste this block into **normal Windows PowerShell**. Estimated duration: **2–15 seconds**, including Linux startup; no GPU. It checks the repository branch and prints Linux version information. It does not read credentials or start Corpo CH, the Discord bot, workers or exports.

```powershell
& {
    $ErrorActionPreference = 'Stop'
    Set-Location -LiteralPath 'C:\git\corpo-ch'

    $viewer_branch = git branch --show-current
    if ($LASTEXITCODE -ne 0 -or $viewer_branch -ne 'jons-tree-branch') {
        throw 'This folder is not on jons-tree-branch. Stop here.'
    }

    wsl.exe --list --verbose
    if ($LASTEXITCODE -ne 0) {
        throw 'WSL is not ready. Report the output above.'
    }

    $linux_user_id = wsl.exe --distribution Ubuntu-24.04 --exec id -u
    if ($LASTEXITCODE -ne 0) {
        throw 'Ubuntu-24.04 could not start. Report the output above.'
    }
    $linux_user_id = ([string]$linux_user_id).Trim()
    if ($linux_user_id -notmatch '^[1-9][0-9]*$') {
        throw 'Ubuntu needs a normal Linux user account before continuing. Report this message.'
    }

    wsl.exe --distribution Ubuntu-24.04 --exec uname -r
    if ($LASTEXITCODE -ne 0) {
        throw 'The Linux kernel check failed. Report the output above.'
    }

    wsl.exe --distribution Ubuntu-24.04 --exec cat /etc/os-release
    if ($LASTEXITCODE -ne 0) {
        throw 'The Ubuntu version check failed. Report the output above.'
    }

    Write-Host 'Linux account and version checks completed. Confirm VERSION 2 in the WSL table.' -ForegroundColor Green
}
```

The WSL table must show **Ubuntu-24.04** with **VERSION 2**. Its state may be Running or Stopped when listed; either is normal. The release output should identify Ubuntu 24.04. If VERSION is 1 or any check fails, report the output and stop. Otherwise, retain the output for the next setup step. These checks verify the operating-system prerequisite, not application readiness.

## 4. Check the prepared local instance

Continue on `jons-tree-branch`. Linux uses its own Python environment at `~/CorpoCH/staging/venv`; it cannot use the Windows `.venv`. Private MySQL data is under `~/CorpoCH/staging/mysql`, and private website configuration is under `~/CorpoCH/staging/web`. The commands below discover the Linux home directory without assuming a username. They never print configuration contents.

Use **two terminal windows** for the test. In **Window 1**, open normal Windows PowerShell and paste this block. Estimated duration: **2–15 seconds**; no GPU.

```powershell
Set-Location -LiteralPath 'C:\git\corpo-ch'
wsl.exe --distribution Ubuntu-24.04
```

Window 1 changes to an Ubuntu prompt, usually ending in `$`. **Leave that window open at the Linux prompt until after step 6. Do not type `exit` yet.** Keeping this foreground session open prevents Ubuntu from stopping between the separate control commands. Systemd services, including Supervisor, do not by themselves keep a WSL instance alive. [Microsoft's WSL systemd guidance](https://learn.microsoft.com/en-us/windows/wsl/systemd#how-does-enabling-systemd-affect-wsl-architecture)

Open a **second normal Windows PowerShell window**, called **Window 2** below. Its prompt starts with `PS`. Run the remaining PowerShell status, start and stop blocks in Window 2. Do not paste those blocks into the Ubuntu prompt in Window 1.

| Component | Supervisor name | Address or state |
|---|---|---|
| Private MySQL | `corpo-dev:mysql-dev` | `127.0.0.1:3308`, separate from the Windows checker's port 3307 |
| Local website | `corpo-dev:web-dev` | `http://127.0.0.1:8766/home` |
| Celery worker and scheduler | Not registered | Deferred; no background publication |
| Discord bot | Not registered | Deferred; no Gateway connection or match messages |
| Dedicated game server | Not registered | Deferred |

The local launcher uses Django's threaded development WSGI server. Concurrent connections prevent an idle browser connection from blocking the page; inactive socket reads close after ten seconds. Each connection retains Django's database cleanup. Supervisor controls the two named child processes. Both run as the normal Linux account, although its control command uses WSL's root account to access Supervisor. Neither program starts automatically just because its configuration exists.

Paste into **Window 2, normal Windows PowerShell**. Estimated duration: **2–15 seconds**; no GPU. Keep Window 1 open. The example repository root can be changed if needed.

```powershell
& {
    $ErrorActionPreference = 'Stop'
    Set-Location -LiteralPath 'C:\git\corpo-ch'
    $viewer_branch = git branch --show-current
    if ($LASTEXITCODE -ne 0 -or $viewer_branch -ne 'jons-tree-branch') {
        throw 'This folder is not on jons-tree-branch. Stop here.'
    }
    $linux_home = wsl.exe --distribution Ubuntu-24.04 --exec printenv HOME
    if ($LASTEXITCODE -ne 0) { throw 'Ubuntu could not report its home directory.' }
    $linux_home = ([string]$linux_home).Trim()
    if (-not $linux_home.StartsWith('/home/')) { throw 'Use the prepared normal Linux account.' }
    wsl.exe --distribution Ubuntu-24.04 --exec test -f "$linux_home/CorpoCH/staging/web/config.json"
    if ($LASTEXITCODE -ne 0) { throw 'Private web setup is not complete. Report this message.' }
    wsl.exe --distribution Ubuntu-24.04 --user root --exec /usr/bin/supervisorctl -c /etc/supervisor/supervisord.conf status 'corpo-dev:*'
    if ($LASTEXITCODE -notin @(0, 3)) { throw 'Supervisor could not report the prepared instance. Report its output.' }
}
```

`RUNNING` means that process is active; it does not prove the website is responding. `STOPPED` is normal before starting it. `FATAL`, `BACKOFF`, a missing program, or a missing private file needs diagnosis; do not run ordinary production commands to work around it. A browser that keeps loading needs an HTTP check even when both processes report `RUNNING`.

## 5. Start the website and test login

First, in the **development application's** Discord Developer Portal, open **OAuth2 > Redirects**, add exactly `http://127.0.0.1:8766/auth`, and save. Keep existing redirects that are still needed. This must be the callback for the application configured in the private web setup. `localhost`, a different port, or a trailing slash is a different callback address. Do not alter production application settings.

Paste this block into **Window 2, normal Windows PowerShell**. Estimated duration: **10–30 seconds**; no GPU. Keep Window 1 open throughout the browser test. The block starts only a stopped private MySQL process and then a stopped local website. An already running process is left running; failed or unexpected states stop the command.

```powershell
& {
    $ErrorActionPreference = 'Stop'
    Set-Location -LiteralPath 'C:\git\corpo-ch'
    $viewer_branch = git branch --show-current
    if ($LASTEXITCODE -ne 0 -or $viewer_branch -ne 'jons-tree-branch') {
        throw 'This folder is not on jons-tree-branch. Stop here.'
    }
    $linux_home = wsl.exe --distribution Ubuntu-24.04 --exec printenv HOME
    if ($LASTEXITCODE -ne 0) { throw 'Ubuntu could not report its home directory.' }
    $linux_home = ([string]$linux_home).Trim()
    if (-not $linux_home.StartsWith('/home/')) { throw 'Use the prepared normal Linux account.' }
    wsl.exe --distribution Ubuntu-24.04 --exec test -f "$linux_home/CorpoCH/staging/web/config.json"
    if ($LASTEXITCODE -ne 0) { throw 'Private web setup is not complete. Report this message.' }
    foreach ($program in @('corpo-dev:mysql-dev', 'corpo-dev:web-dev')) {
        $service_status = wsl.exe --distribution Ubuntu-24.04 --user root --exec /usr/bin/supervisorctl -c /etc/supervisor/supervisord.conf status $program
        $status_code = $LASTEXITCODE
        $service_status = ($service_status -join "`n").Trim()
        Write-Host $service_status
        if ($status_code -eq 0 -and $service_status -match '\sRUNNING\s') { continue }
        if ($status_code -ne 3 -or $service_status -notmatch '\sSTOPPED\s') {
            throw 'A staging process is missing or failed. Report its status before retrying.'
        }
        wsl.exe --distribution Ubuntu-24.04 --user root --exec /usr/bin/supervisorctl -c /etc/supervisor/supervisord.conf start $program
        if ($LASTEXITCODE -ne 0) { throw 'A staging process did not start. Report the output above.' }
    }
    Write-Host 'Open http://127.0.0.1:8766/home in your Windows browser.' -ForegroundColor Green
}
```

Open [the local website](http://127.0.0.1:8766/home), select its Discord login action, and verify the application name before approving consent. Allow approximately **1–3 minutes**; no GPU. A successful callback should return to the local site and show the signed-in account. Signing in alone does not assign staff, superuser or referee privileges. If the prepared instance shows **Open sample match viewer**, continue with the [sample-match guide](match-viewer-local-pilot.md). Otherwise it remains in login-only mode.

Report whether the home page loads, whether the displayed Discord application is correct, and whether login returns successfully. For a failure, report the visible error text. Do not share the complete callback URL, browser cookies, private JSON, token values or raw logs; callback addresses can contain short-lived codes and state values. Registering a redirect alone does not prove login works.

## 6. Stop the two staging processes

Paste into **Window 2, normal Windows PowerShell**, while Window 1 remains open. Estimated duration: **5–60 seconds**; no GPU. The website stops first, then its private MySQL process. The WSL installation, private database files and unrelated services remain available.

```powershell
& {
    $ErrorActionPreference = 'Stop'
    Set-Location -LiteralPath 'C:\git\corpo-ch'
    foreach ($program in @('corpo-dev:web-dev', 'corpo-dev:mysql-dev')) {
        $service_status = wsl.exe --distribution Ubuntu-24.04 --user root --exec /usr/bin/supervisorctl -c /etc/supervisor/supervisord.conf status $program
        $status_code = $LASTEXITCODE
        $service_status = ($service_status -join "`n").Trim()
        Write-Host $service_status
        if ($status_code -eq 3 -and $service_status -match '\sSTOPPED\s') { continue }
        if ($status_code -ne 0 -or $service_status -notmatch '\sRUNNING\s') {
            throw 'A staging process has an unexpected state. Report its status.'
        }
        wsl.exe --distribution Ubuntu-24.04 --user root --exec /usr/bin/supervisorctl -c /etc/supervisor/supervisord.conf stop $program
        if ($LASTEXITCODE -ne 0) { throw 'A staging process did not stop cleanly. Report its output.' }
    }
}
```

After both named processes report that they stopped, return to **Window 1's Ubuntu prompt**. Type `exit` and press **Enter**. This returns Window 1 to PowerShell and takes less than a second; no GPU. You can then close either window. If either stop command failed, keep Window 1 open and report the error before closing it.

To open this guide in **Notepad++**, paste into normal PowerShell, such as Window 2. Estimated duration: **1–5 seconds**; no GPU.

```powershell
& {
    $ErrorActionPreference = 'Stop'
    Set-Location -LiteralPath 'C:\git\corpo-ch'
    $editor = 'C:\Program Files\Notepad++\notepad++.exe'
    if (-not (Test-Path -LiteralPath $editor -PathType Leaf)) {
        throw 'Notepad++ was not found in its standard installation folder.'
    }
    & $editor '.\docs\match-viewer-staging-runtime.md'
}
```

## Maintainer preparation for a new instance

The local checkpoint has passed isolated suites, fresh-process startup smoke, fourteen native MySQL checks, database ownership/grant validation, fresh migrations and local HTTP/browser rendering. The native database is MySQL 8.0.46/InnoDB with mysqlclient 2.3.0; the Linux application uses Python 3.12.3 and Django 6.0.8. Real Discord consent and callback acceptance were confirmed in the browser, followed by a read-only DEV referee-membership check. The explicitly enabled synthetic pilot also passed database-backed rendering, live picks/results and undo checks. Manual DEV-channel testing includes finalization and reopening/undo. This does not qualify ordinary bot startup, exports or another deployment; see the [remaining pilot checks](match-viewer-discord-pilot.md#verified-local-checkpoint).

The prepared-PC commands above assume this work is complete. They cannot prepare a fresh checkout or repair a failed provision. Keep generated environments, credentials, logs and databases outside the repository. During preparation that spans separate commands, keep a foreground Ubuntu session open as described in step 4; an installed Supervisor service alone does not keep the distribution running.

1. Install the Linux dependency packages, create a normal-user Python environment at `~/CorpoCH/staging/venv`, install the repository requirements, and run the guarded Python suites plus Node refresh tests. Record actual versions. Keep the existing Windows `.venv` unchanged.
2. Install the MySQL server binary and Supervisor without starting a default MySQL instance. Create the owned private parent `~/CorpoCH/staging` with mode `0700`. Run the one-time provisioner below against a **new** `mysql` directory. It refuses existing destinations and an occupied port 3308. A failed attempt retains partial data and `credentials.pending.json`; inspect it privately rather than deleting or rerunning over it.
3. Create `~/CorpoCH/staging/web/config.json` with mode `0600` in an owned `0700` directory. It must contain exactly `schema_version` (integer 1), `database_name`, `database_user`, `database_password`, `database_port` (3308), `browser_port` (8766), `bot_id`, `bot_secret`, `secret_key` and `salt_key`. Map database values from the provisioner's `database`, `app_user` and `app_password`; use the dedicated application's OAuth client secret. Generate distinct fresh session and encryption keys. No bot token or Google key is needed by this web-only process. Do not paste secrets into command lines.
4. Adapt [the Supervisor template](examples/supervisor-corpo-staging.conf) for the normal Linux username, independently confirmed DEV application ID and checkout path. Install it as `/etc/supervisor/conf.d/corpo-dev.conf`, create private log directories, and load only that group. Do not register worker, scheduler, bot or game-server programs yet. Both supplied programs have automatic startup disabled.
5. Start only `corpo-dev:mysql-dev`. Run the native MySQL checker with the separate generated validation account; it creates and removes its own schema. Run the staged launcher's `check`, then `migrate`, then `check` using the private config and independently confirmed application ID. Each launcher command verifies MySQL port, current account, exact schema grants and the ownership marker before proceeding. It cannot import the production settings or service providers. Apply migrations only to this newly provisioned schema.
6. Start only `corpo-dev:web-dev`; verify local HTTP behavior and that all viewer gates remain off. Then complete the human browser-login check in step 5. Automated tests and status output do not replace that check.

For the one-time MySQL provisioner, use **normal Windows PowerShell** only after dependency and parent-directory preparation. Estimated duration: **15–120 seconds**; no GPU. This creates a fresh local database; it is not an everyday start command.

```powershell
& {
    $ErrorActionPreference = 'Stop'
    Set-Location -LiteralPath 'C:\git\corpo-ch'
    $viewer_branch = git branch --show-current
    if ($LASTEXITCODE -ne 0 -or $viewer_branch -ne 'jons-tree-branch') {
        throw 'This folder is not on jons-tree-branch. Stop here.'
    }
    $linux_home = wsl.exe --distribution Ubuntu-24.04 --exec printenv HOME
    if ($LASTEXITCODE -ne 0) { throw 'Ubuntu could not report its home directory.' }
    $linux_home = ([string]$linux_home).Trim()
    if (-not $linux_home.StartsWith('/home/')) { throw 'Use the prepared normal Linux account.' }
    $expected_bot_id = Read-Host 'Independently confirmed DEV application ID'
    wsl.exe --distribution Ubuntu-24.04 --cd /mnt/c/git/corpo-ch --exec "$linux_home/CorpoCH/staging/venv/bin/python" -B -m staging.provision_mysql --root "$linux_home/CorpoCH/staging/mysql" --expected-bot-id $expected_bot_id
    if ($LASTEXITCODE -ne 0) { throw 'Provisioning did not complete. Retain partial files and report the sanitized result.' }
}
```

The provisioner leaves MySQL stopped. It writes private `mysql.conf` and `credentials.json`, with separate random root, application and checker passwords. The application account can access only its generated `corpo_staging_…` database. The checker account can create disposable schemas only under `corpo_viewer_validation_…`; it has no global database privileges. The schema contains a single ownership marker for this runtime and DEV application. No production data is imported.

The web launcher's allowed commands are `check`, `migrate`, `serve`, `serve-viewer` and `serve-viewer-live`, selected after `--config` and `--expected-bot-id`. The last two require the prepared sample fixture. For example, the maintainer runs the prepared Linux interpreter with `-B -m staging --config <private-web-config> --expected-bot-id <DEV-application-ID> check`. The [separate Discord pilot launcher](match-viewer-discord-pilot.md) reuses the verified sample and its action callbacks. Never substitute the ordinary `manage.py`, bot or Celery launch commands for these restricted checkpoints.

Later stages must enforce development guild/channel/spreadsheet destinations at outbound operations, configure and refresh the human-referee roles, and qualify separate broker queues before adding bot or worker processes. The owner deployment's worker, beat, Discord-bot and dedicated-game-server entries are separate responsibilities, not dependencies to start during this login check. Keep scheduled publication off until an explicit test-sheet export has been inspected. Follow the [rollout checklist](match-viewer-rollout.md) before enabling the viewer for staff.
