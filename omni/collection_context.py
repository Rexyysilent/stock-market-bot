"""Prespecified collector strata and optional measured-expansion context.

The declared family is an evaluation target, never a filter based on classifier
discoveries. Every acquired native row remains in the source-first inventory.
"""
from __future__ import annotations

import re

from .identities import digest


def prepare_scope(store, registry, policies, *, source_id, route_id, query, now,
                  default_family, target_family=None, measurement_proposal=None,
                  measurement_arm=None, current_coverage=None, instruments=None,
                  allow_synthetic_measurement=False):
    """Validate a frozen measurement before transport; do not grant source uses."""
    if target_family is not None and (
            not isinstance(target_family, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", target_family)):
        raise ValueError("target family must be an explicit bounded family identifier")
    if (measurement_proposal is None) != (measurement_arm is None):
        raise ValueError("measurement proposal and arm must be supplied together")
    family = target_family or default_family
    metrics = {}
    if measurement_proposal is not None:
        if current_coverage is None:
            raise ValueError("versioned current coverage is required for a measured collection")
        from .expansion import measurement_context
        context = measurement_context(store, measurement_proposal, measurement_arm,
            registry, policies, current_coverage, source_id, route_id, now,
            family=target_family, instruments=instruments, allow_synthetic=allow_synthetic_measurement)
        if context["query_hash"] != digest(query):
            raise ValueError("collector query differs from the frozen measurement query")
        family = context["sampling_family"]
        metrics.update(context)
    metrics["collection_scope"] = {
        "contract": "omni-collection-scope-1", "source_id": source_id,
        "route_id": route_id, "target_family": family, "query_hash": digest(query),
        "native_inventory_rule": "all_acquired_rows_before_classification",
        "classifier_filter_applied": False, "membership_changed": False,
    }
    return family, metrics
