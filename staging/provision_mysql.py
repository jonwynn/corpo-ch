"""Creates a private, stopped MySQL instance without reading application settings."""

import argparse
import importlib
import json
import os
from pathlib import Path
import re
import secrets
import socket
import stat
import subprocess
import sys
import time


class ProvisionError(RuntimeError):
    """Reports a fixed provisioning failure without credentials or driver output."""


def validate_destination(value, bot_id):
    """Requires a new private Linux directory outside the repository.

    :param str value: Explicit absolute destination
    :param str bot_id: Expected development application identifier
    :return: Validated destination path"""
    if sys.platform != "linux" or os.getuid() == 0:
        raise ProvisionError("Run provisioning as the normal Linux user, not root.")
    if re.fullmatch(r"[1-9][0-9]{16,18}", bot_id) is None or int(bot_id) >= 2 ** 63:
        raise ProvisionError("Provide the expected numeric development application ID.")
    root = Path(value)
    if not root.is_absolute() or any(character in str(root) for character in '\r\n"\\'):
        raise ProvisionError("Use an absolute Linux path without quotes, backslashes or line breaks.")
    if ".." in root.parts or len(os.fsencode(root / "bootstrap.sock")) > 100:
        raise ProvisionError("The private destination path is ambiguous or too long for a local socket.")
    repository = Path(__file__).resolve().parents[1]
    if root == repository or repository in root.parents:
        raise ProvisionError("MySQL data and credentials must remain outside the repository.")
    for path in (root, *root.parents):
        if path.is_symlink():
            raise ProvisionError("The private destination and its parents must not be symlinks.")
    if root.exists():
        raise ProvisionError("The destination already exists; provisioning never reuses or removes it.")
    try:
        parent_stat = root.parent.stat()
    except OSError as error:
        raise ProvisionError("Create the private parent directory before provisioning.") from error
    if not stat.S_ISDIR(parent_stat.st_mode) or parent_stat.st_uid != os.getuid() or parent_stat.st_mode & 0o022:
        raise ProvisionError("The parent must be owned by this Linux user and not writable by other users.")
    return root


def write_private_file(path, content):
    """Creates a new owner-only file without replacing any existing path.

    :param Path path: New file in the owned private directory
    :param str content: File contents, never written to terminal output"""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(content)


def build_credentials(bot_id):
    """Generates separate database identities and credentials.

    :param str bot_id: Expected development application identifier
    :return: Private connection information"""
    suffix = secrets.token_hex(6)
    return {
        "purpose": "corpo-ch-web-staging-v1",
        "expected_bot_id": bot_id,
        "host": "127.0.0.1",
        "port": 3308,
        "database": f"corpo_staging_{suffix}",
        "app_user": f"corpo_stage_{suffix}",
        "app_password": secrets.token_urlsafe(48),
        "validation_user": f"corpo_validate_{suffix}",
        "validation_password": secrets.token_urlsafe(48),
        "root_password": secrets.token_urlsafe(48),
    }


def configure_database(connection, credentials):
    """Creates one staging schema and grants each account only its database scope.

    :param object connection: Owned local bootstrap connection
    :param dict credentials: Generated private connection information"""
    database = credentials["database"]
    if re.fullmatch(r"corpo_staging_[0-9a-f]{12}", database) is None:
        raise ProvisionError("Invalid generated staging database name.")
    database_grant = database.replace("_", "\\_")
    with connection.cursor() as cursor:
        cursor.execute("ALTER USER 'root'@'localhost' IDENTIFIED BY %s", [credentials["root_password"]])
        cursor.execute(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        cursor.execute("CREATE USER %s@'127.0.0.1' IDENTIFIED BY %s", [credentials["app_user"], credentials["app_password"]])
        cursor.execute(f"GRANT ALL PRIVILEGES ON `{database_grant}`.* TO %s@'127.0.0.1'", [credentials["app_user"]])
        cursor.execute("CREATE USER %s@'127.0.0.1' IDENTIFIED BY %s", [credentials["validation_user"], credentials["validation_password"]])
        # mysqlclient formats bound parameters with %, so the database wildcard
        # must survive that formatting as one literal percent character.
        cursor.execute(r"GRANT ALL PRIVILEGES ON `corpo\_viewer\_validation\_%%`.* TO %s@'127.0.0.1'", [credentials["validation_user"]])
        cursor.execute(f"CREATE TABLE `{database}`.corpo_staging_runtime (id TINYINT PRIMARY KEY, purpose VARCHAR(64) NOT NULL, bot_id VARCHAR(20) NOT NULL)")
        cursor.execute(f"INSERT INTO `{database}`.corpo_staging_runtime (id, purpose, bot_id) VALUES (1, %s, %s)", [credentials["purpose"], credentials["expected_bot_id"]])


def connect_bootstrap(process, root):
    """Waits for the owned socket-only bootstrap server.

    :param object process: Child process created by this invocation
    :param Path root: Owned private directory
    :return: Authenticated local bootstrap connection"""
    driver = importlib.import_module("MySQLdb")
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise ProvisionError("The private MySQL bootstrap server exited before it was ready.")
        try:
            return driver.connect(
                host="localhost", unix_socket=str(root / "bootstrap.sock"),
                user="root", passwd="", charset="utf8mb4", autocommit=True,
                connect_timeout=3, read_timeout=5, write_timeout=5,
            )
        except driver.OperationalError:
            time.sleep(0.2)
    raise ProvisionError("The private MySQL bootstrap server did not become ready in time.")


def stop_owned_process(process):
    """Stops only the bootstrap process started by this invocation.

    :param object process: Owned child process, or None before startup"""
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)
        raise ProvisionError("The bootstrap server required forced shutdown; partial files were retained.")


def build_server_configuration(root):
    """Builds a local server configuration with no automatic startup.

    :param Path root: Validated private instance directory
    :return: MySQL option-file contents"""
    return "\n".join([
        "[mysqld]", f'datadir="{root / "data"}"',
        f'socket="{root / "server.sock"}"', f'pid-file="{root / "server.pid"}"',
        f'log-error="{root / "server-error.log"}"', f'tmpdir="{root / "tmp"}"',
        "bind-address=127.0.0.1", "port=3308", "mysqlx=0", "skip-name-resolve",
        "skip-log-bin", "partial-revokes=OFF", "local-infile=0", "secure-file-priv=NULL",
        "character-set-server=utf8mb4", "collation-server=utf8mb4_unicode_ci", "",
    ])


def provision_mysql(root_value, bot_id):
    """Initializes a fresh private instance and leaves its server stopped.

    :param str root_value: Explicit new private directory
    :param str bot_id: Expected development application identifier"""
    root = validate_destination(root_value, bot_id)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
        try:
            reservation.bind(("127.0.0.1", 3308))
        except OSError as error:
            raise ProvisionError("Local port 3308 is already occupied; no instance was created.") from error
        root.mkdir(mode=0o700)
        (root / "tmp").mkdir(mode=0o700)
        credentials = build_credentials(bot_id)
        pending = root / "credentials.pending.json"
        write_private_file(pending, json.dumps(credentials, indent=2) + "\n")
        write_private_file(root / "bootstrap-output.log", "")
        environment = {"PATH": "/usr/sbin:/usr/bin:/bin", "HOME": str(root), "LANG": "C.UTF-8"}
        common = [
            "/usr/sbin/mysqld", "--no-defaults", f"--datadir={root / 'data'}",
            f"--log-error={root / 'server-error.log'}", f"--tmpdir={root / 'tmp'}",
            "--mysqlx=0", "--skip-log-bin", "--skip-name-resolve", "--partial-revokes=OFF",
            "--local-infile=0", "--secure-file-priv=NULL",
        ]
        process = None
        connection = None
        with (root / "bootstrap-output.log").open("a", encoding="utf-8") as output:
            try:
                subprocess.run(common + ["--initialize-insecure"], check=True, timeout=120, stdout=output, stderr=subprocess.STDOUT, env=environment)
                process = subprocess.Popen(
                    common + ["--skip-networking", f"--socket={root / 'bootstrap.sock'}", f"--pid-file={root / 'bootstrap.pid'}"],
                    stdout=output, stderr=subprocess.STDOUT, env=environment,
                )
                connection = connect_bootstrap(process, root)
                configure_database(connection, credentials)
            finally:
                try:
                    if connection is not None:
                        connection.close()
                finally:
                    stop_owned_process(process)
        write_private_file(root / "mysql.conf", build_server_configuration(root))
        os.link(pending, root / "credentials.json")
        pending.unlink()


def main(arguments=None):
    """Runs explicit one-time provisioning without exposing private failures.

    :param list arguments: Optional command-line argument list
    :return: Process exit status"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--expected-bot-id", required=True)
    args = parser.parse_args(arguments)
    try:
        provision_mysql(args.root, args.expected_bot_id)
    except ProvisionError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("FAIL: Provisioning was interrupted; partial files were retained.", file=sys.stderr)
        return 130
    except Exception:
        print("FAIL: Private MySQL provisioning failed; partial files were retained. No credentials were displayed.", file=sys.stderr)
        return 1
    print("PASS: A fresh private MySQL instance was created and stopped. Credentials were saved privately; no application was started.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
