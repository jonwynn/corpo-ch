# Test the viewer from another network

Version **1.7.0-beta.1**, unreleased. For the current local hosting approach, use [Share the local DEV viewer](match-viewer-local-sharing.md). The steps below are an alternative for a future owner-managed DEV server; they do not establish that the branch is installed there or outside access is ready.

## 1. Confirm the server with its owner

Send this request to the DEV server operator:

> Please confirm the public HTTPS URL, SSH hostname/login account, repository folder, Python virtual-environment folder, and process-manager service names for the separate DEV deployment. The earlier example used `/home/dbot/corpo-ch-dev` and `/home/dbot/dev-venv`; please confirm whether those are current. Confirm that its database, bot credentials, broker/queues and storage are separate from production, that a backup/restore procedure is available, and how its environment settings and HTTPS proxy are managed. Please provide identifiers and paths only, not passwords, tokens, keys or configuration-file contents.
>
> We need to test `jons-tree-branch` from `https://github.com/jonwynn/corpo-ch.git` with one allowed staff tester and one account without access. Please confirm who will install it and restart the DEV website/bot. Keep screenshots, exports and production unchanged during the viewer test.

The owner can gather checkout/runtime facts with this **Bash** block in their existing SSH session. Estimated duration: **1–5 seconds**; no GPU. Enter absolute Linux paths to the actual DEV folders when prompted. It reads Git and interpreter information only; it does not import application settings, connect to a database or restart services.

```bash
(
    set -eu
    read -r -p 'Absolute DEV repository folder: ' viewer_checkout
    read -r -p 'Absolute DEV virtual-environment folder: ' viewer_venv
    [[ "$viewer_checkout" == /* && "$viewer_venv" == /* ]]
    test -f "$viewer_checkout/manage.py"
    test -x "$viewer_venv/bin/python"
    cd -- "$viewer_checkout"
    printf 'Repository: '
    pwd -P
    git branch --show-current
    git rev-parse HEAD
    git status --short
    "$viewer_venv/bin/python" --version
    "$viewer_venv/bin/python" -m pip show Django daphne mysqlclient
)
```

Review filenames and paths before sharing that output. Never send `.env`, private JSON, cookie values or full OAuth callback URLs.

Exact install, backup, migration and service commands remain pending these facts. Do not run the local provisioner on the owner's existing database or use the local Supervisor commands there. The confirmed deployment procedure must cover:

- Preserve existing DEV work, use the fork's intended branch/revision, and retain the previous revision for rollback.
- Stop or coordinate DEV match writers; back up the separate database; apply `corpoch.0030` and `dbot.0006`; collect static assets.
- Verify native MySQL behavior under the chosen connection settings before enabling its viewer gate.
- Configure the confirmed HTTPS hostname, matching Discord `/auth` callback, host checks, proxy handling and secure cookies. Keep the application listener private behind the HTTPS server.
- Use a supported ASGI/WSGI process for the public deployment. The earlier `manage.py runserver` example is a development command. See [Django's deployment checklist](https://docs.djangoproject.com/en/6.0/howto/deployment/checklist/) and [Daphne instructions](https://docs.djangoproject.com/en/6.0/howto/deployment/asgi/daphne/).
- Check the effective `DEBUG` value. The inherited `corpoch/settings.py` currently reads it with `os.getenv`; a nonempty string such as `False` is truthy. The accepted local launcher explicitly uses the boolean `False` and is unaffected. Resolve the deployment setting and run `check --deploy` in the confirmed server environment before inviting outside testers.
- Configure and synchronize the DEV guild's human referee roles, create an explicit CORP test match, and verify an allowed and a denied account before enabling polling.

The owner must supply the resulting **HTTPS match URL**, such as `https://<confirmed-dev-host>/match-viewer/<actual-match-id>/`. The local `127.0.0.1` URL and sample ID do not identify a match on that server.

## 2. Outside tester: verify the site is reachable

Wait until the operator confirms the DEV deployment and supplies its exact match URL. The tester should use another internet connection, such as their own home connection or mobile data. They need a browser and their own Discord account; they do not need the repository, Python, MySQL, SSH access or bot credentials.

Paste this complete block into **Windows PowerShell**. Estimated duration: **2–30 seconds**; no GPU. Paste the full match URL when prompted. This performs an anonymous GET with normal certificate verification, then opens the login page. It does not authenticate the command-line request or expose a session cookie.

```powershell
& {
    $ErrorActionPreference = 'Stop'
    $match_url = (Read-Host 'Paste the exact DEV match URL supplied by the operator').Trim()
    $match_uri = $null
    if (-not [Uri]::TryCreate($match_url, [UriKind]::Absolute, [ref]$match_uri)) {
        throw 'The address is not a complete URL.'
    }
    if ($match_uri.Scheme -ne 'https' -or $match_uri.IsLoopback -or $match_uri.UserInfo -or $match_uri.Query -or $match_uri.Fragment) {
        throw 'Use the supplied HTTPS match URL, without login codes or extra parameters.'
    }
    if ($match_uri.AbsolutePath -notmatch '^/match-viewer/[^/]+/$') {
        throw 'Use a selected match URL ending in /match-viewer/<match-id>/.'
    }
    $curl_command = Get-Command curl.exe -CommandType Application -ErrorAction Stop
    $http_status = & $curl_command.Source --silent --show-error --connect-timeout 10 --max-time 30 --output NUL --write-out '%{http_code}' --url $match_uri.AbsoluteUri
    if ($LASTEXITCODE -ne 0) {
        throw 'The HTTPS request failed. Report the curl error; do not bypass certificate checks.'
    }
    $http_status = ([string]$http_status).Trim()
    Write-Host ('Anonymous selected-match HTTP status: ' + $http_status)
    if ($http_status -ne '401') {
        throw 'Expected 401 for an unsigned-in viewer request. Report this result to the operator before continuing.'
    }
    Write-Host 'PASS: HTTPS responds and the unsigned-in request is denied.' -ForegroundColor Green
    Write-Host 'Next: sign in with your own Discord account, then reopen the supplied match URL.'
    Start-Process ($match_uri.GetLeftPart([UriPartial]::Authority) + '/auth/start')
}
```

A `401` is expected for this check: it demonstrates an HTTP response requiring sign-in. It does not prove authorized viewing, correct match existence, live updates or complete privacy. A timeout, certificate error, redirect, `404` or `503` is a different result; the operator should diagnose it before treating this check as passed. Do not use `curl -I`: the viewer accepts GET, not HEAD.

After completing Discord login, paste the supplied match URL into the same browser. A permitted staff account should see the match. Having a Discord role alone is insufficient until the application has synchronized that role's membership.

## 3. Test live updates together

Allow **5–10 minutes**; no GPU. The operator uses the normal DEV referee controls for the server-side test match. The remote tester watches that same match in their browser. The local `/viewer-pilot` command changes the local sample only; it cannot update the owner's separate server database.

| Operator action | Outside tester confirms |
|---|---|
| Select a chart | The chart and picker appear automatically; selecting alone adds no point. |
| Record a winner | The correct player's match score increases and the next picker updates. |
| Select another chart | The corresponding latest-pick panel and round number update. |
| Use **Back** to undo, then record a correction if needed | The remote score/history reflect the correction, including a lower score when applicable. |
| Reach the target and use **Submit Match** | The match shows the finalized result; screenshot/export completion is not fabricated. |
| Use **Reopen match** after finalization | The viewer returns to the recorded active state. |

The tester should leave Match details expanded during changes and confirm it stays open. Active matches request updates about every two seconds; completed matches about every ten seconds, plus request time. Compare both browsers after a successful refresh rather than expecting an instantaneous update.

## 4. Verify access restrictions

Allow **5–10 minutes**; no GPU. Use the same known match URL throughout.

1. Open it in a private/incognito browser window without signing in. The viewer should require sign-in and reveal no player names, score or chart titles.
2. Sign in with a separate account that is not a superuser, guild administrator or synchronized referee. The selected match should be denied with HTTP `403`. The match-list page can legitimately return an empty list with HTTP `200`, so an empty list alone is not this test.
3. Keep the allowed tester's viewer open. The operator temporarily removes all of that test account's qualifying referee roles, ensuring it has no administrator/superuser alternative access. Perform this only for the dedicated DEV test account.
4. In the normal DEV Django administration, select the DEV guild under Guilds and run **Update Discord Info**. Wait for the normal DEV bot task to finish. Website membership is stored; removing a Discord role alone is not continuous synchronization.
5. After synchronization, the tester's next successful authorization check should deny access and clear visible match data. A transport failure may retain stale data until a valid response arrives; that is not proof that access revocation completed.
6. Restore the test role, synchronize again, then reload the selected match. Authorized viewing should return. A page whose polling stopped on access denial needs a reload.

Do not grant a tester superuser access merely to make the positive test pass. The operator can verify page/fragment status and private cache headers in browser developer tools without exporting cookies or authenticated command lines.

## 5. Report results and stop or retain the DEV pilot

Send the operator the tested commit, approximate test time, browser name, which account category was used, and pass/fail for anonymous denial, authorized viewing, live corrections and synchronized role revocation/restoration. Share visible errors without credentials or full callback URLs.

If rollout must stop, the operator disables `MATCH_VIEWER_POLLING_ENABLED` and `MATCH_VIEWER_ENABLED` in the confirmed DEV environment and reloads its website using that host's process manager. Recheck that both the selected page and state endpoint stop serving match data. Do not reverse the provenance migration or restart older match writers against active CORP matches. The [full rollback procedure](match-viewer-rollout.md#rollback) remains authoritative.

No local reprovisioning, router port forwarding, public MySQL access, screenshot submission or spreadsheet export is required for this owner-hosted viewer test.
