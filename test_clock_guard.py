"""A scheduled run refuses to start on a clock it cannot verify (network-free)."""
import email.utils
import io
import json
import struct
import tempfile
import unittest
import urllib.error
from pathlib import Path

import clock_guard
from clock_guard import (
    NTP_EPOCH_OFFSET, check_clock, http_date_offset, main, parse_sntp, sntp_offset,
)

# A host booting 12.5 h (45000 s) behind, as after an RTC written in another zone.
REAL = 1790148000.0
BEHIND = 45000.0


def _ntp_ts(unix_seconds):
    ntp = unix_seconds + NTP_EPOCH_OFFSET
    whole = int(ntp)
    return struct.pack("!II", whole, int((ntp - whole) * 2**32))


def _reply(receive, transmit, *, leap=0, mode=4, stratum=2):
    head = bytes([(leap << 6) | (4 << 3) | mode, stratum, 6, 0xEC])
    return head + b"\0" * 28 + _ntp_ts(receive) + _ntp_ts(transmit)


class FakeSocket:
    def __init__(self, reply):
        self.reply, self.sent = reply, []

    def __call__(self, family, kind):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def settimeout(self, value):
        self.timeout = value

    def sendto(self, data, address):
        self.sent.append((data, address))

    def recvfrom(self, size):
        return self.reply, ("192.0.2.1", 123)


def _probe(value):
    calls = []

    def probe():
        calls.append(1)
        if isinstance(value, Exception):
            raise value
        return value
    probe.calls = calls
    return probe


class SntpTests(unittest.TestCase):
    def test_offset_is_positive_when_local_clock_is_behind(self):
        t1, t4 = REAL - BEHIND, REAL - BEHIND + 0.2
        reply = _reply(REAL + 0.1, REAL + 0.1)
        self.assertAlmostEqual(parse_sntp(reply, t1, t4), BEHIND, places=3)

    def test_rejects_replies_that_are_not_usable_time(self):
        good = _reply(REAL, REAL)
        for bad in (good[:47], _reply(REAL, REAL, stratum=0),
                    _reply(REAL, REAL, mode=3), _reply(REAL, REAL, leap=3),
                    good[:40] + b"\0" * 8):
            with self.assertRaises(ValueError):
                parse_sntp(bad, REAL, REAL)

    def test_sntp_query_uses_a_client_packet_on_port_123(self):
        ticks = iter((REAL - BEHIND, REAL - BEHIND))
        sock = FakeSocket(_reply(REAL, REAL))
        offset = sntp_offset("time.example", clock=lambda: next(ticks),
                             sock_factory=sock)
        self.assertAlmostEqual(offset, BEHIND, places=3)
        data, address = sock.sent[0]
        self.assertEqual((len(data), data[0] & 7, address), (48, 3, ("time.example", 123)))


class HttpDateTests(unittest.TestCase):
    def headers(self, when):
        return {"Date": email.utils.formatdate(when, usegmt=True)}

    def test_http_date_offset_uses_the_round_trip_midpoint(self):
        ticks = iter((REAL - BEHIND - 0.5, REAL - BEHIND + 0.5))

        class Response(io.BytesIO):
            headers = self.headers(REAL)

        offset = http_date_offset("https://example.test", clock=lambda: next(ticks),
                                  opener=lambda request, timeout: Response())
        self.assertAlmostEqual(offset, BEHIND, delta=1.0)

    def test_error_status_still_carries_a_usable_date(self):
        ticks = iter((REAL, REAL))
        error = urllib.error.HTTPError("https://example.test", 405, "no", self.headers(REAL), None)

        def opener(request, timeout):
            raise error
        self.assertAlmostEqual(
            http_date_offset("https://example.test", clock=lambda: next(ticks), opener=opener),
            0.0, delta=1.0)

    def test_missing_date_is_an_error(self):
        class Response(io.BytesIO):
            headers = {}
        with self.assertRaises(ValueError):
            http_date_offset("https://example.test", clock=lambda: REAL,
                             opener=lambda request, timeout: Response())


class CheckClockTests(unittest.TestCase):
    def test_small_offset_passes(self):
        result = check_clock([("a", _probe(1.5)), ("b", _probe(1.4))])
        self.assertEqual(result["status"], "ok")
        self.assertAlmostEqual(result["offset_seconds"], 1.45)

    def test_the_2026_09_23_offset_is_refused(self):
        result = check_clock([("a", _probe(BEHIND)), ("b", _probe(BEHIND - 0.3))])
        self.assertEqual(result["status"], "skewed")
        self.assertIn("behind", result["message"])
        self.assertIn("w32tm /resync", result["message"])

    def test_clock_ahead_is_refused_too(self):
        result = check_clock([("a", _probe(-900.0))])
        self.assertEqual(result["status"], "skewed")
        self.assertIn("ahead", result["message"])

    def test_no_reachable_source_is_unverified_not_ok(self):
        result = check_clock([("a", _probe(OSError("timed out"))),
                              ("b", _probe(ValueError("kiss of death")))])
        self.assertEqual(result["status"], "unverified")
        self.assertIsNone(result["offset_seconds"])
        self.assertEqual([e["source"] for e in result["errors"]], ["a", "b"])

    def test_two_sources_that_disagree_cannot_verify(self):
        result = check_clock([("a", _probe(0.2)), ("b", _probe(BEHIND))])
        self.assertEqual(result["status"], "unverified")
        self.assertIn("disagree", result["message"])

    def test_one_rogue_source_is_outvoted(self):
        result = check_clock([("a", _probe(BEHIND)), ("b", _probe(0.4)),
                              ("c", _probe(0.2))])
        self.assertEqual(result["status"], "ok")
        self.assertAlmostEqual(result["offset_seconds"], 0.3)

    def test_probing_stops_after_enough_measurements(self):
        probes = [(name, _probe(0.1)) for name in "abcde"]
        check_clock(probes)
        self.assertEqual([len(p.calls) for _, p in probes], [1, 1, 1, 0, 0])


class MainTests(unittest.TestCase):
    def run_main(self, probes):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state" / "clock_check.json"
            code = main(["--state", str(state)], probes=probes)
            return code, json.loads(state.read_text(encoding="utf-8"))

    def test_exit_codes_and_state_record(self):
        code, record = self.run_main([("a", _probe(0.2))])
        self.assertEqual((code, record["status"]), (0, "ok"))
        self.assertEqual(record["max_skew_seconds"], clock_guard.MAX_SKEW_SECONDS)
        self.assertTrue(record["checked_at_local"].endswith("Z"))
        self.assertEqual(self.run_main([("a", _probe(BEHIND))])[0], 2)
        self.assertEqual(self.run_main([("a", _probe(OSError("down")))])[0], 3)


if __name__ == "__main__":
    unittest.main()
