"""Regression checks for OpenInsider screener parameters."""
from datetime import datetime, timezone
import sys

sys.path.insert(0, ".")

from openinsider_agent import OpenInsiderAgent


NOW = datetime(2026, 8, 18, 10, tzinfo=timezone.utc)
agent = OpenInsiderAgent(
    now=NOW,
    run_days_back=30,
    run_limit=500,
)
params = agent._default_params(
    days_back=agent.run_days_back,
    limit=agent.run_limit,
)

assert params["xp"] == "1", "OpenInsider should include purchase transactions"
assert params["xs"] == "1", "OpenInsider should include sale transactions"
assert params["fd"] == "30"
assert params["cnt"] == "500"

# Deep-dive composition can widen the canonical per-run scope without a
# second, narrower acquisition winning the single-flight race.
deep_dive = OpenInsiderAgent(now=NOW, run_days_back=60, run_limit=500)
deep_params = deep_dive._default_params(
    days_back=deep_dive.run_days_back,
    limit=deep_dive.run_limit,
)
assert deep_params["fd"] == "60"
assert deep_params["cnt"] == "500"

print("OpenInsider parameter regression checks passed")
