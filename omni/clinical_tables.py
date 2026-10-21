"""Exact plain-text pipe tables; no HTML reconstruction or inherited scope.

Every accepted row repeats its complete clinical scope. Column labels and all
cells are revision-bound. Footnotes, merged cells, duplicate/unknown headings,
split numeric units and mixed paragraphs explicitly refuse the whole block.
"""
from __future__ import annotations

from copy import deepcopy
import re

from .clinical_numerics import parse_sentence, semantic, EXTENDED_VERSION
from .evidence import span
from .identities import digest

HEADERS = {"trial": "", "phase": "Phase ", "endpoint": "endpoint: ",
           "rank": "rank: ", "population": "population: ", "cohort": "cohort: ",
           "treatment": "treatment: ", "comparator": "comparator: ",
           "timepoint": "timepoint: ", "analysis": "analysis: ",
           "multiplicity": "multiplicity: ", "statistics": ""}
REQUIRED = {"trial", "endpoint", "population", "treatment", "comparator", "timepoint", "analysis", "statistics"}
FOOTNOTE = re.compile(r"[†‡¹²³]|<sup\b|(?m:^\s*(?:\*|\[\d+\]|[a-z]\)|notes?\s*:|footnotes?\s*:))", re.I)


def _cells(doc, start, end):
    line = doc["text"][start:end]
    if not line.strip().startswith("|") or not line.strip().endswith("|"):
        raise ValueError("numeric_table_line_unsupported")
    left, right = line.index("|"), line.rindex("|")
    boundaries = [m.start() for m in re.finditer(r"\|", line[left:right+1])]
    cells = []
    for lo, hi in zip(boundaries, boundaries[1:]):
        lo, hi = lo + left + 1, hi + left
        raw = line[lo:hi]
        if not raw.strip():
            raise ValueError("numeric_table_empty_or_merged_cell")
        a = lo + len(raw) - len(raw.lstrip())
        b = hi - (len(raw) - len(raw.rstrip()))
        cells.append(span(doc, start + a, start + b))
    return cells


def parse_tables(doc, registry, *, known_by):
    output = {"results": [], "abstentions": [], "blocks": []}
    for block in re.finditer(r"[^\n]+(?:\n(?!\s*\n)[^\n]+)*", doc["text"]):
        if "|" not in block[0]:
            continue
        start, end = block.span()
        block_support = span(doc, start, end)
        output["blocks"].append((start, end))

        def refuse(reason):
            output["abstentions"].append({"reason": reason, "support": block_support,
                "human_review_status": "pending", "signal_eligible": False})

        if FOOTNOTE.search(doc["text"]) or re.search(r"[\*\t<>]", block[0]):
            refuse("numeric_table_footnote_or_markup_unsupported")
            continue
        lines = [(start + m.start(), start + m.end()) for m in re.finditer(r"[^\n]+", block[0])]
        if len(lines) < 2 or len(lines) > 22 or end - start > 20000:
            refuse("numeric_table_size_or_shape_unsupported")
            continue
        try:
            header_cells = _cells(doc, *lines[0])
            names = [c["text"].casefold() for c in header_cells]
            if len(names) != len(set(names)) or set(names) - HEADERS.keys() or not REQUIRED <= set(names):
                raise ValueError("numeric_table_header_ambiguous_or_unsupported")
            # Statistics is one self-describing cell, never an inferred measure
            # from a value in an adjacent unlabeled/merged column.
            row_lines = lines[1:]
            first = _cells(doc, *row_lines[0])
            if all(re.fullmatch(r":?-{3,}:?", c["text"]) for c in first):
                if len(first) != len(names):
                    raise ValueError("numeric_table_column_count_mismatch")
                row_lines = row_lines[1:]
            if not row_lines:
                raise ValueError("numeric_table_rows_missing")
            accepted = []
            for row_start, row_end in row_lines:
                cells = _cells(doc, row_start, row_end)
                if len(cells) != len(names):
                    raise ValueError("numeric_table_column_count_mismatch")
                virtual, mapping = "", []
                for name, cell in zip(names, cells):
                    if virtual:
                        virtual += "; "
                    virtual += HEADERS[name]
                    low = len(virtual)
                    virtual += cell["text"]
                    mapping.append((low, len(virtual), cell["start"]))
                proxy = dict(doc, text=virtual)
                parsed = parse_sentence(proxy, 0, len(virtual), registry, known_by=known_by)
                if not parsed["results"]:
                    reason = parsed["abstentions"][0]["reason"] if parsed["abstentions"] else "numeric_table_statistics_missing"
                    raise ValueError(reason)
                result = deepcopy(parsed["results"][0])

                def remap(value):
                    if isinstance(value, dict):
                        if set(value) == {"evidence_id", "revision_id", "start", "end", "text"}:
                            if (value["evidence_id"], value["revision_id"]) != (doc["evidence_id"], doc["revision_id"]):
                                return value
                            for lo, hi, native in mapping:
                                if lo <= value["start"] < value["end"] <= hi:
                                    return span(doc, native + value["start"] - lo, native + value["end"] - lo)
                            raise ValueError("numeric_table_cross_cell_proof_unsupported")
                        return {k: remap(v) for k, v in value.items()}
                    if isinstance(value, list):
                        return [remap(v) for v in value]
                    return value

                result.pop("support")
                result = remap(result)
                result["support"] = span(doc, row_start, row_end)
                result["contract"] = EXTENDED_VERSION
                result["source_form"] = "complete_pipe_table_row"
                result["table_proof"] = {"block": block_support, "header": span(doc, *lines[0]),
                    "row": result["support"], "columns": [{"name": n, "header": h, "cell": c}
                        for n, h, c in zip(names, header_cells, cells)]}
                result["result_id"] = "numeric:" + digest(semantic(result))
                accepted.append(result)
            # A row/cell/footnote ambiguity poisons the complete bounded table,
            # preventing a cropped prefix from hiding a competing source row.
            output["results"].extend(accepted)
        except (ValueError, KeyError, TypeError) as exc:
            refuse(str(exc) if isinstance(exc, ValueError) else "numeric_table_invalid")
    return output
