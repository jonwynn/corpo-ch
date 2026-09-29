"""Runs one guild-scoped Discord control session for the local sample."""

import argparse
import asyncio
from contextlib import contextmanager
import logging
from pathlib import Path
import sys

from staging.configuration import StagingConfigurationError, load_configuration
from staging.referee_check import validate_snowflake
from staging.runtime import configure_web_runtime, validate_database_boundary
from tests.staging_preflight import StagingInputError, parse_discord_credentials, read_private_text


def run_database_call(function, *arguments, **options):
    """
    Keeps database work and connection cleanup inside its synchronous thread

    :param callable function: Bounded fixture operation
    :param tuple arguments: Positional arguments
    :param dict options: Keyword arguments
    :return: Detached operation result"""
    from django.db import close_old_connections, connection

    close_old_connections()
    try:
        return function(*arguments, **options)
    finally:
        connection.close()


@contextmanager
def acquire_pilot_lock(directory):
    """
    Prevents two local gateway sessions from controlling the sample concurrently

    :param Path directory: Validated private Linux runtime directory"""
    import fcntl
    import os
    import stat

    descriptor = os.open(directory / "discord-pilot.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or metadata.st_uid != os.getuid():
            raise StagingConfigurationError("The local Discord session lock is invalid.")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise StagingConfigurationError("A local Discord pilot is already running. Stop its window first.") from None
        yield
    finally:
        os.close(descriptor)


def create_pilot_bot(snapshot, expected_bot_id, channel_id, public_origin=None):
    """
    Builds a guild-only client without loading the production bot or its queues

    :param dict snapshot: Validated detached local fixture
    :param int expected_bot_id: Independently pinned DEV application
    :param int channel_id: Explicit DEV test channel
    :param str public_origin: Optional validated HTTPS origin enabling shared referees
    :return: Configured Pycord client, not yet connected"""
    import discord
    from asgiref.sync import sync_to_async

    from corpoch.match_actions import MatchActionError
    from staging.discord_controls import create_controls
    from staging.discord_fixture import (
        apply_control_action, build_control_snapshot, grant_shared_referee, revoke_shared_referee,
    )
    from staging.viewer_fixture import revoke_fixture_access

    if public_origin is not None:
        from staging.sharing import validate_public_origin

        public_origin = validate_public_origin(public_origin)

    class PilotBot(discord.Bot):
        """Responds only to explicitly authorized humans in the pinned DEV channel."""

        def __init__(self):
            intents = discord.Intents.none()
            intents.guilds = True
            intents.members = True
            super().__init__(intents=intents, auto_sync_commands=False, chunk_guilds_at_startup=False,
                             allowed_mentions=discord.AllowedMentions.none())
            self.log = logging.getLogger(__name__)
            self.pilot_snapshot = snapshot
            self.pilot_lock = asyncio.Lock()
            self.active_view = None
            self.active_views = {}
            self.public_origin = public_origin
            self.registered = False
            self.failed = False
            self.pilot_command = self.slash_command(
                name="viewer-pilot", description="Open the isolated local sample match controls",
                guild_ids=[snapshot["guild_id"]],
            )(self.open_controls)

        def in_scope(self, interaction):
            """Checks destinations before responding or reading member metadata."""
            return (
                interaction.guild_id == snapshot["guild_id"]
                and interaction.channel_id == channel_id
                and interaction.application_id == expected_bot_id
            )

        async def revoke_member(self, account_id):
            """Revokes only the confirmed caller's local referee grant."""
            if public_origin is None:
                await sync_to_async(run_database_call)(revoke_fixture_access)
            else:
                await sync_to_async(run_database_call)(revoke_shared_referee, account_id, snapshot["guild_id"])

        async def current_member(self, account_id=None):
            """Reads current human membership directly from the pinned guild."""
            account_id = snapshot["account_id"] if account_id is None else account_id
            guild = self.get_guild(snapshot["guild_id"])
            if guild is None:
                raise StagingConfigurationError("The DEV server is unavailable to this bot.")
            member = await asyncio.wait_for(guild.fetch_member(account_id), timeout=15)
            if (
                member.id != account_id or member.bot or member.pending
                or not {role.id for role in member.roles}.intersection(snapshot["role_ids"])
            ):
                await self.revoke_member(account_id)
                raise StagingConfigurationError("This member does not have an approved DEV referee role.")
            return member

        async def read_snapshot(self, account_id):
            """Reads the sample with only this session's explicit authorization policy."""
            options = {"shared_referees": True} if public_origin is not None else {}
            return await sync_to_async(run_database_call)(
                build_control_snapshot, account_id, snapshot["guild_id"], **options,
            )

        async def notify(self, interaction, message):
            """Sends only a private response within the pinned test channel."""
            if not self.in_scope(interaction):
                return
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
            else:
                await interaction.response.send_message(message, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

        async def authorize(self, interaction):
            """Checks destination, human identity, fresh roles and stored local access."""
            if not self.registered or self.failed or not self.in_scope(interaction):
                return False
            if interaction.user.bot or (public_origin is None and interaction.user.id != snapshot["account_id"]):
                message = (
                    "Only human DEV referees can use these controls." if public_origin
                    else "Only the signed-in sample owner can use these controls."
                )
                await self.notify(interaction, message)
                return False
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True, invisible=True)
            try:
                await self.current_member(interaction.user.id)
                if public_origin is not None:
                    await sync_to_async(run_database_call)(grant_shared_referee, interaction.user.id, snapshot["guild_id"])
                await self.read_snapshot(interaction.user.id)
            except discord.NotFound:
                await self.revoke_member(interaction.user.id)
                await self.notify(interaction, "DEV membership is unavailable. Local sample access was revoked.")
                return False
            except (StagingConfigurationError, discord.HTTPException, asyncio.TimeoutError):
                await self.notify(interaction, "Current referee access could not be verified. No action was applied.")
                return False
            return True

        async def show_controls(self, interaction, current):
            """Replaces one private control message with the current saved state."""
            if not self.in_scope(interaction) or (public_origin is None and interaction.user.id != snapshot["account_id"]):
                return
            view, embed = await create_controls(current, self.authorize, self.perform, self.notify)
            content = "DEV sample only · changes appear in your local match viewer"
            if public_origin is not None:
                view.add_item(discord.ui.Button(label="Open viewer", url=f"{public_origin}/auth/start"))
                content = "DEV sample only · Open viewer and sign in with Discord once to watch live updates."
            try:
                await interaction.edit_original_response(
                    content=content,
                    embed=embed, view=view, allowed_mentions=discord.AllowedMentions.none(),
                )
            except Exception:
                view.stop()
                raise
            if public_origin is None:
                if self.active_view is not None:
                    self.active_view.stop()
                self.active_view = view
            else:
                previous = self.active_views.pop(interaction.user.id, None)
                if previous is not None:
                    previous.stop()
                while len(self.active_views) >= 20:
                    oldest_account = next(iter(self.active_views))
                    self.active_views.pop(oldest_account).stop()
                self.active_views[interaction.user.id] = view

        async def open_controls(self, context):
            """Opens controls only after a human invokes the DEV slash command."""
            interaction = context.interaction
            async with self.pilot_lock:
                if await self.authorize(interaction):
                    current = await self.read_snapshot(interaction.user.id)
                    await self.show_controls(interaction, current)

        async def perform(self, interaction, action, expected_state, chart_id=None, player_id=None):
            """Rechecks access and applies an existing action with its captured token."""
            async with self.pilot_lock:
                if not await self.authorize(interaction):
                    return
                try:
                    options = {"shared_referees": True} if public_origin is not None else {}
                    current = await sync_to_async(run_database_call)(
                        apply_control_action, action, expected_state, interaction.user.id, snapshot["guild_id"],
                        chart_id=chart_id, player_id=player_id, **options,
                    )
                except MatchActionError:
                    await self.notify(interaction, "The match changed or this action is unavailable. Review the refreshed controls.")
                    current = await self.read_snapshot(interaction.user.id)
                except StagingConfigurationError:
                    await self.notify(interaction, "The local sample is unavailable. No action was applied.")
                    return
                await self.show_controls(interaction, current)

        async def on_interaction(self, interaction):
            """Excludes other servers, channels and applications from command dispatch."""
            if self.registered and self.in_scope(interaction) and interaction.data.get("name") == "viewer-pilot":
                await self.process_application_commands(interaction, auto_sync=False)

        async def on_ready(self):
            """Serializes reconnect readiness with the same local control session."""
            async with self.pilot_lock:
                await self.register_pilot()

        async def register_pilot(self):
            """Registers only the single DEV guild command without deleting others."""
            if self.registered or self.failed or self.is_closed():
                return
            try:
                if self.user.id != expected_bot_id or {guild.id for guild in self.guilds} != {snapshot["guild_id"]}:
                    raise StagingConfigurationError("This pilot requires the expected bot in only the DEV server.")
                channel = await asyncio.wait_for(self.fetch_channel(channel_id), timeout=15)
                if not isinstance(channel, discord.TextChannel) or channel.guild.id != snapshot["guild_id"]:
                    raise StagingConfigurationError("The selected channel is not the DEV text channel.")
                permissions = channel.permissions_for(channel.guild.me)
                if not all((permissions.view_channel, permissions.send_messages, permissions.embed_links)):
                    raise StagingConfigurationError("The DEV bot needs View Channel, Send Messages and Embed Links here.")
                await self.current_member()
                await asyncio.wait_for(self.register_commands(
                    commands=[self.pilot_command], guild_id=snapshot["guild_id"],
                    method="individual", force=False, delete_existing=False,
                ), timeout=30)
                if self.is_closed():
                    return
                self.registered = True
                audience = "approved DEV referees" if public_origin is not None else "the sample owner"
                print(f"READY: Use /viewer-pilot in the DEV test channel. Controls are private to {audience}.", flush=True)
            except Exception:
                self.failed = True
                print("STOP: DEV startup verification failed. No match command was enabled by this session.", flush=True)
                await self.close()

        async def on_error(self, event_method, *arguments, **options):
            """Reports failures without credential, interaction-token or response details."""
            print("A DEV pilot event failed. Reopen /viewer-pilot and inspect the saved match before retrying.", flush=True)

        async def on_application_command_error(self, context, exception):
            """Keeps command failures private and prevents raw traceback output."""
            await self.notify(context.interaction, "The DEV command could not finish. Inspect the viewer before retrying.")

        async def close(self):
            """Stops the ephemeral controls before closing this client only."""
            self.registered = False
            if self.active_view is not None:
                self.active_view.stop()
            for view in self.active_views.values():
                view.stop()
            self.active_views.clear()
            await super().close()

    return PilotBot()


async def run_gateway(snapshot, expected_bot_id, channel_id, bot_token, public_origin=None):
    """
    Verifies the authenticated bot before opening its gateway connection

    :param dict snapshot: Validated owned sample
    :param int expected_bot_id: Pinned DEV application
    :param int channel_id: Pinned DEV text channel
    :param str bot_token: Private in-memory credential
    :param str public_origin: Optional public HTTPS viewer origin
    :return: Process exit status"""
    bot = create_pilot_bot(snapshot, expected_bot_id, channel_id, public_origin)
    try:
        await asyncio.wait_for(bot.login(bot_token), timeout=30)
        if bot.user.id != expected_bot_id or not bot.user.bot:
            raise StagingConfigurationError("The credential belongs to a different Discord bot.")
        await bot.connect(reconnect=True)
        return 1 if bot.failed else 0
    finally:
        await bot.close()


async def check_controls(snapshot, expected_bot_id, channel_id, public_origin=None):
    """
    Builds and serializes the actual client and controls without connecting

    :param dict snapshot: Validated detached sample
    :param int expected_bot_id: Pinned DEV application
    :param int channel_id: Pinned DEV channel
    :param str public_origin: Optional public HTTPS viewer origin"""
    from staging.discord_controls import create_controls

    bot = create_pilot_bot(snapshot, expected_bot_id, channel_id, public_origin)
    try:
        view, embed = await create_controls(snapshot, bot.authorize, bot.perform)
        try:
            if public_origin is not None:
                import discord

                view.add_item(discord.ui.Button(label="Open viewer", url=f"{bot.public_origin}/auth/start"))
            view.to_components()
            embed.to_dict()
            bot.pilot_command.to_dict()
        finally:
            view.stop()
    finally:
        await bot.close()


def main(arguments=None):
    """
    Checks the isolated database before explicitly starting the DEV gateway

    :param list arguments: Optional explicit command-line arguments
    :return: Process exit status"""
    parser = argparse.ArgumentParser(description="Run the isolated sample's DEV Discord controls.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--expected-bot-id", required=True)
    parser.add_argument("--guild-id", required=True)
    parser.add_argument("--channel-id", required=True)
    parser.add_argument("--credentials-file", required=True)
    parser.add_argument("--public-origin", help="Optional temporary HTTPS viewer origin enabling approved DEV referees.")
    parser.add_argument("command", choices=("check", "run"))
    options = parser.parse_args(arguments)
    try:
        if not all(validate_snowflake(value) for value in (options.expected_bot_id, options.guild_id, options.channel_id)):
            raise StagingConfigurationError("Valid DEV application, server and channel identifiers are required.")
        configuration = load_configuration(options.config, options.expected_bot_id)
        configure_web_runtime(configuration)
        import django
        from django.db import connection

        django.setup()
        validate_database_boundary(connection, configuration)
        from staging.discord_fixture import build_control_snapshot

        snapshot = run_database_call(build_control_snapshot)
        if snapshot["guild_id"] != int(options.guild_id):
            raise StagingConfigurationError("The sample does not belong to the explicitly selected DEV server.")
        credentials = parse_discord_credentials(read_private_text(Path(options.credentials_file), "DEV credential file"))
        token = credentials.get("BOT_TOKEN")
        if not token:
            raise StagingConfigurationError("The private DEV bot credential is missing.")
        del credentials
        if options.command == "check":
            asyncio.run(check_controls(snapshot, int(options.expected_bot_id), int(options.channel_id), options.public_origin))
            print("PASS: Owned sample, local access, database, credential structure and Discord controls are ready.")
            print("No Discord connection, command registration or message was sent.")
            return 0
        with acquire_pilot_lock(configuration.runtime_root):
            return asyncio.run(run_gateway(snapshot, int(options.expected_bot_id), int(options.channel_id), token, options.public_origin))
    except KeyboardInterrupt:
        print("Stopped the local Discord pilot. The website and database remain running.")
        return 0
    except (StagingConfigurationError, StagingInputError) as error:
        print(f"STOP: {error}", file=sys.stderr)
        return 1
    except Exception:
        print("STOP: The DEV pilot could not finish. Private error details were not printed.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
