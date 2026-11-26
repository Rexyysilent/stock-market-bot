"""Loopback HTTP checks. These tests never contact external hosts."""
import http.client
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace
import tempfile
import threading
import unittest
from unittest import mock

import serve_dump
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

    def _outside_secret(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        secret = Path(outside.name) / "secret.txt"
        secret.write_text("outside-data-dir-secret", encoding="utf-8")
        return secret

    def test_brief_routes_refuse_symlinks_out_of_the_data_dir(self):
        secret = self._outside_secret()
        for route, name in (("/api/brief", "daily_brief.json"), ("/api/txt", "daily_brief.txt"),
                            ("/raw", "daily_brief.txt")):
            with self.subTest(route=route):
                brief = self.root / name
                if brief.exists() or brief.is_symlink():
                    brief.unlink()
                try:
                    brief.symlink_to(secret)
                except (OSError, NotImplementedError):
                    self.skipTest("symlinks unavailable on this platform/account")
                status, _headers, body = self.request(route)
                self.assertEqual(status, 403)
                self.assertNotIn(b"outside-data-dir-secret", body)
                # The lstat fallback (Windows) refuses the same link.
                with mock.patch.object(serve_dump.os, "supports_dir_fd", set()):
                    with self.assertRaises(serve_dump.UnsafeLocalFile):
                        serve_dump.open_brief_file(self.root, name)

    def test_brief_routes_refuse_non_regular_files(self):
        (self.root / "daily_brief.json").unlink()
        (self.root / "daily_brief.json").mkdir()
        (self.root / "daily_brief.txt").mkdir()
        for route in ("/api/brief", "/api/txt"):
            with self.subTest(route=route):
                self.assertEqual(self.request(route)[0], 403)

    def test_text_route_serves_a_regular_brief(self):
        (self.root / "daily_brief.txt").write_text("plain brief", encoding="utf-8")
        status, _headers, body = self.request("/api/txt")
        self.assertEqual((status, body), (200, b"plain brief"))

    def test_check_then_open_fallback_refuses_a_swapped_file(self):
        # The lstat + same-file path used where dir_fd/O_NOFOLLOW are missing
        # (Windows): a file swapped in between the check and the open is refused.
        secret = self._outside_secret()
        real_open = serve_dump.os.open

        def swapped_open(path, flags, *args, **kwargs):
            if str(path).endswith("daily_brief.json"):
                path = str(secret)
            return real_open(path, flags, *args, **kwargs)

        with mock.patch.object(serve_dump.os, "supports_dir_fd", set()), \
                mock.patch.object(serve_dump.os, "open", swapped_open):
            with self.assertRaises(serve_dump.UnsafeLocalFile):
                serve_dump.open_brief_file(self.root, "daily_brief.json")
        with mock.patch.object(serve_dump.os, "supports_dir_fd", set()):
            with serve_dump.open_brief_file(self.root, "daily_brief.json") as handle:
                self.assertEqual(handle.read(), b'{"synthetic":true}')

    def test_brief_names_must_be_plain_filenames(self):
        for name in ("../daily_brief.json", "sub/daily_brief.json", "..", "C:daily_brief.json", ""):
            with self.subTest(name=name):
                with self.assertRaises(serve_dump.UnsafeLocalFile):
                    serve_dump.open_brief_file(self.root, name)

    def test_brief_routes_refuse_hard_links_to_outside_files(self):
        secret = self._outside_secret()
        brief = self.root / "daily_brief.txt"
        try:
            os.link(secret, brief)
        except (OSError, NotImplementedError):
            self.skipTest("hard links unavailable here")
        for route in ("/api/txt", "/raw"):
            with self.subTest(route=route):
                status, _headers, body = self.request(route)
                self.assertEqual(status, 403)
                self.assertNotIn(b"outside-data-dir-secret", body)
        with mock.patch.object(serve_dump.os, "supports_dir_fd", set()):
            with self.assertRaises(serve_dump.UnsafeLocalFile):
                serve_dump.open_brief_file(self.root, "daily_brief.txt")

    def test_only_link_type_reparse_points_are_refused(self):
        # OneDrive/Cloud Files placeholders are reparse points but not links;
        # symlinks, junctions and WSL symlinks carry the name-surrogate bit.
        def entry(tag):
            return SimpleNamespace(st_mode=stat.S_IFREG | 0o644, st_file_attributes=0x400,
                                   st_reparse_tag=tag)
        for tag in (0x9000001A, 0x9000101A, 0x80000013):  # cloud, cloud, dedup
            self.assertFalse(serve_dump._is_link_entry(entry(tag)), hex(tag))
        for tag in (0xA000000C, 0xA0000003, 0xA000001D):  # symlink, junction, LX symlink
            self.assertTrue(serve_dump._is_link_entry(entry(tag)), hex(tag))
        self.assertTrue(serve_dump._is_link_entry(SimpleNamespace(st_mode=stat.S_IFLNK | 0o777)))


if __name__ == "__main__":
    unittest.main()
