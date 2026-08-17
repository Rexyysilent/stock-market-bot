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
from pathlib import Path
from urllib.parse import unquote, urlsplit

# Fix Windows console encoding
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
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
        or os.path.splitdrive(relative)[0]
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


if __name__ == "__main__":
    ip = get_lan_ip()

    print("=" * 56)
    print("  📡  MARKETBOT DASHBOARD SERVER")
    print("=" * 56)
    print(f"\n  📱 Phone:     http://{ip}:{PORT}")
    print(f"  💻 PC:        http://localhost:{PORT}")
    print(f"  📊 API JSON:  http://{ip}:{PORT}/api/brief")
    print(f"  📄 API TXT:   http://{ip}:{PORT}/api/txt")
    print(f"  📄 Raw text:  http://{ip}:{PORT}/raw")
    print(f"\n  Dashboard:    {DASHBOARD_DIR}")
    print(f"  Data:         {DUMP_FILE_JSON}")
    print(f"\n  Press Ctrl+C to stop\n")

    server = http.server.HTTPServer(("0.0.0.0", PORT), DashboardHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
        server.server_close()
