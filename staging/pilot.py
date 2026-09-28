"""Prepares and exercises one local sample match without starting a bot."""

import argparse
import json
from pathlib import Path
import sys
import time

from staging.configuration import StagingConfigurationError, load_configuration, prepare_private_directories
from staging.referee_check import RefereeCheckError
from staging.runtime import configure_web_runtime, validate_database_boundary
from tests.staging_preflight import StagingInputError


def find_signed_in_account(display_name):
    """
    Resolves one existing active OAuth account without exposing its credentials

    :param str display_name: Exact name shown by the local sign-in checkpoint
    :return: Existing account"""
    from corpoch.models import DiscordUser

    accounts = list(DiscordUser.objects.filter(
        global_name=display_name, is_active=True, last_login__isnull=False,
        token__isnull=False,
    ).only("id", "global_name")[:2])
    if len(accounts) != 1 or accounts[0].global_name != display_name:
        raise StagingConfigurationError("Sign in first, then select the exact unambiguous displayed account name.")
    return accounts[0]


def prepare_sample(options, configuration):
    """
    Verifies the selected DEV membership before writing the local fixture

    :param Namespace options: Explicit preparation arguments
    :param WebConfiguration configuration: Validated local database profile"""
    from staging.referee_check import verify_referee
    from staging.viewer_fixture import prepare_fixture
    from tests.staging_preflight import parse_discord_credentials, read_private_text

    if not options.credentials_file or not options.guild_id or not options.referee_role_id or not options.account_name:
        raise StagingConfigurationError("Preparation requires the private credential file, DEV guild, referee roles and displayed account name.")
    account = find_signed_in_account(options.account_name)
    credentials = parse_discord_credentials(read_private_text(Path(options.credentials_file), "DEV credentials"))
    if not credentials.get("BOT_TOKEN"):
        raise StagingConfigurationError("The private DEV credential file is missing its bot token.")
    roles = verify_referee(
        credentials["BOT_TOKEN"], configuration.bot_id, options.guild_id,
        str(account.pk), options.referee_role_id,
    )
    del credentials
    match_id = prepare_fixture(
        int(options.guild_id), account.pk,
        [{"id": int(role["id"]), "name": role["name"]} for role in roles],
    )
    print("PASS: Discord confirmed an approved DEV referee role for the signed-in account.")
    print(f"PASS: The local sample is ready at /match-viewer/{match_id}/.")
    print("Only local referee membership and synthetic match records were prepared. No Discord messages or exports were sent.")


def inspect_sample():
    """
    Checks the real reader and renderer and reports bounded local measurements

    :return: Current match action token"""
    from django.conf import settings
    from django.db import connection
    from django.test import RequestFactory
    from django.test.utils import CaptureQueriesContext
    from corpoch.match_actions import get_match_state_token
    from corpoch.match_viewer_views import match_viewer, match_viewer_state
    from staging.viewer_fixture import validate_fixture

    match = validate_fixture()
    settings.MATCH_VIEWER_ENABLED = True
    settings.MATCH_VIEWER_MYSQL_VERIFIED = True
    settings.MATCH_VIEWER_POLLING_ENABLED = False
    request = RequestFactory().get(f"/match-viewer/{match.pk}/")
    request.user = match.referee
    for label, view in (("page", match_viewer), ("fragment", match_viewer_state)):
        started = time.monotonic()
        with CaptureQueriesContext(connection) as queries:
            response = view(request, match.pk)
        if response.status_code != 200 or "no-store" not in response.get("Cache-Control", ""):
            raise StagingConfigurationError("The sample match did not pass its authenticated read-only rendering check.")
        print(f"PASS: Sample {label}: {len(queries)} queries, {len(response.content)} bytes, {time.monotonic() - started:.3f} seconds.")
    from django.contrib.auth.models import AnonymousUser

    request.user = AnonymousUser()
    if match_viewer_state(request, match.pk).status_code != 401:
        raise StagingConfigurationError("The sample match did not deny an anonymous request.")
    token = get_match_state_token(match)
    print(json.dumps({"match": str(match.pk), "action_token": token}))
    print("Measurements describe one local sample request, not production capacity.")
    return token


def main(arguments=None):
    """
    Runs one explicit operation against the provisioned staging database

    :param list arguments: Optional arguments for isolated tests
    :return: Process exit status"""
    parser = argparse.ArgumentParser(description="Prepare or inspect the isolated local sample match.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--expected-bot-id", required=True)
    parser.add_argument("--credentials-file")
    parser.add_argument("--guild-id")
    parser.add_argument("--referee-role-id", action="append", default=[])
    parser.add_argument("--account-name")
    parser.add_argument("--expected-state")
    parser.add_argument("command", choices=("prepare", "inspect", "pick", "win-p1", "win-p2", "undo", "finalize", "revoke"))
    options = parser.parse_args(arguments)
    connection = None
    try:
        configuration = load_configuration(options.config, options.expected_bot_id)
        prepare_private_directories(configuration)
        configure_web_runtime(configuration)
        import django
        from django.db import connection

        django.setup()
        validate_database_boundary(connection, configuration)
        if options.command == "prepare":
            prepare_sample(options, configuration)
        elif options.command == "inspect":
            inspect_sample()
        elif options.command == "revoke":
            from staging.viewer_fixture import revoke_fixture_access

            revoke_fixture_access()
            print("PASS: Local sample referee access removed. The viewer will deny the next request.")
        else:
            from corpoch.match_actions import MatchActionError
            from staging.viewer_fixture import advance_fixture

            if not options.expected_state:
                raise StagingConfigurationError("Inspect the sample first and supply its current action token.")
            try:
                advance_fixture(options.command, options.expected_state)
            except MatchActionError as error:
                raise StagingConfigurationError(str(error)) from None
            print("PASS: The local sample changed using the supported match action.")
            inspect_sample()
    except (StagingConfigurationError, StagingInputError, RefereeCheckError) as error:
        print(f"STOP: {error}", file=sys.stderr)
        return 1
    except Exception:
        print("STOP: The sample checkpoint could not complete. Private error details were not printed.", file=sys.stderr)
        return 1
    finally:
        if connection is not None:
            connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
