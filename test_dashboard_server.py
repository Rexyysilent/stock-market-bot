"""Loopback HTTP checks. These tests never contact external hosts."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from serve_dump import BoundedDashboardServer, create_server, resolve_dashboard_path


class DashboardServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "daily_brief.json").write_text('{"synthetic":true}', encoding="utf-8")
        self.server = create_server(port=0, data_dir=self.root)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, path, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
        try:
            conn.request("GET", path, headers=headers or {})
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            conn.close()

    def test_loopback_default_and_local_json(self):
        self.assertIsInstance(self.server, BoundedDashboardServer)
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        status, headers, body = self.request("/api/brief")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"synthetic": True})
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])

    def test_unrecognized_host_is_rejected(self):
        self.assertEqual(self.request("/api/brief", {"Host": "untrusted.invalid"})[0], 403)

    def test_cross_origin_is_rejected(self):
        self.assertEqual(self.request("/api/brief", {"Origin": "https://untrusted.invalid"})[0], 403)
        self.assertEqual(self.request("/api/brief", {"Sec-Fetch-Site": "cross-site"})[0], 403)

    def test_same_origin_is_allowed(self):
        origin = f"http://127.0.0.1:{self.server.server_port}"
        self.assertEqual(self.request("/api/brief", {"Origin": origin})[0], 200)

    def test_missing_text_does_not_replace_json(self):
        status, headers, _ = self.request("/api/txt")
        self.assertEqual(status, 404)
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(self.request("/api/brief")[0], 200)

    def test_demo_text_is_explicitly_synthetic(self):
        demo_server = create_server(port=0, demo=True)
        demo_thread = threading.Thread(target=demo_server.serve_forever, daemon=True)
        demo_thread.start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", demo_server.server_port, timeout=2)
            conn.request("GET", "/api/txt")
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            self.assertTrue(response.read().startswith(b"SYNTHETIC DEMO."))
            conn.close()
        finally:
            demo_server.shutdown()
            demo_server.server_close()
            demo_thread.join(timeout=2)

    def test_paths_and_symlinks_stay_inside_assets(self):
        assets = self.root / "assets"
        assets.mkdir()
        target = self.root / "private.txt"
        target.write_text("private", encoding="utf-8")
        for path in ("/dashboard/../private.txt", "/dashboard/%2e%2e/private.txt", "https://untrusted.invalid/dashboard/app.js"):
            self.assertIsNone(resolve_dashboard_path(path, assets))
        try:
            (assets / "link.txt").symlink_to(target)
        except (OSError, NotImplementedError):
            return
        self.assertIsNone(resolve_dashboard_path("/dashboard/link.txt", assets))


if __name__ == "__main__":
    unittest.main()
