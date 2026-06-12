"""
MarketBot Daily Brief Dashboard Server
Serves the premium mobile dashboard + JSON/TXT API endpoints.

Routes:
  /              → Dashboard (index.html)
  /dashboard/*   → Static files (CSS, JS)
  /api/brief     → gemini_daily_brief.json
  /api/txt       → gemini_daily_brief.txt (raw text)
  /raw           → Plain text (legacy compat)
"""
import http.server
import socket
import sys
import os
import io
import json
import mimetypes

# Fix Windows console encoding
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DUMP_FILE_TXT = os.path.join(BASE_DIR, "gemini_daily_brief.txt")
DUMP_FILE_JSON = os.path.join(BASE_DIR, "gemini_daily_brief.json")
DASHBOARD_DIR = os.path.join(BASE_DIR, "dashboard")


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
        if self.path == "/" or self.path == "/index.html":
            self.serve_file(os.path.join(DASHBOARD_DIR, "index.html"), "text/html")

        elif self.path.startswith("/dashboard/"):
            # Static files (CSS, JS)
            rel_path = self.path[len("/dashboard/"):]
            file_path = os.path.join(DASHBOARD_DIR, rel_path)
            if os.path.isfile(file_path):
                content_type, _ = mimetypes.guess_type(file_path)
                self.serve_file(file_path, content_type or "application/octet-stream")
            else:
                self.send_error(404)

        elif self.path == "/api/brief":
            self.serve_file(DUMP_FILE_JSON, "application/json")

        elif self.path == "/api/txt" or self.path == "/raw":
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
