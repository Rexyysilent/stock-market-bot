"""Refuse a scheduled run when this machine's clock cannot be trusted.

Every time the pipeline writes (generated_at, archive names, price
acquisitions, outcome revisions) comes from the local clock, and the ledger
places each signal's entry session from generated_at. A clock that is hours
off labels a run with a time it could not have known: a run that really
happens after a close can be entered at a session whose close it already saw.
The dual-boot machine this runs on has come up 12.5 hours behind.

A missed run is visibly missing; a mislabelled run silently enters the
append-only archive. So a skewed clock, or one no time source can confirm,
stops the run before any stage writes.

    python clock_guard.py [--max-skew SECONDS] [--state PATH]

Exit 0: clock verified within tolerance. 2: skewed. 3: unverified.
"""
from __future__ import annotations

import argparse
import email.utils
import json
import socket
import statistics
import struct
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

MAX_SKEW_SECONDS = 300
AGREEMENT_SECONDS = 5
MEASUREMENTS_WANTED = 3
TIMEOUT_SECONDS = 3
NTP_EPOCH_OFFSET = 2208988800  # 1900-01-01 to 1970-01-01
NTP_SERVERS = ("time.windows.com", "time.cloudflare.com", "time.google.com",
               "pool.ntp.org")
HTTP_DATE_URLS = ("https://www.google.com", "https://www.cloudflare.com")
STATE_PATH = Path("state") / "clock_check.json"
EXIT_CODES = {"ok": 0, "skewed": 2, "unverified": 3}


def _ntp_seconds(raw):
    whole, fraction = struct.unpack("!II", raw)
    return whole - NTP_EPOCH_OFFSET + fraction / 2**32


def parse_sntp(data, sent_at, received_at):
    """Offset (server minus local, seconds) from an SNTP server reply."""
    if len(data) < 48:
        raise ValueError("short NTP reply")
    leap, mode, stratum = data[0] >> 6, data[0] & 7, data[1]
    if mode != 4:
        raise ValueError(f"NTP reply mode {mode} is not a server reply")
    if leap == 3 or not 1 <= stratum <= 15:
        raise ValueError(f"NTP server unsynchronized (leap={leap}, stratum={stratum})")
    if data[40:48] == b"\0" * 8:
        raise ValueError("NTP reply has no transmit time")
    server_received = _ntp_seconds(data[32:40])
    server_sent = _ntp_seconds(data[40:48])
    return ((server_received - sent_at) + (server_sent - received_at)) / 2


def sntp_offset(host, *, timeout=TIMEOUT_SECONDS, clock=time.time,
                sock_factory=socket.socket):
    packet = b"\x1b" + b"\0" * 47  # version 3, client mode
    with sock_factory(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        sent_at = clock()
        sock.sendto(packet, (host, 123))
        data, _ = sock.recvfrom(512)
        received_at = clock()
    return parse_sntp(data, sent_at, received_at)


def http_date_offset(url, *, timeout=TIMEOUT_SECONDS, clock=time.time,
                     opener=urllib.request.urlopen):
    """Coarse (one-second) fallback from an HTTPS Date header."""
    request = urllib.request.Request(url, method="HEAD")
    sent_at = clock()
    try:
        response = opener(request, timeout=timeout)
        headers = response.headers
        response.close()
    except urllib.error.HTTPError as error:
        headers = error.headers
    received_at = clock()
    value = headers.get("Date") if headers else None
    if not value:
        raise ValueError("response has no Date header")
    server = email.utils.parsedate_to_datetime(value).timestamp()
    return server - (sent_at + received_at) / 2


def default_probes():
    probes = [(f"ntp:{host}", lambda host=host: sntp_offset(host))
              for host in NTP_SERVERS]
    probes += [(f"http-date:{url}", lambda url=url: http_date_offset(url))
               for url in HTTP_DATE_URLS]
    return probes


def _describe(offset):
    direction = "behind" if offset > 0 else "ahead of"
    return f"{abs(offset):.1f} s {direction} network time"


def check_clock(probes=None, *, max_skew=MAX_SKEW_SECONDS):
    """Measure the local offset; a majority of sources must agree."""
    measurements, errors = [], []
    for name, probe in probes if probes is not None else default_probes():
        if len(measurements) >= MEASUREMENTS_WANTED:
            break
        try:
            measurements.append({"source": name, "offset_seconds": float(probe())})
        except Exception as error:  # every failure is recorded, none trusted
            errors.append({"source": name, "error": f"{type(error).__name__}: {error}"})
    result = {"status": "unverified", "offset_seconds": None,
              "max_skew_seconds": max_skew, "measurements": measurements,
              "errors": errors}
    if not measurements:
        result["message"] = "no time source reachable; clock not verified"
        return result
    offsets = [m["offset_seconds"] for m in measurements]
    middle = statistics.median(offsets)
    agreeing = [o for o in offsets if abs(o - middle) <= AGREEMENT_SECONDS]
    if len(agreeing) * 2 <= len(offsets):
        result["message"] = "time sources disagree; clock not verified"
        return result
    offset = statistics.median(agreeing)
    result["offset_seconds"] = offset
    if abs(offset) > max_skew:
        result["status"] = "skewed"
        result["message"] = (
            f"local clock is {_describe(offset)} (limit {max_skew} s); "
            "run refused. Fix it with `w32tm /resync /force` in an administrator "
            "terminal, then rerun."
        )
    else:
        result["status"] = "ok"
        result["message"] = f"local clock within tolerance ({_describe(offset)})"
    return result


def main(argv=None, *, probes=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--max-skew", type=float, default=MAX_SKEW_SECONDS)
    parser.add_argument("--state", type=Path, default=STATE_PATH)
    args = parser.parse_args(argv)
    result = check_clock(probes, max_skew=args.max_skew)
    result["checked_at_local"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    args.state.parent.mkdir(parents=True, exist_ok=True)
    args.state.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    if result["status"] != "ok":
        print(f"[clock_guard] {result['message']}", file=sys.stderr)
    return EXIT_CODES[result["status"]]


if __name__ == "__main__":
    sys.exit(main())
