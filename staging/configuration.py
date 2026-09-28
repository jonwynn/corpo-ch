"""Validates the private configuration for one local Linux web instance."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat
import sys


class StagingConfigurationError(ValueError):
    """Reports a configuration problem without including private values."""


@dataclass(frozen=True, repr=False)
class WebConfiguration:
    """Holds validated local configuration without a printable secret representation."""

    runtime_root: Path
    database_name: str
    database_user: str
    database_password: str
    database_port: int
    browser_port: int
    bot_id: str
    bot_secret: str
    secret_key: str
    salt_key: str


def validate_configuration(values, runtime_root, expected_bot_id):
    """
    Validates the complete configuration and independent application identifier

    :param dict values: Decoded private JSON object
    :param Path runtime_root: Private directory containing the configuration
    :param str expected_bot_id: Independently supplied DEV application identifier
    :return: Validated configuration"""
    fields = {
        "schema_version", "database_name", "database_user", "database_password",
        "database_port", "browser_port", "bot_id", "bot_secret", "secret_key", "salt_key",
    }
    if not isinstance(values, dict) or set(values) != fields:
        raise StagingConfigurationError("The private web configuration has missing or unsupported fields.")
    if type(values["schema_version"]) is not int or values["schema_version"] != 1:
        raise StagingConfigurationError("The private web configuration version is unsupported.")
    for name, pattern in (
        ("database_name", r"corpo_staging_[a-z0-9_]{1,32}"),
        ("database_user", r"corpo_stage_[a-z0-9_]{1,16}"),
    ):
        if not isinstance(values[name], str) or re.fullmatch(pattern, values[name]) is None:
            raise StagingConfigurationError("The database and account must use dedicated staging names.")
    if any(
        type(values[name]) is not int or values[name] != expected
        for name, expected in (("database_port", 3308), ("browser_port", 8766))
    ):
        raise StagingConfigurationError("The web instance requires the dedicated local staging ports.")
    for identifier in (values["bot_id"], expected_bot_id):
        if (
            not isinstance(identifier, str)
            or re.fullmatch(r"[1-9][0-9]{16,18}", identifier) is None
            or int(identifier) >= 2 ** 63
        ):
            raise StagingConfigurationError("A valid DEV application identifier is required.")
    if values["bot_id"] != expected_bot_id:
        raise StagingConfigurationError("The configured application does not match the expected DEV application.")
    for name, minimum in (("database_password", 16), ("bot_secret", 1), ("secret_key", 50), ("salt_key", 32)):
        value = values[name]
        if not isinstance(value, str) or not minimum <= len(value) <= 1024 or value.strip() != value:
            raise StagingConfigurationError("A required private credential or generated key is invalid.")
    secrets = [values[name] for name in ("database_password", "bot_secret", "secret_key", "salt_key")]
    if len(set(secrets)) != len(secrets):
        raise StagingConfigurationError("The database, OAuth, session and encryption credentials must be separate.")
    return WebConfiguration(
        runtime_root=runtime_root,
        **{name: value for name, value in values.items() if name != "schema_version"},
    )


def validate_private_path(path, directory=False):
    """
    Requires an owned private Unix path with no symbolic or file hard links

    :param Path path: Absolute path to inspect
    :param bool directory: Whether an owned private directory is required
    :return: Validated filesystem metadata"""
    if sys.platform != "linux" or not path.is_absolute() or ".." in path.parts:
        raise StagingConfigurationError("Use an absolute private configuration path in the Linux runtime.")
    repository = Path(__file__).resolve().parents[1]
    if path == repository or repository in path.parents:
        raise StagingConfigurationError("Private staging files must stay outside the repository.")
    try:
        for component in reversed((path, *path.parents)):
            if stat.S_ISLNK(component.lstat().st_mode):
                raise StagingConfigurationError("Private staging paths cannot contain symbolic links.")
        metadata = path.stat()
    except OSError:
        raise StagingConfigurationError("A required private staging path is unavailable.") from None
    expected_mode = 0o700 if directory else 0o600
    correct_kind = stat.S_ISDIR(metadata.st_mode) if directory else stat.S_ISREG(metadata.st_mode)
    if (
        not correct_kind or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != expected_mode
        or (not directory and metadata.st_nlink != 1)
    ):
        raise StagingConfigurationError("Private staging ownership or permissions are incorrect.")
    return metadata


def load_configuration(filename, expected_bot_id):
    """
    Reads one bounded private configuration without following links

    :param str filename: Explicit absolute private JSON path
    :param str expected_bot_id: Independently supplied DEV application identifier
    :return: Validated configuration"""
    path = Path(filename)
    validate_private_path(path.parent, directory=True)
    metadata = validate_private_path(path)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if (
                opened.st_ino != metadata.st_ino or opened.st_dev != metadata.st_dev
                or opened.st_uid != os.getuid() or stat.S_IMODE(opened.st_mode) != 0o600
                or opened.st_nlink != 1 or not stat.S_ISREG(opened.st_mode)
            ):
                raise StagingConfigurationError("The private configuration changed while it was being opened.")
            content = stream.read(65537)
        if len(content) > 65536:
            raise StagingConfigurationError("The private configuration is too large.")
        values = json.loads(content.decode("utf-8"), object_pairs_hook=read_unique_fields)
    except (OSError, ValueError, UnicodeError):
        raise StagingConfigurationError("The private web configuration could not be read safely.") from None
    return validate_configuration(values, path.parent, expected_bot_id)


def read_unique_fields(pairs):
    """
    Rejects ambiguous repeated JSON fields

    :param list pairs: Decoded object key-value pairs
    :return: Object with unique fields"""
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("Duplicate configuration field.")
        result[name] = value
    return result


def prepare_private_directories(configuration):
    """
    Creates only private runtime storage inside the configuration directory

    :param WebConfiguration configuration: Validated private configuration"""
    for name in ("media", "static", "tmp"):
        directory = configuration.runtime_root / name
        directory.mkdir(mode=0o700, exist_ok=True)
        validate_private_path(directory, directory=True)
