"""Frozen archive cohort and retained-sighting ledger regressions."""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ledger.db import connect
import ledger.ingest as ingest


def _brief(generated_at, members=None, *, alert_level="LOW"):
    document = {
        "schema_version": "2.8",
        "pipeline_version": "2.6.5",
        "generated_at": generated_at,
        "sections": {
            "insider_clusters": [{
                "ticker": "AAA",
                "cluster_direction": "buy",
                "alert_level": alert_level,
                "record_id": "shared-source",
            }],
        },
    }
    if members is not None:
        document["universe"] = {"instrumented_tickers": members}
    return document


class LedgerCohortTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def write(self, name, document):
        path = self.root / name
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    def test_archive_cohort_controls_eligibility_not_runtime_allowlist(self):
        path = self.write(
            "2026-09-21_120000Z.json",
            _brief("2026-09-21T12:00:00Z", ["AAA", "BBB"]),
        )
        db_path = self.root / "cohort.db"
        conn = connect(db_path)
        ingest.ingest_file(path, conn)

        signal = conn.execute(
            "SELECT ticker FROM signals WHERE source_record_id='shared-source'"
        ).fetchone()
        cohort = conn.execute(
            """SELECT status,reason,members_json,weights_json
               FROM run_cohorts WHERE run_id=?""",
            (path.stem,),
        ).fetchone()
        conn.close()
        self.assertEqual(signal["ticker"], "AAA")
        self.assertEqual(cohort["status"], "valid")
        self.assertIsNone(cohort["reason"])
        self.assertEqual(json.loads(cohort["members_json"]), ["AAA", "BBB"])
        self.assertEqual(json.loads(cohort["weights_json"]), {"AAA": 0.5, "BBB": 0.5})

    def test_missing_strict_membership_is_typed_refusal(self):
        path = self.write(
            "2026-09-21_120000Z.json",
            _brief("2026-09-21T12:00:00Z"),
        )
        conn = connect(self.root / "missing.db")
        ingest.ingest_file(path, conn)
        cohort = conn.execute(
            "SELECT status,reason FROM run_cohorts WHERE run_id=?",
            (path.stem,),
        ).fetchone()
        count = conn.execute("SELECT COUNT(*) AS n FROM signals").fetchone()["n"]
        conn.close()
        self.assertEqual(dict(cohort), {
            "status": "refused",
            "reason": "missing_instrumented_membership",
        })
        self.assertEqual(count, 0)

    def test_arrival_order_retains_sightings_and_stable_first_last(self):
        earlier = self.write(
            "2026-09-21_120000Z.json",
            _brief("2026-09-21T12:00:00Z", ["AAA", "BBB"], alert_level="LOW"),
        )
        later = self.write(
            "2026-09-22_120000Z.json",
            _brief("2026-09-22T12:00:00Z", ["AAA", "BBB"], alert_level="HIGH"),
        )
        projections = []
        for index, order in enumerate(((earlier, later), (later, earlier))):
            conn = connect(self.root / f"order-{index}.db")
            for path in order:
                ingest.ingest_file(path, conn)
            signal = conn.execute(
                """SELECT first_seen_run,last_seen_run,payload
                   FROM signals WHERE source_record_id='shared-source'"""
            ).fetchone()
            sightings = [
                tuple(row) for row in conn.execute(
                    """SELECT run_id,observed_at,payload FROM signal_sightings
                       ORDER BY observed_at,run_id"""
                )
            ]
            projections.append((dict(signal), sightings))
            conn.close()

        self.assertEqual(projections[0], projections[1])
        signal, sightings = projections[0]
        self.assertEqual(signal["first_seen_run"], earlier.stem)
        self.assertEqual(signal["last_seen_run"], later.stem)
        self.assertEqual(json.loads(signal["payload"])["alert_level"], "LOW")
        self.assertEqual(len(sightings), 2)

    def test_equal_observation_times_use_run_id_as_stable_tie(self):
        first = self.write(
            "tie-z.json",
            _brief("2026-09-21T12:00:00Z", ["AAA"], alert_level="HIGH"),
        )
        second = self.write(
            "tie-a.json",
            _brief("2026-09-21T12:00:00Z", ["AAA"], alert_level="LOW"),
        )
        conn = connect(self.root / "ties.db")
        ingest.ingest_file(first, conn)
        ingest.ingest_file(second, conn)
        signal = conn.execute(
            """SELECT first_seen_run,last_seen_run,payload FROM signals
               WHERE source_record_id='shared-source'"""
        ).fetchone()
        conn.close()
        self.assertEqual(signal["first_seen_run"], "tie-a")
        self.assertEqual(signal["last_seen_run"], "tie-z")
        self.assertEqual(json.loads(signal["payload"])["alert_level"], "LOW")


if __name__ == "__main__":
    unittest.main()
