"""Entry point for the fixed local web staging commands."""

import argparse
import sys

from staging.configuration import (
    StagingConfigurationError, load_configuration, prepare_private_directories,
)
from staging.runtime import run_command


def main(arguments=None):
    """
    Validates the private configuration before running an allowed command

    :param list arguments: Optional command-line arguments for isolated tests
    :return: Process exit status"""
    parser = argparse.ArgumentParser(description="Run the dedicated local web-only staging instance.")
    parser.add_argument("--config", required=True, help="Absolute path to the private Linux JSON configuration.")
    parser.add_argument("--expected-bot-id", required=True, help="Independently confirmed DEV application ID.")
    parser.add_argument("command", choices=("check", "migrate", "serve", "serve-viewer", "serve-viewer-live"))
    options = parser.parse_args(arguments)
    try:
        configuration = load_configuration(options.config, options.expected_bot_id)
        prepare_private_directories(configuration)
        run_command(configuration, options.command)
    except StagingConfigurationError as error:
        print(f"STOP: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Local web staging stopped.")
        return 0
    except Exception:
        print("STOP: Local web staging could not complete. Private error details were not printed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
