"""Duplicate occurrence regression, reduced from the September 1 export.

The actual archive stays immutable; this fixture tests the identity relationship.
"""
from copy import deepcopy
from scripts.validate_export_schema import (
    DEFAULT_FIXTURE, load_json, editorial_focus_contract_semantic_errors,
)

base = load_json(DEFAULT_FIXTURE.with_name("daily_brief.current-2.6.json"))
assert editorial_focus_contract_semantic_errors(base) == []
evidence = base["sections"]["deep_dive"]["evidence"][0]
selected = next(r for r in base["sections"]["headlines"]
                if r["source_record_id"] == evidence["source_record_id"])
duplicate = {
    **deepcopy(selected), "reason": "duplicate",
    "kept_source_record_id": selected["source_record_id"],
}
good = deepcopy(base)
good["data_quality"]["headlines_dropped"].append(duplicate)
assert editorial_focus_contract_semantic_errors(good) == []

# A copied ID cannot launder another rejection, story, time or provider.
for field, value in (
    ("reason", "stale"), ("reason", "selection_limit"),
    ("reason", "no_approved_lane"), ("reason", None),
    ("kept_source_record_id", "another:story"),
    ("canonical_url", "https://unrelated.example/story"),
    ("canonical_url", None), ("title", "Unrelated issuer"),
    ("provider", "Another provider"), ("publisher", "Another publisher"),
    ("source_class", "unknown"), ("source_time_kind", None),
    ("as_of", "2020-01-01T00:00:00Z"),
):
    bad = deepcopy(good)
    bad["data_quality"]["headlines_dropped"][-1][field] = value
    errors = editorial_focus_contract_semantic_errors(bad)
    assert any("cannot supply" in e.message for e in errors), (field, value)

# A valid duplicate occurrence must not cancel a second conflicting occurrence.
bad = deepcopy(good)
bad["data_quality"]["headlines_dropped"].append(
    {**duplicate, "reason": "stale"}
)
assert any("cannot supply" in e.message
           for e in editorial_focus_contract_semantic_errors(bad))

# Removing the representative does not turn a dropped copy into valid evidence.
bad = deepcopy(good)
bad["sections"]["headlines"] = [
    r for r in bad["sections"]["headlines"]
    if r["source_record_id"] != selected["source_record_id"]
]
assert any("cannot supply" in e.message
           for e in editorial_focus_contract_semantic_errors(bad))
print("PASS selected duplicate occurrence and rejected/conflicting-only negatives")

