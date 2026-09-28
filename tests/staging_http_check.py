"""Checks local HTTP concurrency and cleanup using one owned loopback listener."""

from collections import Counter
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import io
import queue
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from staging.runtime import create_web_server
from tests.viewer_test_bootstrap import BlockedTestOperation, ViewerTestEnvironment


class StagingHttpEnvironment(ViewerTestEnvironment):
    """Permits only the disposable HTTP listener and its selected endpoint."""

    def __init__(self, directory):
        super().__init__(directory)
        self.server_socket = None
        self.endpoint = None

    def select_server(self, server):
        """
        Allows clients to reach the listener created within this guard

        :param object server: Owned local HTTP server"""
        if server.socket is not self.server_socket or server.server_address[0] != "127.0.0.1":
            raise BlockedTestOperation("The HTTP check must own its loopback listener.")
        self.endpoint = server.server_address

    def check_operation(self, event, arguments):
        """
        Keeps external I/O blocked except for the selected HTTP endpoint

        :param str event: Python audit event
        :param tuple arguments: Audit event arguments"""
        if self.active:
            if event == "socket.bind":
                connection, endpoint = arguments
                if self.server_socket is None and endpoint == ("127.0.0.1", 0):
                    self.server_socket = connection
                    return
                raise BlockedTestOperation("The HTTP check permits one ephemeral loopback listener.")
            if event == "socket.connect":
                if self.endpoint is not None and arguments[1] == self.endpoint:
                    return
                raise BlockedTestOperation("The HTTP check permits only its owned loopback endpoint.")
            if event == "sqlite3.connect":
                raise BlockedTestOperation("The HTTP check does not use a database.")
        super().check_operation(event, arguments)


def create_client(endpoint, sockets):
    """
    Connects a bounded client to the owned server without a DNS lookup

    :param tuple endpoint: Owned loopback address
    :param ExitStack sockets: Cleanup owner for client sockets
    :return: Connected client socket"""
    connection = sockets.enter_context(socket.socket(socket.AF_INET, socket.SOCK_STREAM))
    connection.settimeout(2)
    connection.connect(endpoint)
    return connection


def read_response(connection):
    """
    Reads a bounded HTTP response until the server closes the connection

    :param socket connection: Connected client socket
    :return: Complete response bytes"""
    chunks = []
    while chunk := connection.recv(4096):
        chunks.append(chunk)
        if sum(map(len, chunks)) > 16384:
            raise AssertionError("The HTTP check response exceeded its limit.")
    return b"".join(chunks)


def check_server(server, checks):
    """
    Exercises accepted idle sockets, incomplete headers and private failures

    :param object server: Actual staging HTTP server with a fixture application
    :param TestCase checks: Assertion interface"""
    from django.core.servers.basehttp import ThreadedWSGIServer
    from django.db import connections

    checks.assertIsInstance(server, ThreadedWSGIServer)
    checks.assertTrue(server.daemon_threads)
    checks.assertIsNone(server.connections_override)
    checks.assertEqual(server.RequestHandlerClass.timeout, 10)
    accepted = threading.Event()
    request_threads = []
    cleanup_threads = queue.Queue()
    original_get_request = server.get_request
    original_finish_request = server.finish_request
    original_close_all = connections.close_all

    def record_accept():
        request = original_get_request()
        accepted.set()
        return request

    def record_request(request, client_address):
        request_threads.append(threading.current_thread())
        original_finish_request(request, client_address)

    def record_cleanup():
        original_close_all()
        cleanup_threads.put(threading.current_thread())

    def application(environment, start_response):
        if environment["PATH_INFO"] == "/auth":
            raise RuntimeError("fixture-private-exception")
        body = b"Local HTTP regression passed"
        start_response("200 OK", [("Content-Type", "text/plain"), ("Content-Length", str(len(body)))])
        return [body]

    server.set_app(application)
    runner = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    with (
        patch.object(server, "get_request", side_effect=record_accept),
        patch.object(server, "finish_request", side_effect=record_request),
        patch.object(connections, "close_all", side_effect=record_cleanup),
    ):
        runner.start()
        try:
            with ExitStack() as sockets:
                idle = create_client(server.server_address, sockets)
                checks.assertTrue(accepted.wait(2), "The idle connection was not accepted.")
                started = time.monotonic()
                active = create_client(server.server_address, sockets)
                active.sendall(b"GET /home HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
                response = read_response(active)
                checks.assertTrue(response.startswith(b"HTTP/1.1 200 OK\r\n"))
                checks.assertTrue(response.endswith(b"Local HTTP regression passed"))
                checks.assertLess(time.monotonic() - started, 2)
                idle.settimeout(0.05)
                with checks.assertRaises(TimeoutError):
                    idle.recv(1)
                idle.close()

                with patch.object(server.RequestHandlerClass, "timeout", 0.2):
                    expired = create_client(server.server_address, sockets)
                    checks.assertEqual(expired.recv(1), b"")
                    partial = create_client(server.server_address, sockets)
                    partial.sendall(b"GET /auth?code=fixture-private-code HTTP/1.1\r\nHost: 127.0.0.1\r\nX-Incomplete: ")
                    checks.assertEqual(partial.recv(1), b"")

                failed = create_client(server.server_address, sockets)
                failed.sendall(b"GET /auth?code=fixture-private-code&state=fixture-private-state HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
                response = read_response(failed)
                checks.assertTrue(response.startswith(b"HTTP/1.1 500 Internal Server Error\r\n"))
                checks.assertNotIn(b"fixture-private", response)
                cleaned = [cleanup_threads.get(timeout=2) for request in range(5)]
                checks.assertEqual(Counter(cleaned), Counter(request_threads))
                checks.assertEqual(len(request_threads), 5)
                checks.assertNotIn(runner, cleaned)
                checks.assertNotIn(threading.current_thread(), cleaned)
        finally:
            shutdown = threading.Thread(target=server.shutdown, daemon=True)
            shutdown.start()
            shutdown.join(3)
            runner.join(3)
            checks.assertFalse(shutdown.is_alive(), "The HTTP server did not finish shutdown.")
            checks.assertFalse(runner.is_alive(), "The HTTP server thread did not stop.")


def run_check():
    """
    Runs the HTTP regression without application services or credentials

    :return: Process exit status"""
    checks = unittest.TestCase()
    with tempfile.TemporaryDirectory(prefix="corpo-staging-http-check-") as directory:
        with StagingHttpEnvironment(directory) as guard:
            sys.modules.pop("corpoch.settings", None)
            from django.conf import settings

            settings.configure(DATABASES={})
            # HTTPServer otherwise resolves its own display name during bind.
            # Keep name-service operations blocked throughout the HTTP check.
            with patch("socket.getfqdn", return_value="localhost"):
                server = create_web_server(0)
            with server:
                guard.select_server(server)
                with redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()) as errors:
                    check_server(server, checks)
                checks.assertEqual(output.getvalue(), "")
                checks.assertEqual(errors.getvalue(), "")
            checks.assertEqual(server.socket.fileno(), -1)
            checks.assertNotIn("corpoch", sys.modules)
            checks.assertNotIn("MySQLdb", sys.modules)
    print("PASS: Parallel HTTP requests, idle and partial-header timeouts, private logs and per-thread cleanup passed.")
    print("Only an owned ephemeral loopback listener was used. No database, service or private credential was accessed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_check())
