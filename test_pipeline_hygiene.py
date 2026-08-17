"""Offline v2.6 pipeline-hygiene regressions."""
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import export_for_gemini as exporter
from timeutil import split_fresh_records


with tempfile.TemporaryDirectory() as tmpdir:
    exporter.BASELINE_STATE_FILE = os.path.join(tmpdir, "baselines.json")
    version_state = {
        "schema_version": 2,
        "versions": {
            "2.6.1": {
                "TSLA": {
                    "volume_ratio": [
                        {"date": f"2026-06-{day:02d}", "value": 1.0}
                        for day in range(1, 16)
                    ]
                }
            }
        },
    }
    with open(exporter.BASELINE_STATE_FILE, "w", encoding="utf-8") as handle:
        json.dump(version_state, handle)
    scores, alerts = exporter.update_baselines_and_score(
        {}, {"TSLA": {"volume_ratio": 100.0}}, "2026-07-21"
    )
    assert scores["TSLA"]["volume_ratio_z"] == 8.0
    assert scores["TSLA"]["volume_ratio_baseline_n"] == 15
    assert scores["TSLA"]["volume_ratio_baseline_immature"] is True
    assert alerts[0]["signal"] == "HIGH_VOLUME_SURPRISE"
    assert alerts[0]["baseline_immature"] is True

    # Negative volume anomalies are a separate family/tag.
    version_state["versions"]["2.6.1"]["ROKU"] = {
        "volume_ratio": [
            {"date": f"2026-06-{day:02d}", "value": 1.0}
            for day in range(1, 16)
        ]
    }
    with open(exporter.BASELINE_STATE_FILE, "w", encoding="utf-8") as handle:
        json.dump(version_state, handle)
    _, alerts = exporter.update_baselines_and_score(
        {}, {"ROKU": {"volume_ratio": 0.0}}, "2026-07-21"
    )
    assert alerts[0]["signal"] == "LOW_PARTICIPATION"

    # Twenty prior observations are mature and no longer capped.
    version_state["versions"]["2.6.1"]["COIN"] = {
        "volume_ratio": [
            {"date": f"2026-06-{day:02d}", "value": 1.0}
            for day in range(1, 21)
        ]
    }
    with open(exporter.BASELINE_STATE_FILE, "w", encoding="utf-8") as handle:
        json.dump(version_state, handle)
    scores, alerts = exporter.update_baselines_and_score(
        {}, {"COIN": {"volume_ratio": 100.0}}, "2026-07-21"
    )
    assert scores["COIN"]["volume_ratio_z"] > 8
    assert alerts[0]["baseline_immature"] is False

    # Archive copies are byte-identical and never replaced.
    source = os.path.join(tmpdir, "brief.json")
    archive = os.path.join(tmpdir, "archive", "briefs")
    mirror = os.path.join(tmpdir, "mirror", "briefs")
    with open(source, "wb") as handle:
        handle.write(b'{"generated_at":"2026-07-21T21:30:00Z"}\n')
    canonical, mirrored = exporter.archive_brief(
        source, "2026-07-21T21:30:00Z", archive, mirror
    )
    assert open(canonical, "rb").read() == open(mirrored, "rb").read()
    exporter.archive_brief(source, "2026-07-21T21:30:00Z", archive, mirror)
    with open(source, "wb") as handle:
        handle.write(b"different")
    try:
        exporter.archive_brief(source, "2026-07-21T21:30:00Z", archive, mirror)
        raise AssertionError("archive collision should fail")
    except FileExistsError:
        pass


now = datetime(2026, 7, 21, 21, 30, tzinfo=timezone.utc)
fresh, dropped = split_fresh_records(
    [
        {"id": "fresh", "date": "2026-07-20T12:00:00Z"},
        {"id": "stale", "date": "2026-07-01T12:00:00Z"},
        {"id": "undated", "date": None},
    ],
    "date", 7, now=now,
)
assert [row["id"] for row in fresh] == ["fresh"]
assert [(row["id"], row["drop_reason"]) for row in dropped] == [
    ("stale", "stale"), ("undated", "undated")
]

assert exporter.SCHEMA_VERSION == "2.6"
assert exporter.PIPELINE_VERSION == "2.6.1"

# Machine-readable signal emitters cannot regress to the retired causal copy.
repo = Path(__file__).resolve().parent
signal_source = "\n".join(
    (repo / path).read_text(encoding="utf-8").lower()
    for path in ("agents/watcher_agent.py", "signals.py", "export_for_gemini.py")
)
for banned in ("buyers panicking for delivery", "gamma squeeze", "potential buy"):
    assert banned not in signal_source
watcher_source = (repo / "agents/watcher_agent.py").read_text(encoding="utf-8")
assert "VOLUME_ALERT_MULT" not in watcher_source
assert "above 20-day avg — UNUSUAL" not in watcher_source
print("v2.6 pipeline hygiene checks passed")
