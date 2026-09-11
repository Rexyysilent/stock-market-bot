"""
MarketBot Daily Brief Dashboard Server
Serves the local dashboard and JSON/TXT API endpoints.

Routes:
  /              → Dashboard (index.html)
  /dashboard/*   → Static files (CSS, JS)
  /api/brief     → daily_brief.json
  /api/txt       → daily_brief.txt (raw text)
  /raw           → Plain text (legacy compat)
"""
import http.server
import socket
import sys
import os
import io
import json
import mimetypes
import ntpath
import socketserver
import threading
import time
from pathlib import Path
from urllib.parse import unquote, urlsplit

PORT = 8080
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DUMP_FILE_TXT = os.path.join(BASE_DIR, "daily_brief.txt")
DUMP_FILE_JSON = os.path.join(BASE_DIR, "daily_brief.json")
DASHBOARD_DIR = os.path.join(BASE_DIR, "dashboard")


def _request_path(request_target):
    """Return one decoded URL path, excluding query and fragment data."""
    return unquote(urlsplit(request_target).path)


def resolve_dashboard_path(request_target, dashboard_root=None):
    """Resolve a dashboard asset only when it remains beneath the asset root."""
    try:
        request_path = _request_path(request_target)
    except (TypeError, ValueError, UnicodeError):
        return None
    prefix = "/dashboard/"
    if not request_path.startswith(prefix):
        return None

    relative = request_path[len(prefix):]
    if (
        not relative
        or "\x00" in relative
        or "\\" in relative
        or os.path.isabs(relative)
        or ntpath.splitdrive(relative)[0]
    ):
        return None

    root = Path(dashboard_root or DASHBOARD_DIR).resolve()
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def get_lan_ip():
    """Get the LAN IP address of this machine."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "localhost"


class DashboardHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            request_path = _request_path(self.path)
        except (TypeError, ValueError, UnicodeError):
            self.send_error(400)
            return

        if request_path == "/" or request_path == "/index.html":
            self.serve_file(os.path.join(DASHBOARD_DIR, "index.html"), "text/html")

        elif request_path.startswith("/dashboard/"):
            # Static files (CSS, JS)
            file_path = resolve_dashboard_path(self.path)
            if file_path is not None and file_path.is_file():
                content_type, _ = mimetypes.guess_type(file_path)
                self.serve_file(file_path, content_type or "application/octet-stream")
            else:
                self.send_error(404)

        elif request_path == "/api/brief":
            self.serve_file(DUMP_FILE_JSON, "application/json")

        elif request_path == "/api/txt" or request_path == "/raw":
            self.serve_file(DUMP_FILE_TXT, "text/plain")

        else:
            self.send_error(404)

    def serve_file(self, filepath, content_type):
        """Serve a file with proper headers."""
        try:
            with open(filepath, 'rb') as f:
                content = f.read()

            self.send_response(200)
            self.send_header('Content-Type', f'{content_type}; charset=utf-8')
            self.send_header('Content-Length', str(len(content)))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header(
                'Content-Security-Policy',
                "default-src 'self'; script-src 'self'; "
                "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                "font-src https://fonts.gstatic.com; img-src 'self' data:; "
                "connect-src 'self'; object-src 'none'; base-uri 'none'; "
                "frame-ancestors 'none'; form-action 'none'"
            )
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            self.wfile.write(content)

        except FileNotFoundError:
            self.send_response(404)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            msg = f"File not found: {os.path.basename(filepath)}. Run export_for_gemini.py first."
            self.wfile.write(msg.encode('utf-8'))

    def log_message(self, format, *args):
        """Suppress noisy access logs."""
        pass


class BoundedDashboardServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """HTTP server with bounded, expiring connections.

    Both limits are applied before BaseHTTPRequestHandler starts parsing headers,
    so incomplete or slowly-dripped requests cannot monopolize the listener.
    """

    daemon_threads = True
    max_workers = 8
    socket_timeout = 5.0
    request_deadline = 10.0

    def __init__(self, *args, **kwargs):
        # HTTPServer.__init__ invokes server_close() when bind/activation fails,
        # so lifecycle state must exist before the base constructor runs.
        self._worker_slots = threading.BoundedSemaphore(self.max_workers)
        self._state_lock = threading.Lock()
        self._active_requests = 0
        self._active_timers = set()
        self._active_sockets = set()
        self._worker_threads = set()
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

        # Socket shutdown makes handlers return promptly; wait only a bounded
        # total interval so cleanup cannot itself hang application shutdown.
        deadline = time.monotonic() + self.socket_timeout
        current = threading.current_thread()
        for thread in threads:
            # A concurrent admission failure may leave an unstarted Thread in
            # the snapshot briefly; joining one raises RuntimeError.
            if thread is current or not thread.is_alive():
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            thread.join(remaining)


def main():
    # Keep importing this module safe for test runners with their own argv/stdout.
    if hasattr(sys.stdout, 'buffer'):
        sys.stdout = io.TextIOWrapper(
            sys.stdout.buffer, encoding='utf-8', errors='replace'
        )
    port = int(sys.argv[1]) if len(sys.argv) > 1 else PORT
    ip = get_lan_ip()

    print("=" * 56)
    print("  📡  MARKETBOT DASHBOARD SERVER")
    print("=" * 56)
    print(f"\n  📱 Phone:     http://{ip}:{port}")
    print(f"  💻 PC:        http://localhost:{port}")
    print(f"  📊 API JSON:  http://{ip}:{port}/api/brief")
    print(f"  📄 API TXT:   http://{ip}:{port}/api/txt")
    print(f"  📄 Raw text:  http://{ip}:{port}/raw")
    print(f"\n  Dashboard:    {DASHBOARD_DIR}")
    print(f"  Data:         {DUMP_FILE_JSON}")
    print(f"\n  Press Ctrl+C to stop\n")

    server = BoundedDashboardServer(("0.0.0.0", port), DashboardHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
