"""Serves the owned sample on loopback for one explicit temporary HTTPS tunnel."""

import argparse
from pathlib import Path
import sys

from staging.configuration import StagingConfigurationError, load_configuration
from staging.runtime import configure_web_runtime, validate_database_boundary
from staging.sharing import SharedRefereeAccess, SharedSurface, build_shared_settings, validate_public_origin
from tests.staging_preflight import StagingInputError, parse_discord_credentials, read_private_text


def main(arguments=None):
    """Starts an isolated foreground web process without changing saved settings.

    :param list arguments: Optional explicit command-line inputs
    :return: Process exit status"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--expected-bot-id", required=True)
    parser.add_argument("--guild-id", required=True)
    parser.add_argument("--credentials-file", required=True)
    parser.add_argument("--public-origin", required=True)
    parser.add_argument("command", choices=("check", "run"))
    options = parser.parse_args(arguments)
    try:
        origin = validate_public_origin(options.public_origin)
        configuration = load_configuration(options.config, options.expected_bot_id)
        configure_web_runtime(configuration)
        from django.conf import settings
        for name, value in build_shared_settings(configuration, origin).items():
            setattr(settings, name, value)
        settings.MIDDLEWARE = [*settings.MIDDLEWARE, "staging.sharing.SharedRefereeMiddleware"]
        import django
        from django.db import connection
        django.setup()
        validate_database_boundary(connection, configuration)
        from staging.discord_fixture import build_control_snapshot
        snapshot = build_control_snapshot()
        connection.close()
        if str(snapshot["guild_id"]) != options.guild_id:
            raise StagingConfigurationError("The sample does not belong to the expected DEV server.")
        credentials = parse_discord_credentials(read_private_text(Path(options.credentials_file), "DEV credential file"))
        token = credentials.get("BOT_TOKEN")
        if not token:
            raise StagingConfigurationError("The private DEV bot credential is missing.")
        del credentials
        settings.SHARED_DEV_ACCESS = SharedRefereeAccess(token, options.expected_bot_id, snapshot)
        from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler
        from django.core.asgi import get_asgi_application
        from daphne.server import Server
        application = SharedSurface(ASGIStaticFilesHandler(get_asgi_application()), origin)
        server = Server(application, endpoints=["tcp:port=8768:interface=127.0.0.1"],
                        action_logger=None, http_timeout=30, verbosity=0)
        if options.command == "check":
            print("PASS: Shared viewer configuration, owned database, sample and ASGI application are ready.")
            print("No Discord connection, web listener or tunnel was started.")
            return 0
        settings.SHARED_DEV_ACCESS.verify(snapshot["account_id"])
        print("Starting shared DEV viewing on 127.0.0.1:8768. Verify the HTTPS link after startup.", flush=True)
        print("Stop this window with Ctrl+C. The original local website and sample remain available.", flush=True)
        server.run()
        if not server.listening_addresses:
            raise StagingConfigurationError("The shared listener could not open. Check whether another sharing window is already running.")
        return 0
    except KeyboardInterrupt:
        print("Stopped the shared viewer.")
        return 0
    except (StagingConfigurationError, StagingInputError) as error:
        print(f"STOP: {error}", file=sys.stderr)
        return 1
    except Exception:
        print("STOP: Shared viewing could not start. Private error details were not printed.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
