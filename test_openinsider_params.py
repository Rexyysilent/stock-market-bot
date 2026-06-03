"""Regression checks for OpenInsider screener parameters."""
import sys

sys.path.insert(0, ".")

from openinsider_agent import OpenInsiderAgent


params = OpenInsiderAgent()._default_params(days_back=30, limit=500)

assert params["xp"] == "1", "OpenInsider should include purchase transactions"
assert params["xs"] == "1", "OpenInsider should include sale transactions"
assert params["fd"] == "30"
assert params["cnt"] == "500"

print("OpenInsider parameter regression checks passed")
