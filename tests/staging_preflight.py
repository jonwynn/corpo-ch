"""Checks private development inputs without loading or starting the application."""

import argparse
import ctypes
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat


class StagingInputError(ValueError):
    """Contains only a safe explanation, never private input or parser output."""


class StagingArgumentParser(argparse.ArgumentParser):
    """Keeps invalid command-line values out of error output."""

    def error(self, message):
        """
        Replaces argparse errors that can contain private argument values

        :param str message: Original parser diagnostic"""
        raise StagingInputError(
            "Use --resources followed by the absolute private inventory file path.",
        )


@dataclass(frozen=True)
class StagingReadiness:
    """Contains reportable results without retaining credential values."""

    inventory_valid: bool
    issues: tuple[str, ...]

    @property
    def ready(self):
        """Reports local structure readiness, not verified service access."""
        return self.inventory_valid and not self.issues


def validate_storage_file(path, allow_missing=False):
    """
    Rejects repository storage, redirected parents and non-regular files

    :param Path path: Absolute private file location
    :param bool allow_missing: Whether an absent final file is a readiness issue"""
    repository = Path(__file__).resolve().parents[1]
    if not path.is_absolute() or ".." in path.parts:
        raise StagingInputError("Private file paths must be absolute without parent traversal.")
    if os.name == "nt":
        if re.fullmatch(r"[A-Za-z]:", path.drive) is None:
            raise StagingInputError("Private inputs must use a local drive, not UNC or device paths.")
        drive_type = ctypes.windll.kernel32.GetDriveTypeW
        drive_type.argtypes = [ctypes.c_wchar_p]
        drive_type.restype = ctypes.c_uint
        if drive_type(path.anchor) not in (2, 3, 6):
            raise StagingInputError("Private inputs must use a local drive, not mapped network storage.")
    if path == repository or repository in path.parents:
        raise StagingInputError("Development inputs must be stored outside the repository.")
    for component in (*reversed(path.parents), path):
        try:
            information = component.lstat()
        except FileNotFoundError:
            if allow_missing and component == path:
                return
            raise StagingInputError("A required private file or parent folder is missing.") from None
        except (OSError, ValueError):
            raise StagingInputError("A private file or parent folder could not be inspected.") from None
        if stat.S_ISLNK(information.st_mode) or (
            getattr(information, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        ):
            raise StagingInputError("Private inputs cannot use links or redirected folders.")
        if component == path:
            if not stat.S_ISREG(information.st_mode) or information.st_nlink != 1:
                raise StagingInputError("Each private input must be a regular file without links.")
        elif not stat.S_ISDIR(information.st_mode):
            raise StagingInputError("A private input parent is not a regular folder.")


def read_private_text(path, label, maximum_bytes=65536):
    """
    Reads one bounded UTF-8 file without exposing paths or decoding exceptions

    :param Path path: Validated private file location
    :param str label: Fixed public file description
    :param int maximum_bytes: Maximum accepted file size
    :return: Decoded text"""
    validate_storage_file(path)
    try:
        inspected = path.lstat()
        with path.open("rb") as source:
            opened = os.fstat(source.fileno())
            if (
                (opened.st_dev, opened.st_ino) != (inspected.st_dev, inspected.st_ino)
                or not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
            ):
                raise StagingInputError(f"The {label} changed while being inspected. Try again.")
            contents = source.read(maximum_bytes + 1)
        if len(contents) > maximum_bytes:
            raise StagingInputError(f"The {label} exceeds the supported file size.")
        return contents.decode("utf-8-sig")
    except (OSError, UnicodeError, ValueError) as error:
        if isinstance(error, StagingInputError):
            raise
        raise StagingInputError(f"The {label} could not be read as UTF-8 text.") from None


def create_unique_object(pairs):
    """
    Rejects ambiguous duplicate keys while decoding JSON

    :param list pairs: JSON object entries
    :return: Unambiguous object"""
    result = {}
    for key, value in pairs:
        if key in result:
            raise StagingInputError("JSON files must not contain duplicate keys.")
        result[key] = value
    return result


def load_json_object(path, label):
    """
    Reads a JSON object with sanitized parse diagnostics

    :param Path path: Private file location
    :param str label: Fixed public file description
    :return: Parsed object"""
    contents = read_private_text(path, label)
    try:
        document = json.loads(contents, object_pairs_hook=create_unique_object)
    except (ValueError, RecursionError):
        raise StagingInputError(f"The {label} must contain valid JSON with unique keys.") from None
    if not isinstance(document, dict):
        raise StagingInputError(f"The {label} must contain a JSON object.")
    return document


def load_inventory(resources_path):
    """
    Validates resource identifiers and anchors both credential paths locally

    :param str resources_path: Explicit private inventory path
    :return: Validated inventory object"""
    path = Path(resources_path)
    document = load_json_object(path, "resource inventory")
    required = {
        "discord_bot_id", "discord_guild_id", "discord_test_channel_id",
        "google_spreadsheet_id", "credentials_file", "google_service_account_file",
    }
    if not required.issubset(document) or set(document) - required - {"purpose"}:
        raise StagingInputError("The resource inventory has missing or unsupported fields.")
    for field in ("discord_bot_id", "discord_guild_id", "discord_test_channel_id"):
        value = document[field]
        if (
            not isinstance(value, str)
            or re.fullmatch(r"[1-9][0-9]{16,19}", value) is None
            or int(value) > 18446744073709551615
        ):
            raise StagingInputError(f"The resource inventory needs a valid {field} string.")
    spreadsheet = document["google_spreadsheet_id"]
    if not isinstance(spreadsheet, str) or re.fullmatch(r"[A-Za-z0-9_-]{20,128}", spreadsheet) is None:
        raise StagingInputError("The resource inventory needs a spreadsheet ID, not an editor link.")
    for field, filename in (
        ("credentials_file", "dev-credentials.env"),
        ("google_service_account_file", "google-service-account.json"),
    ):
        value = document[field]
        if not isinstance(value, str) or Path(value) != path.parent / filename:
            raise StagingInputError(f"The {field} must identify its expected file beside the inventory.")
        validate_storage_file(Path(value), allow_missing=True)
    return document


def check_discord_credentials(path):
    """
    Checks two explicit quoted values without expansion or token validation

    :param Path path: Private credential file
    :return: Readiness issues containing no credential values"""
    if not path.exists():
        return ["dev-credentials.env is missing. Enter BOT_TOKEN and BOT_SECRET in that file."]
    text = read_private_text(path, "Discord credential file")
    values = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.fullmatch(r"\s*(BOT_TOKEN|BOT_SECRET)\s*=\s*([\"'])(.*?)\2\s*", line)
        if match is None or match[1] in values:
            raise StagingInputError(
                "Use only one quoted BOT_TOKEN and one quoted BOT_SECRET assignment; comments and blank lines are allowed.",
            )
        value = match[3]
        if any(character.isspace() or ord(character) < 32 or character in "$%`\\\"'" for character in value):
            raise StagingInputError("Discord credential values cannot contain whitespace, escapes or interpolation.")
        values[match[1]] = value
    return [f"{field} is missing or blank." for field in ("BOT_TOKEN", "BOT_SECRET") if not values.get(field)]


def check_google_credentials(path):
    """
    Checks key structure and the Google token destination without authenticating

    :param Path path: Private service-account file
    :return: Readiness issues containing no key material"""
    if not path.exists():
        return ["google-service-account.json is missing. Import the downloaded service-account key."]
    document = load_json_object(path, "Google service-account file")
    issues = []
    if document.get("type") != "service_account":
        issues.append("The Google key type must be service_account.")
    email = document.get("client_email")
    if not isinstance(email, str) or re.fullmatch(r"[A-Za-z0-9._-]+@[A-Za-z0-9.-]+\.iam\.gserviceaccount\.com", email) is None:
        issues.append("The Google key needs a service-account client_email.")
    private_key = document.get("private_key")
    if (
        not isinstance(private_key, str)
        or re.fullmatch(
            r"-----BEGIN PRIVATE KEY-----\r?\n[A-Za-z0-9+/=\r\n]+\r?\n-----END PRIVATE KEY-----\s*",
            private_key,
        ) is None
    ):
        issues.append("The Google key needs a PEM private_key from the downloaded JSON.")
    if document.get("token_uri") != "https://oauth2.googleapis.com/token":
        issues.append("The Google key token_uri must be https://oauth2.googleapis.com/token.")
    return issues


def check_staging_inputs(resources_path):
    """
    Collects local readiness issues without importing application settings

    :param str resources_path: Explicit private inventory path
    :return: Safe inventory and credential structure results"""
    try:
        inventory = load_inventory(resources_path)
    except StagingInputError as error:
        return StagingReadiness(False, (str(error),))
    issues = []
    for field, checker in (
        ("credentials_file", check_discord_credentials),
        ("google_service_account_file", check_google_credentials),
    ):
        try:
            issues.extend(checker(Path(inventory[field])))
        except StagingInputError as error:
            issues.append(str(error))
    return StagingReadiness(True, tuple(issues))


def main(arguments=None):
    """
    Reports local readiness with exit zero for ready or two for invalid/not ready

    :param list arguments: Optional command-line arguments
    :return: Process exit status"""
    parser = StagingArgumentParser(prog="python -B -m tests.staging_preflight", description=__doc__)
    parser.add_argument("--resources", required=True, help="Absolute private staging-resources.json path")
    try:
        options = parser.parse_args(arguments)
        result = check_staging_inputs(options.resources)
    except StagingInputError as error:
        result = StagingReadiness(False, (str(error),))
    except Exception:
        result = StagingReadiness(False, ("Local inputs could not be checked. Review the private files and access permissions.",))
    if result.inventory_valid:
        print("PASS: Development resource inventory is valid.")
    for issue in result.issues:
        print(f"NOT READY: {issue}")
    if result.ready:
        print("PASS: Local credential fields and Google key structure are present.")
    print("No service connection, bot startup, database change or export was performed.")
    print("This check does not verify credential authenticity, Sheet access or an isolated staging runtime.")
    return 0 if result.ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
