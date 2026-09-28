# Prepare the local Linux staging environment

Version **1.7.0-beta.1**, unreleased. Complete the [read-only service check](match-viewer-staging.md#4-verify-development-identities-and-access) first. That check verifies credentials and resource metadata; it does not configure the application or start a runtime.

The local pilot will run Django, the Discord bot, Celery, Redis and a fresh MySQL staging database together in **Ubuntu 24.04 on WSL 2**. The website remains accessible from a Windows browser. The existing Windows fixture preview and disposable MySQL checker remain separate tools.

The bot connects directly to Redis in `CorpoDbot.__init__`, and Celery handles background work. [Celery does not support native Windows](https://docs.celeryq.dev/en/stable/faq.html#windows). Running only Redis in Linux would leave the worker on an unsupported platform. WSL supplies a Linux runtime without adding Docker or replacing the application's existing components.

This checkpoint installs and verifies WSL and Ubuntu only. The application runtime, Linux dependencies, private configuration, staging database and broker are not yet provisioned. WSL installation may need administrator approval, a Windows restart and interactive Linux account creation. These steps must be completed locally.

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

## Work after this checkpoint

Continue development and commits on `jons-tree-branch`. A separate branch or production checkout is not required. Linux will need its own Python environment; the Windows `.venv` cannot be reused. The next implementation must:

- Prepare Linux dependencies and rerun isolated tests against the actual installed versions. Keep generated environments and private runtime data outside the repository.
- Create explicit staging settings, private media storage, fresh encryption/session keys, a fresh MySQL database and separate Redis queues. Do not reuse the disposable Windows checker's database or copy production data. Existing direct imports of `corpoch.settings` must also respect the staging configuration; an alternate `DJANGO_SETTINGS_MODULE` alone does not cover every consumer.
- Enforce the approved development application, guild, channel and spreadsheet before bot startup or outbound operations. Spreadsheet destination checks must occur before opening or creating worksheets. The resource inventory and `HOME_GUILD_ID` alone do not enforce these boundaries.
- Configure the two human-referee roles, refresh stored memberships, and register the exact local OAuth callback. Verify login and effective channel permissions before the match pilot. Keep scheduled publication off until one controlled export has been inspected.

Do not run the README's ordinary service-start commands at this checkpoint. A successful WSL installation does not yet provide the isolated application configuration required by the [rollout checklist](match-viewer-rollout.md).
