"""Offline JSON Schema validation and negative-fixture regression."""

from copy import deepcopy
import sys

sys.path.insert(0, ".")

from scripts.validate_export_schema import (
    DEFAULT_FIXTURE,
    DEFAULT_SCHEMA,
    load_json,
    validation_errors,
)


schema = load_json(DEFAULT_SCHEMA)
valid = load_json(DEFAULT_FIXTURE)
assert validation_errors(valid, schema) == []
assert (
    valid["data_quality"]["headline_pool"]["accounted_candidate_count"]
    == valid["data_quality"]["headline_pool"]["fetched_count"]
)

missing_required = deepcopy(valid)
del missing_required["generated_at"]
errors = validation_errors(missing_required, schema)
assert any(error.validator == "required" for error in errors)

invalid_lane = deepcopy(valid)
invalid_lane["sections"]["headlines"][0]["lane"] = "exchange_prefix"
errors = validation_errors(invalid_lane, schema)
assert any(error.validator == "enum" for error in errors)

invalid_components = deepcopy(valid)
del invalid_components["sections"]["headlines"][0]["score_components"]["impact"]
errors = validation_errors(invalid_components, schema)
assert any(error.validator == "required" for error in errors)

print("Daily brief JSON Schema positive and negative checks passed")
