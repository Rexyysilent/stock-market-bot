"""Loopback-only regression tests for dashboard connection exhaustion."""
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import serve_dump


class TestServer(serve_dump.BoundedDashboardServer):
    max_workers = 2
    socket_timeout = 0.25
    request_deadline = 0.65

    def handle_error(self, request, client_address):
        pass


class DashboardConnectionSecurityTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        dashboard = root / "dashboard"
        dashboard.mkdir()
        (dashboard / "index.html").write_text("dashboard", encoding="utf-8")
        (root / "daily_brief.json").write_text('{"ok": true}', encoding="utf-8")
        (root / "daily_brief.txt").write_text("brief", encoding="utf-8")
        self.old_paths = (
            serve_dump.DASHBOARD_DIR,
            serve_dump.DUMP_FILE_JSON,
            serve_dump.DUMP_FILE_TXT,
        )
        serve_dump.DASHBOARD_DIR = str(dashboard)
        serve_dump.DUMP_FILE_JSON = str(root / "daily_brief.json")
        serve_dump.DUMP_FILE_TXT = str(root / "daily_brief.txt")

        self.server = TestServer(("127.0.0.1", 0), serve_dump.DashboardHandler)
        self.address = self.server.server_address
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.sockets = []

    def tearDown(self):
        for sock in self.sockets:
            sock.close()
        if self.thread.is_alive():
            self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        (
            serve_dump.DASHBOARD_DIR,
            serve_dump.DUMP_FILE_JSON,
            serve_dump.DUMP_FILE_TXT,
        ) = self.old_paths
        self.tempdir.cleanup()

    def connect(self):
        sock = socket.create_connection(self.address, timeout=1)
        sock.settimeout(1.5)
        self.sockets.append(sock)
        return sock

    def request(self, path="/"):
        sock = self.connect()
        sock.sendall(
            f"GET {path} HTTP/1.0\r\nHost: localhost:{self.address[1]}\r\n\r\n".encode()
        )
        data = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                return data
            data += chunk

    def wait_for_idle(self):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if self.server.active_requests == 0 and self.server.active_timers == 0:
                return
            time.sleep(0.02)
        self.fail("server did not release worker slots and deadline timers")

    def test_incomplete_client_does_not_block_ordinary_request(self):
        incomplete = self.connect()
        incomplete.sendall(
            f"GET / HTTP/1.1\r\nHost: localhost:{self.address[1]}\r\n".encode()
        )
        self.assertIn(b"200 OK", self.request("/api/brief"))

    def test_idle_and_slow_trickle_connections_expire(self):
        idle = self.connect()
        self.assertEqual(idle.recv(1), b"")
        self.wait_for_idle()

        slow = self.connect()
        started = time.monotonic()
        while time.monotonic() - started < 1.2:
            try:
                slow.sendall(b"G")
                time.sleep(0.12)  # Faster than the idle timeout; deadline must win.
            except OSError:
                break
        try:
            self.assertEqual(slow.recv(1), b"")
        except OSError:
            pass
        self.assertLess(time.monotonic() - started, 1.2)
        self.wait_for_idle()

    def test_worker_admission_is_bounded_then_recovers(self):
        first = self.connect()
        second = self.connect()
        first.sendall(b"G")
        second.sendall(b"G")
        deadline = time.monotonic() + 1
        while self.server.active_requests < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.server.active_requests, 2)

        overflow = self.connect()
        overflow.sendall(b"GET / HTTP/1.0\r\n\r\n")
        try:
            self.assertEqual(overflow.recv(1), b"")
        except (ConnectionResetError, ConnectionAbortedError):
            pass

        first.close()
        second.close()
        self.wait_for_idle()
        self.assertIn(b"200 OK", self.request("/api/txt"))

    def test_parse_and_write_errors_do_not_leak_slots_or_timers(self):
        malformed = self.connect()
        malformed.sendall(b"NOT HTTP\r\n\r\n")
        malformed.close()
        aborted = self.connect()
        aborted.sendall(b"GET / HTTP/1.0\r\n\r\n")
        aborted.close()
        self.wait_for_idle()
        self.assertEqual(self.server.active_requests, 0)
        self.assertEqual(self.server.active_timers, 0)
        self.assertIn(b"200 OK", self.request())

    def test_worker_setup_and_start_failures_release_admission(self):
        original = self.server._make_worker_thread

        def fail_construction(*args):
            raise RuntimeError("injected thread construction failure")

        self.server._make_worker_thread = fail_construction
        failed = self.connect()
        failed.sendall(b"GET / HTTP/1.0\r\n\r\n")
        try:
            failed.recv(1)
        except OSError:
            pass
        self.wait_for_idle()

        class FailingStartThread:
            def start(self):
                raise RuntimeError("injected thread start failure")

        self.server._make_worker_thread = lambda *args: FailingStartThread()
        failed = self.connect()
        failed.sendall(b"GET / HTTP/1.0\r\n\r\n")
        try:
            failed.recv(1)
        except OSError:
            pass
        self.wait_for_idle()

        self.server._make_worker_thread = original
        self.assertIn(b"200 OK", self.request())

    def test_server_close_cancels_active_connection_and_timer(self):
        incomplete = self.connect()
        incomplete.sendall(b"G")
        deadline = time.monotonic() + 1
        while self.server.active_requests != 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.server.active_requests, 1)

        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.wait_for_idle()
        self.assertEqual(self.server.active_timers, 0)
        try:
            self.assertEqual(incomplete.recv(1), b"")
        except OSError:
            pass

    def test_failed_bind_preserves_socket_error(self):
        with mock.patch.object(
            serve_dump.http.server.HTTPServer,
            "server_bind",
            side_effect=OSError("injected bind failure"),
        ):
            with self.assertRaises(OSError):
                TestServer(("127.0.0.1", 0), serve_dump.DashboardHandler)


if __name__ == "__main__":
    unittest.main()
