"""Verifies a human referee through a bounded read-only Discord check."""

import json
import logging
import re
import time

import requests


class RefereeCheckError(ValueError):
    """Reports a fixed explanation without credential or response values."""

    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


class RefereeAccessDenied(RefereeCheckError):
    """Distinguishes confirmed access loss from incomplete metadata."""


class RefereeTransport:
    """Allows only the three Discord metadata requests needed for one check."""

    def __init__(self, guild_id, account_id):
        self.log = logging.getLogger(__name__)
        self.session = requests.Session()
        self.session.trust_env = False
        self.maximum_bytes = 1048576
        self.allowed_urls = {
            "https://discord.com/api/v10/users/@me",
            f"https://discord.com/api/v10/guilds/{guild_id}/roles",
            f"https://discord.com/api/v10/guilds/{guild_id}/members/{account_id}",
        }

    def close(self):
        """Closes the dedicated connection pool without retaining credentials."""
        try:
            self.session.close()
        except Exception:
            raise RefereeCheckError("The Discord connection could not be closed cleanly.") from None

    def get_json(self, url, bot_token):
        """
        Reads one allowlisted response without redirects, proxies or retries

        :param str url: Exact official metadata endpoint
        :param str bot_token: In-memory DEV bot credential
        :return: Decoded bounded JSON metadata"""
        if url not in self.allowed_urls:
            raise RefereeCheckError("A request outside the fixed referee check was blocked.")
        started = time.monotonic()
        try:
            with self.session.request(
                "GET", url,
                headers={"Authorization": f"Bot {bot_token}", "Accept": "application/json"},
                timeout=(5, 15),
                allow_redirects=False,
                verify=True,
                stream=True,
            ) as response:
                if response.status_code != 200:
                    raise RefereeCheckError(
                        "Discord did not allow the required metadata check. Review access and try again.",
                        status_code=response.status_code,
                    )
                contents = bytearray()
                for chunk in response.iter_content(chunk_size=16384):
                    contents.extend(chunk)
                    # Read timeouts limit inactivity; elapsed time is checked
                    # between chunks and cannot impose a hard total deadline.
                    if len(contents) > self.maximum_bytes or time.monotonic() - started > 30:
                        raise RefereeCheckError("The Discord response exceeded the check's size or time limit.")
                if time.monotonic() - started > 30:
                    raise RefereeCheckError("The Discord response exceeded the check's size or time limit.")
            return json.loads(contents.decode("utf-8"), object_pairs_hook=build_unique_fields)
        except RefereeCheckError:
            raise
        except requests.RequestException:
            raise RefereeCheckError("The Discord request failed or timed out. Check the connection and try again.") from None
        except (ValueError, RecursionError):
            raise RefereeCheckError("Discord returned invalid metadata for the referee check.") from None
        except Exception:
            raise RefereeCheckError("The Discord metadata check could not finish.") from None


def build_unique_fields(pairs):
    """
    Rejects ambiguous repeated fields in Discord JSON metadata

    :param list pairs: Decoded object key-value pairs
    :return: Object containing unique fields"""
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("Duplicate metadata field.")
        result[name] = value
    return result


def validate_snowflake(value):
    """
    Checks a Discord ID against the staging database's signed integer range

    :param object value: Supplied or returned identifier
    :return: Whether the value is an unambiguous supported ID string"""
    return (
        isinstance(value, str)
        and re.fullmatch(r"[1-9][0-9]{16,18}", value) is not None
        and int(value) < 2 ** 63
    )


def validate_inputs(bot_token, expected_bot_id, guild_id, account_id, role_ids):
    """
    Rejects malformed inputs before creating a connection or reading metadata

    :param str bot_token: In-memory DEV bot credential
    :param str expected_bot_id: Independently pinned DEV bot identity
    :param str guild_id: Intended Discord server
    :param str account_id: Human account to authorize
    :param tuple role_ids: One or two distinct configured referee roles"""
    if not isinstance(bot_token, str) or re.fullmatch(r"[A-Za-z0-9._-]{1,1024}", bot_token) is None:
        raise RefereeCheckError("A valid DEV bot credential is required.")
    if any(not validate_snowflake(value) for value in (expected_bot_id, guild_id, account_id)):
        raise RefereeCheckError("Valid DEV bot, server and human account identifiers are required.")
    if (
        not isinstance(role_ids, (tuple, list)) or not 1 <= len(role_ids) <= 2
        or any(not validate_snowflake(value) for value in role_ids)
        or len(set(role_ids)) != len(role_ids) or guild_id in role_ids
    ):
        raise RefereeCheckError("Provide one or two distinct valid referee roles other than the everyone role.")


def validate_roles(document, role_ids):
    """
    Requires complete unique role metadata and every configured role

    :param object document: Discord server role metadata
    :param tuple role_ids: Configured referee role identifiers
    :return: Configured role IDs and names in supplied order"""
    if not isinstance(document, list):
        raise RefereeCheckError("Discord returned incomplete server role metadata.")
    roles = {}
    for role in document:
        if (
            not isinstance(role, dict) or not validate_snowflake(role.get("id"))
            or not isinstance(role.get("name"), str) or not 1 <= len(role["name"]) <= 100
            or not role["name"].strip() or role["id"] in roles
        ):
            raise RefereeCheckError("Discord returned incomplete server role metadata.")
        roles[role["id"]] = {"id": role["id"], "name": role["name"]}
    if any(role_id not in roles for role_id in role_ids):
        raise RefereeCheckError("A configured referee role is unavailable in the intended server.")
    return tuple(roles[role_id] for role_id in role_ids)


def validate_member(document, account_id, role_ids):
    """
    Confirms the intended human member currently holds a configured role

    :param object document: Discord server member metadata
    :param str account_id: Intended human Discord account
    :param tuple role_ids: Configured referee role identifiers"""
    if not isinstance(document, dict):
        raise RefereeCheckError("Discord did not confirm an authorized human referee.")
    user = document.get("user")
    roles = document.get("roles")
    if (
        not isinstance(user, dict) or user.get("id") != account_id
        or type(user.get("bot", False)) is not bool or type(document.get("pending", False)) is not bool
        or not isinstance(roles, list) or any(not validate_snowflake(role_id) for role_id in roles)
        or len(set(roles)) != len(roles)
    ):
        raise RefereeCheckError("Discord did not confirm an authorized human referee.")
    if user.get("bot", False) or document.get("pending", False) or not set(roles).intersection(role_ids):
        raise RefereeAccessDenied("Discord did not confirm an authorized human referee.")


def verify_referee(bot_token, expected_bot_id, guild_id, account_id, role_ids):
    """
    Authenticates the pinned DEV bot and checks one human member's current roles

    :param str bot_token: In-memory credential that is never persisted
    :param str expected_bot_id: Independently pinned DEV bot identity
    :param str guild_id: Intended Discord server
    :param str account_id: Human account to authorize
    :param tuple role_ids: One or two distinct configured referee roles
    :return: Tuple of configured role ID/name dictionaries after authorization"""
    transport = None
    try:
        validate_inputs(bot_token, expected_bot_id, guild_id, account_id, role_ids)
        transport = RefereeTransport(guild_id, account_id)
        identity = transport.get_json("https://discord.com/api/v10/users/@me", bot_token)
        if not isinstance(identity, dict) or identity.get("id") != expected_bot_id or identity.get("bot") is not True:
            raise RefereeCheckError("Discord authenticated a different identity. No server resources were checked.")
        roles = validate_roles(
            transport.get_json(f"https://discord.com/api/v10/guilds/{guild_id}/roles", bot_token),
            role_ids,
        )
        validate_member(
            transport.get_json(f"https://discord.com/api/v10/guilds/{guild_id}/members/{account_id}", bot_token),
            account_id, role_ids,
        )
        return roles
    except RefereeCheckError:
        raise
    except Exception:
        raise RefereeCheckError("The referee check could not finish. Review inputs and service availability.") from None
    finally:
        if transport is not None:
            transport.close()
