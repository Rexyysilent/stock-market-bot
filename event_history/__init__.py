"""Rebuildable evidence history (T7): an append-only sidecar over archived briefs.

It reads archives and never writes them, the ledger, state or any collector.
See adapters.py for what each archive version contributes, store.py for the
decision rules, and views.py for the three views.
"""
from .db import CONTRACT_VERSION, DEFAULT_DB, connect
from .store import (
    anchor_filings, annotate_archive, create_event, import_archive, import_paths,
    record_evidence_relation, record_event_relation, record_membership,
    register_resolver,
)
from .views import exact_archive, knowledge_view, origin_summary, time_relation, view_digest

__all__ = [
    "CONTRACT_VERSION", "DEFAULT_DB", "connect", "anchor_filings", "annotate_archive",
    "create_event", "import_archive", "import_paths", "record_evidence_relation",
    "record_event_relation", "record_membership", "register_resolver", "exact_archive",
    "knowledge_view", "origin_summary", "time_relation", "view_digest",
]
