"""Read-only local brief viewer. LAN exposure requires an explicit --host.

This is not an authenticated public web service. Use --demo to view synthetic
fixtures without installing collection dependencies or contacting providers.
"""
from __future__ import annotations

import argparse
import errno
import http.server
import ipaddress
import mimetypes
import ntpath
import os
import socket
import socketserver
import stat
import threading
import time
from pathlib import Path
from urllib.parse import unquote, urlsplit

BASE_DIR = Path(__file__).resolve().parent
DASHBOARD_DIR = BASE_DIR / "dashboard"
DUMP_FILE_TXT = BASE_DIR / "daily_brief.txt"
DUMP_FILE_JSON = BASE_DIR / "daily_brief.json"
MAX_FILE_BYTES = 32 * 1024 * 1024


def _request_path(request_target):
    parsed = urlsplit(request_target)
    if parsed.scheme or parsed.netloc:
        raise ValueError("absolute request targets are not supported")
    return unquote(parsed.path)


def resolve_dashboard_path(request_target, dashboard_root=None):
    """Keep assets beneath dashboard/, including after resolving symlinks."""
    try:
        request_path = _request_path(request_target)
        if not request_path.startswith("/dashboard/"):
            return None
        relative = request_path[len("/dashboard/"):]
        if (not relative or "\x00" in relative or "\\" in relative
                or os.path.isabs(relative) or ntpath.splitdrive(relative)[0]):
            return None
        root = Path(dashboard_root or DASHBOARD_DIR).resolve()
        candidate = (root / relative).resolve()
        candidate.relative_to(root)
        return candidate
    except (TypeError, ValueError, UnicodeError, OSError, RuntimeError):
        return None


class UnsafeLocalFile(Exception):
    """A brief path is a link or not a regular file."""


_REPARSE_POINT = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT
# Microsoft's "name surrogate" bit marks reparse tags that redirect to another
# path (symlinks, junctions, WSL/LX symlinks). Cloud placeholders (OneDrive),
# dedup and similar tags lack it and are ordinary files for our purposes.
_NAME_SURROGATE = 0x20000000


def _is_link_entry(info):
    return bool(stat.S_ISLNK(info.st_mode)
                or (getattr(info, "st_file_attributes", 0) & _REPARSE_POINT
                    and getattr(info, "st_reparse_tag", 0) & _NAME_SURROGATE))


def open_brief_file(directory, name):
    """Open a fixed brief filename inside a trusted directory, refusing links.

    The name is opened relative to the directory without following a link at
    open time, and the opened object must be a regular file with a single
    link (a hard link would otherwise expose a file from elsewhere). On POSIX
    this is dir_fd + O_NOFOLLOW (+ O_NONBLOCK so a FIFO cannot stall the
    worker). Where those are unavailable (Windows) the entry is lstat'ed first
    and the opened handle must be that same file, so a swap between the check
    and the open is refused rather than followed.
    """
    name = os.fspath(name)
    if (not name or name in (".", "..") or "/" in name or "\\" in name
            or "\x00" in name or ntpath.splitdrive(name)[0]):
        raise UnsafeLocalFile("brief name must be a plain filename")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if nofollow and os.open in os.supports_dir_fd:
        dir_fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            try:
                fd = os.open(name, flags | nofollow, dir_fd=dir_fd)
            except OSError as exc:
                if exc.errno in (errno.ELOOP, getattr(errno, "EMLINK", None), getattr(errno, "EFTYPE", None)):
                    raise UnsafeLocalFile("brief is a link") from exc
                raise
        finally:
            os.close(dir_fd)
        expected = None
    else:
        path = os.path.join(directory, name)
        expected = os.lstat(path)
        if _is_link_entry(expected) or not stat.S_ISREG(expected.st_mode):
            raise UnsafeLocalFile("brief is a link or not a regular file")
        fd = os.open(path, flags)
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode):
            raise UnsafeLocalFile("brief is not a regular file")
        if opened.st_nlink > 1:
            raise UnsafeLocalFile("brief has other hard links")
        if expected is not None and (opened.st_dev, opened.st_ino) != (expected.st_dev, expected.st_ino):
            raise UnsafeLocalFile("brief changed while it was being opened")
        return os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise


def _authority(value):
    try:
        parsed = urlsplit("//" + value)
        if (parsed.username or parsed.password or parsed.path
                or parsed.query or parsed.fragment or not parsed.hostname):
            return None
        return parsed.hostname.lower(), parsed.port or 80
    except (TypeError, ValueError):
        return None


class DashboardHandler(http.server.BaseHTTPRequestHandler):
    server_version = "MarketBot"
    sys_version = ""

    def end_headers(self):
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _trusted_request(self):
        hosts = self.headers.get_all("Host", [])
        authority = _authority(hosts[0]) if len(hosts) == 1 else None
        if (authority is None or authority[0] not in self.server.allowed_hosts
                or authority[1] != self.server.server_port):
            self.send_error(403, "Unrecognized local Host")
            return False
        origins = self.headers.get_all("Origin", [])
        if len(origins) > 1:
            self.send_error(403, "Multiple origins are not allowed")
            return False
        if origins:
            try:
                parsed = urlsplit(origins[0])
                valid = (parsed.scheme == "http" and not parsed.path
                         and not parsed.query and not parsed.fragment
                         and _authority(parsed.netloc) == authority)
            except ValueError:
                valid = False
            if not valid:
                self.send_error(403, "Cross-origin access is disabled")
                return False
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            self.send_error(403, "Cross-site access is disabled")
            return False
        return True

    def do_GET(self):
        if not self._trusted_request():
            return
        try:
            path = _request_path(self.path)
        except (TypeError, ValueError, UnicodeError):
            self.send_error(400)
            return
        if path in ("/", "/index.html"):
            self.serve_file(Path(DASHBOARD_DIR) / "index.html", "text/html")
        elif path.startswith("/dashboard/"):
            asset = resolve_dashboard_path(self.path)
            if asset is None or not asset.is_file():
                self.send_error(404)
            else:
                self.serve_file(asset, mimetypes.guess_type(asset)[0] or "application/octet-stream")
        elif path == "/api/brief":
            brief = Path(self.server.brief_path)
            self.serve_brief(brief.parent, brief.name, "application/json")
        elif path in ("/api/txt", "/raw"):
            if self.server.demo:
                self.serve_demo_text()
            else:
                self.serve_brief(self.server.data_dir, "daily_brief.txt", "text/plain")
        else:
            self.send_error(404)

    def _send_bytes(self, content, content_type):
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def serve_file(self, filepath, content_type, *, brief=False, prefix=b"",
                   missing="No local brief. Generate one, select --data-dir, or use --demo."):
        """Send one capped local file. ``brief=True`` uses open_brief_file:
        the fixed name inside its trusted directory, links refused."""
        try:
            path = Path(filepath)
            handle = open_brief_file(path.parent, path.name) if brief else path.open("rb")
            with handle:
                content = handle.read(MAX_FILE_BYTES - len(prefix) + 1)
            if len(prefix) + len(content) > MAX_FILE_BYTES:
                self.send_error(413, "Local file exceeds viewer size limit")
                return
            self._send_bytes(prefix + content, content_type)
        except FileNotFoundError:
            self.send_error(404, missing)
        except UnsafeLocalFile:
            self.send_error(403, "Local brief must be a regular file, not a link")
        except OSError:
            self.send_error(500, "Local file could not be read")

    def serve_brief(self, directory, name, content_type):
        self.serve_file(Path(directory) / name, content_type, brief=True)

    def serve_demo_text(self):
        self.serve_file(self.server.brief_path, "text/plain", brief=True,
                        prefix=b"SYNTHETIC DEMO. Not live market data.\n",
                        missing="Bundled synthetic brief is unavailable")

    def log_message(self, format, *args):
        pass


class BoundedDashboardServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """Threaded HTTP server with bounded, expiring connections.

    Admission and the hard deadline are established before request headers are
    parsed, so idle and slowly trickled requests cannot occupy the viewer
    indefinitely.
    """

    daemon_threads = True
    max_workers = 8
    socket_timeout = 5.0
    request_deadline = 10.0

    def __init__(self, *args, **kwargs):
        # HTTPServer.__init__ calls server_close() when bind/activation fails.
        self._worker_slots = threading.BoundedSemaphore(self.max_workers)
        self._state_lock = threading.Lock()
        self._active_requests = 0
        self._active_timers = set()
        self._active_sockets = set()
        self._worker_threads = set()
        # Defaults preserve direct construction for local tests and embedders;
        # create_server replaces them with its validated configuration.
        self.allowed_hosts = frozenset({"localhost", "127.0.0.1"})
        self.data_dir = Path(DUMP_FILE_JSON).resolve().parent
        self.demo = False
        self.brief_path = Path(DUMP_FILE_JSON)
        super().__init__(*args, **kwargs)

    @property
    def active_requests(self):
        with self._state_lock:
            return self._active_requests

    @property
    def active_timers(self):
        with self._state_lock:
            return len(self._active_timers)

    @staticmethod
    def _expire_request(request):
        try:
            request.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def _make_worker_thread(self, request, client_address, timer):
        return threading.Thread(
            target=self._run_bounded_request,
            args=(request, client_address, timer),
            daemon=self.daemon_threads,
        )

    def process_request(self, request, client_address):
        if not self._worker_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return

        timer = None
        thread = None
        registered = False
        try:
            request.settimeout(self.socket_timeout)
            timer = threading.Timer(
                self.request_deadline, self._expire_request, args=(request,)
            )
            timer.daemon = True
            thread = self._make_worker_thread(request, client_address, timer)
            with self._state_lock:
                self._active_requests += 1
                self._active_timers.add(timer)
                self._active_sockets.add(request)
                self._worker_threads.add(thread)
            registered = True
            timer.start()
            thread.start()
        except BaseException:
            if timer is not None:
                timer.cancel()
            if registered:
                with self._state_lock:
                    self._active_requests -= 1
                    self._active_timers.discard(timer)
                    self._active_sockets.discard(request)
                    self._worker_threads.discard(thread)
            self._worker_slots.release()
            self.shutdown_request(request)
            raise

    def _run_bounded_request(self, request, client_address, timer):
        try:
            self.finish_request(request, client_address)
        except Exception:
            self.handle_error(request, client_address)
        finally:
            timer.cancel()
            self.shutdown_request(request)
            with self._state_lock:
                self._active_requests -= 1
                self._active_timers.discard(timer)
                self._active_sockets.discard(request)
                self._worker_threads.discard(threading.current_thread())
            self._worker_slots.release()

    def server_close(self):
        with self._state_lock:
            timers = tuple(self._active_timers)
            requests = tuple(self._active_sockets)
            threads = tuple(self._worker_threads)
        for timer in timers:
            timer.cancel()
        for request in requests:
            self._expire_request(request)
        super().server_close()

        deadline = time.monotonic() + self.socket_timeout
        current = threading.current_thread()
        for thread in threads:
            if thread is current or not thread.is_alive():
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            thread.join(remaining)


def create_server(host="127.0.0.1", port=8080, data_dir=None, demo=False, allow_hosts=()):
    """Build a single-user server. Port 0 is useful for loopback-only tests."""
    # IPv4 literals keep binding deterministic and avoid implicit DNS lookups.
    host = str(ipaddress.IPv4Address(host))
    allowed = {"localhost", "127.0.0.1"}
    if host != "0.0.0.0":
        allowed.add(host)
    for candidate in allow_hosts:
        parsed = _authority(candidate)
        if not parsed or ":" in candidate or "/" in candidate:
            raise ValueError("--allow-host expects a hostname or IPv4 address, without a port")
        allowed.add(parsed[0])
    root = Path(data_dir or BASE_DIR).resolve()
    server = BoundedDashboardServer((host, port), DashboardHandler)
    server.allowed_hosts = frozenset(allowed)
    server.data_dir = root
    server.demo = demo
    server.brief_path = (BASE_DIR / "fixtures/exports/daily_brief.demo.json"
                         if demo else root / "daily_brief.json")
    return server


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("port", nargs="?", type=int, default=8080)
    parser.add_argument("--host", default="127.0.0.1", help="IPv4 bind address; default is loopback only")
    parser.add_argument("--allow-host", action="append", default=[], help="Additional Host name accepted for explicit trusted-LAN access")
    parser.add_argument("--data-dir", type=Path, default=BASE_DIR)
    parser.add_argument("--demo", action="store_true", help="Serve bundled synthetic data, never personal exports")
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("port must be between 0 and 65535")
    try:
        server = create_server(args.host, args.port, args.data_dir, args.demo, args.allow_host)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    display_host = "127.0.0.1" if args.host == "0.0.0.0" else args.host
    print(f"MarketBot viewer: http://{display_host}:{server.server_port}", flush=True)
    if args.host != "127.0.0.1":
        print("Trusted LAN only: no authentication or TLS. Do not expose to the internet.", flush=True)
    if args.demo:
        print("SYNTHETIC DEMO: not live prices or investment evidence.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
