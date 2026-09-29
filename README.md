# Corpo CH
The Corpo CH Django App/Discord Bot

Clone Hero Tournament organizer and tools.

## Fork development: live match viewer

Development version: **1.7.0-beta.1**, unreleased.

This fork adds a read-only live match viewer for tournament staff on the existing website. It shows recorded bans/saves, chart picks, round winners and match progress in the approved navy, blue and coral layout. Large player names, stable player colors and a dynamic win target keep the main display compact.

- [Viewer contract and rule decisions](docs/match-viewer-contract.md)
- [Beginner PowerShell guide: local checks and preview](docs/match-viewer-development.md)
- [Private development credentials and test-service preparation](docs/match-viewer-staging.md)
- [Local Linux website: setup, Supervisor controls and login check](docs/match-viewer-staging-runtime.md)
- [Accepted local DEV Discord pilot and manual checks](docs/match-viewer-discord-pilot.md#verified-local-checkpoint)
- [Share the local viewer and DEV controls with another tester](docs/match-viewer-local-sharing.md)
- [Staged rollout and rollback checklist](docs/match-viewer-rollout.md)
- [Outside-access testing: operator and remote tester steps](docs/match-viewer-outside-access.md)
- [Changelog](CHANGELOG.md)

The explicit CORP Cup profile uses four opening ban/save actions, higher-seed first pick and subsequent loser picks. The website and bot share validated recorded state; continuous in-song statistics have no verified data source. Native browser refresh retains the last valid result during temporary failures and rechecks staff access on every request.

The local single-owner DEV pilot is accepted: real Discord login, picks/results, finalization, reopening/undo, restart, private controls, narrow layouts, 200% zoom and Windows high contrast have passed manual review. Isolated tests, browser checks and fourteen native Linux MySQL checks provide additional coverage. It is **not deployed to production**. Viewer, polling and MySQL verification switches default off outside the explicit local pilot. Broader staff access, deployment-specific database/load checks and ordinary bot/provider integration remain separate work. Existing overlay and non-CORP rule paths remain available.

The local Linux environment runs the website and its separate MySQL database under Supervisor. Restricted DEV referee controls run in an explicitly started foreground session. Private configuration stays outside the repository, and production settings are not loaded. The guides include exact PowerShell start/stop commands. Ordinary bot startup, workers, screenshots and exports remain separate integration work.

Optional local sharing adds a separate loopback website behind a temporary HTTPS tunnel. Approved DEV referees can invoke `/viewer-pilot` without website setup, then open the viewer through Discord sign-in. All testers use the same synthetic match. The default owner-only mode stays available; real outside-browser acceptance is still pending.

## Links

Access [Corpo CH's main site](https://corpo-ch.org)
 - [Live Matches/Stream Overlay](https://corpo-ch.org/livematches)
 - [REST API](https://corpo-ch.org/api/swagger/)

[Corpo Discord Bot](https://discord.com/discovery/applications/1381816611086012456)
 - User install will allow you to use the non-tournament specific (matches/qualifiers) commands most anywhere in discord

## Installation for self-hosting

Git clone this repo down to a new folder.

Create Discord bot app -> needs discord.intents.members = True

Bot Installation Permissions
 - Attach Files
 - Create Private/Public Threads
 - Embed Links
 - Manage Roles
 - Send Messages + in Threads
 - View Channels

Install redis+MySQL + populate .env vars for needed fields.

Install requirements `pip3 install -r requirements.txt`

Migrate -> `python3 manage.py migrate`

Collect Static -> `python3 manage.py collectstatic`

Load CH Icons/AppEmotes -> `python3 manage.py ch_icon_import`

Login to Main Site to create a discord user.

(Re)Start the Discord Bot, and all Discord App Team members or the singular owner will be made a SuperUser on start

OR

Set your user as superuser. `python3 manage.py set_discord_superuser -d DISCORDID`

(Optional) Create Google Service Account API. Upload contents of json file into admin UI

Start Processes:
 - `celery -A corpoch beat -l INFO --scheduler django_celery_beat.schedulers:DatabaseScheduler`
 - `celery -A corpoch worker -l info`
 - `python3 manage.py runserver`
 - `python3 manage.py run_dbot`
 - `python3 manage.py run_ch_servers` (Optional)

Needs nginx/apache2/web server hosting the static directories - preferable turn off autoindexing/view on images/qualifiers

Setup periodic tasks for management in admin UI:
 - corpoch.tasks.update_oauth_tokens - 12 hours
 - corpoch.tasks.update_all_users - 6 hours
 - corpoch.tasks.update_all_guilds - 20 mins
 - corpoch.tasks.upload_qualifiers_gsheet - 5 mins
 - corpoch.tasks.upload_completed_match_gsheet - 5 mins

More to come!

## Credits

The original Corpo CH implementation is maintained by [Jetsurf and contributors](https://github.com/Jetsurf/corpo-ch). The live match viewer work is developed in this fork.

Created by [@jonw_CH on Twitch](https://www.twitch.tv/jonw_CH).

 - All Contributors
 - The CH Competitive Scene
 - [CHOpt](https://github.com/GenericMadScientist/CHOpt) [CH Steg Reader](https://github.com/GenericMadScientist/CH-Steg-Reader) - [@GenericMadScientist](https://github.com/GenericMadScientist)
 - [Hydra](https://github.com/DragonDelgar/hydra) - [@DragonDelgar](https://github.com/DragonDelgar)

If you enjoy using this tool, or find it useful, please consider subscribing to my Twitch channel. This will help motivate me to make more cool things for you guys! :)
