"""Checks pinned DEV identities and access with read-only service requests."""

import argparse
from dataclasses import dataclass
import json
import logging
from pathlib import Path
import re
import time

from google.oauth2 import service_account
import requests

from tests.staging_preflight import (
    StagingInputError,
    create_unique_object,
    load_inventory,
    load_json_object,
    parse_discord_credentials,
    read_private_text,
    validate_google_credentials,
)


class StagingServiceError(ValueError):
    """Contains a fixed public explanation without response or credential data."""


class ServiceArgumentParser(argparse.ArgumentParser):
    """Keeps rejected CLI values out of diagnostics."""

    def error(self, message):
        """
        Replaces diagnostics that could contain private arguments

        :param str message: Original argparse diagnostic"""
        raise StagingInputError(
            "Provide --resources and all four --expected-*-id arguments; "
            "optional --referee-role-id may be repeated.",
        )


@dataclass(frozen=True)
class ServiceCheckResult:
    """Retains only safe check labels and a fixed failure explanation."""

    passed: tuple[str, ...]
    issue: str | None = None


@dataclass(frozen=True)
class BoundedResponse:
    """Adapts a bounded response to the google-auth transport interface."""

    status: int
    data: bytes
    headers: dict


class ServiceTransport:
    """Limits requests to explicit official endpoints with bounded responses."""

    def __init__(self, inventory):
        self.log = logging.getLogger(__name__)
        self.session = requests.Session()
        self.session.trust_env = False
        self.maximum_bytes = 1048576
        self.allowed_requests = {
            ("GET", "https://discord.com/api/v10/users/@me"),
            ("GET", "https://discord.com/api/v10/applications/@me"),
            ("GET", f"https://discord.com/api/v10/channels/{inventory['discord_test_channel_id']}"),
            ("GET", f"https://discord.com/api/v10/guilds/{inventory['discord_guild_id']}/roles"),
            ("POST", "https://oauth2.googleapis.com/token"),
            ("GET", f"https://sheets.googleapis.com/v4/spreadsheets/{inventory['google_spreadsheet_id']}"),
            ("GET", f"https://www.googleapis.com/drive/v3/files/{inventory['google_spreadsheet_id']}"),
        }
        self.token_requests = 0

    def close(self):
        """Closes the dedicated session without retaining its connection pool."""
        self.session.close()

    def request(self, method, url, headers=None, body=None, parameters=None):
        """
        Sends one fixed request without proxies, redirects or automatic retries

        :param str method: Allowlisted HTTP method
        :param str url: Exact allowlisted endpoint
        :param dict headers: Explicit authentication and protocol headers
        :param bytes body: Optional Google token exchange body
        :param dict parameters: Metadata-only query parameters
        :return: Bounded response detached from the HTTP connection"""
        if (method, url) not in self.allowed_requests:
            raise StagingServiceError("A request outside the fixed service check was blocked.")
        if method == "POST":
            self.token_requests += 1
            if self.token_requests > 1:
                raise StagingServiceError("Google authentication did not complete on its first attempt. Try again later.")
        started = time.monotonic()
        try:
            with self.session.request(
                method,
                url,
                headers=headers,
                data=body,
                params=parameters,
                timeout=(5, 15),
                allow_redirects=False,
                verify=True,
                stream=True,
            ) as response:
                if 300 <= response.status_code < 400:
                    raise StagingServiceError("A service returned a redirect; no redirect was followed.")
                contents = bytearray()
                for chunk in response.iter_content(chunk_size=16384):
                    contents.extend(chunk)
                    # The read timeout limits inactivity. Elapsed time is checked
                    # between chunks; it is not a hard total request deadline.
                    if len(contents) > self.maximum_bytes or time.monotonic() - started > 30:
                        raise StagingServiceError("A service response exceeded the check's size or time limit.")
                return BoundedResponse(response.status_code, bytes(contents), {})
        except StagingServiceError:
            raise
        except requests.RequestException:
            raise StagingServiceError("A service request failed or timed out. Check the connection and try again.") from None

    def google_auth_request(self, url, method="GET", body=None, headers=None, **keywords):
        """
        Restricts the google-auth callback to its one official token endpoint

        :param str url: Token endpoint requested by google-auth
        :param str method: Authentication request method
        :param bytes body: Signed service-account assertion
        :param dict headers: Google authentication headers
        :param dict keywords: Library options; fixed transport limits take precedence
        :return: Bounded google-auth transport response"""
        if url != "https://oauth2.googleapis.com/token" or method != "POST":
            raise StagingServiceError("An unexpected Google authentication destination was blocked.")
        response = self.request(method, url, headers=headers, body=body)
        if response.status != 200:
            raise StagingServiceError("Google authentication failed. Check the test service-account key and enabled APIs.")
        return response

    def get_json(self, url, authorization, label, parameters=None):
        """
        Decodes successful metadata responses with fixed public error messages

        :param str url: Exact allowlisted metadata endpoint
        :param str authorization: In-memory service credential
        :param str label: Fixed public check description
        :param dict parameters: Metadata-only field selection
        :return: Parsed JSON metadata"""
        response = self.request(
            "GET", url,
            headers={"Authorization": authorization, "Accept": "application/json"},
            parameters=parameters,
        )
        if response.status != 200:
            reason = {
                401: "credentials were rejected",
                403: "access was denied; check permissions and enabled APIs",
                404: "the resource was unavailable to this account",
                429: "the service rate limit was reached; retry later",
            }.get(response.status, "the service did not return a successful response")
            raise StagingServiceError(f"{label}: {reason}.")
        try:
            return json.loads(response.data, object_pairs_hook=create_unique_object)
        except (ValueError, RecursionError):
            raise StagingServiceError(f"{label}: the service returned invalid metadata.") from None


def validate_snowflake(value):
    """
    Checks a Discord identifier without copying it into diagnostics

    :param str value: Expected Discord identifier
    :return: Whether the identifier is valid"""
    return (
        isinstance(value, str)
        and re.fullmatch(r"[1-9][0-9]{16,19}", value) is not None
        and int(value) <= 18446744073709551615
    )


def validate_expected_resources(inventory, expected, referee_roles):
    """
    Compares every intended destination before credentials are read

    :param dict inventory: Validated private resource inventory
    :param dict expected: Explicit independently supplied resource identifiers
    :param list referee_roles: Optional expected human referee role identifiers"""
    fields = (
        "discord_bot_id", "discord_guild_id", "discord_test_channel_id",
        "google_spreadsheet_id",
    )
    if set(expected) != set(fields):
        raise StagingInputError("All four expected resource identifiers are required.")
    if any(not validate_snowflake(expected[field]) for field in fields[:3]):
        raise StagingInputError("Expected Discord identifiers must be valid ID strings.")
    if (
        not isinstance(expected[fields[3]], str)
        or re.fullmatch(r"[A-Za-z0-9_-]{20,128}", expected[fields[3]]) is None
    ):
        raise StagingInputError("The expected spreadsheet identifier must be an ID, not a link.")
    if any(not validate_snowflake(role) for role in referee_roles) or len(set(referee_roles)) != len(referee_roles):
        raise StagingInputError("Referee role identifiers must be valid and unique.")
    if any(inventory[field] != expected[field] for field in fields):
        raise StagingInputError("The inventory does not match the four expected DEV resources. No credentials were read.")
    if inventory["discord_guild_id"] in referee_roles:
        raise StagingInputError("The everyone role cannot identify human referees.")


def check_discord_access(transport, inventory, bot_token, referee_roles, passed):
    """
    Verifies the bot identity before checking its intended server resources

    :param ServiceTransport transport: Restricted HTTP transport
    :param dict inventory: Pinned resource inventory
    :param str bot_token: Private bot credential
    :param list referee_roles: Expected human referee roles
    :param list passed: Public labels for completed checks"""
    authorization = f"Bot {bot_token}"
    identity = transport.get_json(
        "https://discord.com/api/v10/users/@me", authorization, "Discord bot identity",
    )
    if not isinstance(identity, dict) or identity.get("id") != inventory["discord_bot_id"] or identity.get("bot") is not True:
        raise StagingServiceError("Discord authenticated a different identity. No server resources were checked.")
    passed.append("Discord authenticated the expected DEV bot.")
    application = transport.get_json(
        "https://discord.com/api/v10/applications/@me", authorization, "Discord application",
    )
    if not isinstance(application, dict) or application.get("id") != inventory["discord_bot_id"]:
        raise StagingServiceError("Discord application identity did not match the DEV bot.")
    flags = application.get("flags_new", application.get("flags"))
    if isinstance(flags, str) and re.fullmatch(r"[0-9]{1,30}", flags):
        flags = int(flags)
    if type(flags) is not int or flags < 0 or not flags & ((1 << 14) | (1 << 15)):
        raise StagingServiceError("Enable Server Members Intent on the DEV application's Bot page, then retry.")
    passed.append("The DEV application reports Server Members Intent enabled.")
    channel = transport.get_json(
        f"https://discord.com/api/v10/channels/{inventory['discord_test_channel_id']}",
        authorization, "Discord test channel",
    )
    if (
        not isinstance(channel, dict)
        or channel.get("id") != inventory["discord_test_channel_id"]
        or channel.get("guild_id") != inventory["discord_guild_id"]
        or type(channel.get("type")) is not int
        or channel["type"] != 0
    ):
        raise StagingServiceError("The accessible test channel is not the expected server text channel.")
    passed.append("The bot can read metadata for the expected server text channel.")
    roles = transport.get_json(
        f"https://discord.com/api/v10/guilds/{inventory['discord_guild_id']}/roles",
        authorization, "Discord server roles",
    )
    if not isinstance(roles, list) or any(not isinstance(role, dict) or not validate_snowflake(role.get("id")) for role in roles):
        raise StagingServiceError("Discord returned incomplete server role metadata.")
    matched = {role["id"]: role for role in roles if role["id"] in referee_roles}
    if set(matched) != set(referee_roles) or any(role.get("managed") is not False for role in matched.values()):
        raise StagingServiceError("An expected human referee role is missing or managed by an integration.")
    passed.append(
        "The expected human referee roles exist in the DEV server."
        if referee_roles else "The bot can read role metadata in the expected DEV server.",
    )


def check_google_access(transport, inventory, google_document, passed):
    """
    Reads spreadsheet identity and edit-capability metadata without reading cells

    :param ServiceTransport transport: Restricted HTTP transport
    :param dict inventory: Pinned resource inventory
    :param dict google_document: Validated private service-account key
    :param list passed: Public labels for completed checks"""
    try:
        credential = service_account.Credentials.from_service_account_info(
            {
                "type": "service_account",
                "client_email": google_document["client_email"],
                "private_key": google_document["private_key"],
                "token_uri": "https://oauth2.googleapis.com/token",
            },
            scopes=(
                "https://www.googleapis.com/auth/spreadsheets.readonly",
                "https://www.googleapis.com/auth/drive.metadata.readonly",
            ),
        )
        credential.refresh(transport.google_auth_request)
        if not isinstance(credential.token, str) or not credential.token:
            raise StagingServiceError("Google authentication returned no usable access token.")
    except StagingServiceError:
        raise
    except Exception:
        raise StagingServiceError("Google authentication failed. Check the test service-account key and system clock.") from None
    passed.append("Google authenticated the supplied test service-account key with read-only scopes.")
    authorization = f"Bearer {credential.token}"
    spreadsheet_id = inventory["google_spreadsheet_id"]
    spreadsheet = transport.get_json(
        f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}",
        authorization, "Google Sheets metadata", parameters={"fields": "spreadsheetId"},
    )
    if not isinstance(spreadsheet, dict) or spreadsheet.get("spreadsheetId") != spreadsheet_id:
        raise StagingServiceError("Google Sheets did not confirm the expected test spreadsheet identity.")
    passed.append("Google Sheets confirmed access to the expected test spreadsheet metadata.")
    file_metadata = transport.get_json(
        f"https://www.googleapis.com/drive/v3/files/{spreadsheet_id}",
        authorization, "Google Drive metadata",
        parameters={"fields": "id,mimeType,trashed,capabilities(canEdit,canModifyContent)"},
    )
    if (
        not isinstance(file_metadata, dict)
        or file_metadata.get("id") != spreadsheet_id
        or file_metadata.get("mimeType") != "application/vnd.google-apps.spreadsheet"
        or file_metadata.get("trashed") is not False
    ):
        raise StagingServiceError("Google Drive did not confirm an active test spreadsheet.")
    capabilities = file_metadata.get("capabilities")
    if not isinstance(capabilities, dict) or capabilities.get("canEdit") is not True or capabilities.get("canModifyContent") is not True:
        raise StagingServiceError("Google does not report permission to edit the test spreadsheet. Check sharing and restrictions.")
    passed.append("Google reports permission to edit the test spreadsheet; no edit was attempted.")


def check_staging_services(resources_path, expected, referee_roles=()):
    """
    Runs the explicit read-only checks without application imports or workers

    :param str resources_path: Absolute private inventory path
    :param dict expected: Independently supplied destination identifiers
    :param tuple referee_roles: Optional human referee role identifiers
    :return: Safe result without credentials or response metadata"""
    passed = []
    transport = None
    try:
        inventory = load_inventory(resources_path)
        validate_expected_resources(inventory, expected, referee_roles)
        discord_values = parse_discord_credentials(
            read_private_text(Path(inventory["credentials_file"]), "Discord credential file"),
        )
        if not all(discord_values.get(field) for field in ("BOT_TOKEN", "BOT_SECRET")):
            raise StagingInputError("Both BOT_TOKEN and BOT_SECRET must be present in the private credential file.")
        google_document = load_json_object(Path(inventory["google_service_account_file"]), "Google service-account file")
        issues = validate_google_credentials(google_document)
        if issues:
            raise StagingInputError(" ".join(issues))
        passed.append("Local inputs match the four explicitly expected DEV resources.")
        transport = ServiceTransport(inventory)
        check_discord_access(transport, inventory, discord_values["BOT_TOKEN"], referee_roles, passed)
        check_google_access(transport, inventory, google_document, passed)
        return ServiceCheckResult(tuple(passed))
    except (StagingInputError, StagingServiceError) as error:
        return ServiceCheckResult(tuple(passed), str(error))
    except Exception:
        return ServiceCheckResult(tuple(passed), "The check could not finish. Review local inputs, dependencies and service availability.")
    finally:
        if transport is not None:
            transport.close()


def main(arguments=None):
    """
    Prints credential-free results with zero for success or two for incomplete

    :param list arguments: Optional command-line arguments
    :return: Process exit status"""
    parser = ServiceArgumentParser(prog="python -B -m tests.staging_service_check", description=__doc__)
    parser.add_argument("--resources", required=True)
    for field in ("bot", "guild", "channel", "spreadsheet"):
        parser.add_argument(f"--expected-{field}-id", required=True)
    parser.add_argument("--referee-role-id", action="append", default=[])
    try:
        options = parser.parse_args(arguments)
        result = check_staging_services(
            options.resources,
            {
                "discord_bot_id": options.expected_bot_id,
                "discord_guild_id": options.expected_guild_id,
                "discord_test_channel_id": options.expected_channel_id,
                "google_spreadsheet_id": options.expected_spreadsheet_id,
            },
            options.referee_role_id,
        )
    except (StagingInputError, StagingServiceError) as error:
        result = ServiceCheckResult((), str(error))
    except Exception:
        result = ServiceCheckResult((), "The service check could not finish. No private error details were displayed.")
    for label in result.passed:
        print(f"PASS: {label}")
    if result.issue:
        print(f"NOT READY: {result.issue}")
    print("No Discord messages, database changes, bot startup, worker startup or spreadsheet edits were performed.")
    print("BOT_SECRET authenticity, channel write permissions and export behavior remain unverified.")
    print("Referee role discovery does not enable role authorization in the application.")
    return 2 if result.issue else 0


if __name__ == "__main__":
    raise SystemExit(main())
