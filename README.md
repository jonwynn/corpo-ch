# Corpo CH
The Corpo CH Django App/Discord Bot

Clone Hero Tournament organizer and tools.

## Fork development: live match viewer

Development version: **1.7.0-beta.1**, unreleased.

This fork is preparing a read-only live match viewer for tournament staff on the existing website. The current milestone provides the data contract, independent fixtures, isolated CORP Cup rule calculations and a presentation builder. The viewer page and production integration are not implemented yet.

- [Viewer contract and rule decisions](docs/match-viewer-contract.md)
- [Foundation tests and validation limits](docs/match-viewer-development.md)
- [Changelog](CHANGELOG.md)

The planned viewer uses recorded match results, bans and chart selections. Continuous in-song statistics have no verified data source. CORP Cup uses opening-only bans, higher-seed first pick and subsequent loser picks. Database integration is paused because the initial migration requires a legacy encryption dependency absent from the current requirements; see the development notes before running migration tests.

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
