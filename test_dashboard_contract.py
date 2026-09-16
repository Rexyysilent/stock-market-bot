"""Static sink/asset guard plus the real demo schema and inspection contract."""
import json
from pathlib import Path
import subprocess
import sys
from brief_tools import inspect_brief

ROOT = Path(__file__).resolve().parent
source = (ROOT / "dashboard/app.js").read_text(encoding="utf-8")
assert "innerHTML" not in source
assert "insertAdjacentHTML" not in source
assert "document.write(" not in source
assert "eval(" not in source
assert "tech.rsi || 50" not in source
html = (ROOT / "dashboard/index.html").read_text(encoding="utf-8")
assert "fonts.googleapis.com" not in html
assert "user-scalable=no" not in html
assert "onclick=" not in html
for required in ("quality-status", "source-health", "confluence-list", "ticker-filter", "load-status"):
    assert f'id="{required}"' in html
fixture = ROOT / "fixtures/exports/daily_brief.demo.json"
subprocess.run([sys.executable, str(ROOT / "scripts/validate_export_schema.py"), str(fixture)], check=True, timeout=20)
brief = json.loads(fixture.read_text(encoding="utf-8"))
assert brief["demo"] is True
assert "SYNTHETIC" in brief["health"]["warnings"][0]
quality = inspect_brief(brief)
assert quality["coverage"]["numeric_price_records"] == 1
assert quality["coverage"]["rsi_unavailable"] == ["AAA"]
assert "derived_measurement_warning" in quality
print("Dashboard text-sink, offline-asset and synthetic-demo contracts passed")
