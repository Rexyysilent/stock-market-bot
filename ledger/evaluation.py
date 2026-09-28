"""Prospective evaluation partitions (M11 first part).

Development, holdout and purged are assigned from completed-session dates
only. Any future interval method, threshold study or model comparison must be
designed on development outcomes and evaluated on the holdout; purged
outcomes belong to neither because their window straddles the boundary.
"""
from __future__ import annotations

from .config import (
    EVALUATION_DESIGN_VERSION, EVALUATION_HOLDOUT_DECLARED_AT,
    EVALUATION_HOLDOUT_START,
)

PARTITIONS = ("development", "purged", "holdout")


def evaluation_partition(entry_session, exit_session,
                         holdout_start=EVALUATION_HOLDOUT_START):
    """Return development / purged / holdout, or None for an unusable window."""
    if not entry_session or not exit_session or exit_session < entry_session:
        return None
    if entry_session >= holdout_start:
        return "holdout"
    if exit_session >= holdout_start:
        return "purged"
    return "development"


def evaluation_design():
    return {
        "version": EVALUATION_DESIGN_VERSION,
        "holdout_start": EVALUATION_HOLDOUT_START,
        "declared_at": EVALUATION_HOLDOUT_DECLARED_AT,
        "rule": (
            "entry sessions on or after holdout_start are holdout; outcomes "
            "entered earlier whose exit reaches holdout_start are purged from "
            "development; design on development, evaluate once on holdout"
        ),
    }
