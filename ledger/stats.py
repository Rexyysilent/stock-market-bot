"""Deterministic per-family Signal Ledger statistics."""
from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from pathlib import Path

from .config import DB_PATH, MIN_N, STATS_JSON_PATH, STATS_MD_PATH
from .db import connect
from .evaluation import PARTITIONS, evaluation_design, evaluation_partition

STATS_SCHEMA_VERSION = "2.6"
DEPENDENCE_WARNING = (
    "n counts (ticker, entry_session) clusters, which are not independent "
    "episodes: same-session market dependence and overlapping forward windows "
    "are not corrected, so no inferential interval is published."
)
INFERENCE_REASON = "dependence_aware_sampling_design_absent"
ZERO_BENCHMARK_TOLERANCE = 1e-12  # returns are fractions; far below any real move


def _round(value):
    return None if value is None else round(float(value), 8)


def _percentile(values, percentile):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)


def _group_keys(row):
    yield ("family", row["family"])
    if row["subtype"]:
        yield ("subtype", f"{row['family']}|{row['subtype']}")
    if row["strength"]:
        yield ("strength", f"{row['family']}|{row['strength']}")
    if row["regime_at_emission"]:
        yield ("regime", f"{row['family']}|{row['regime_at_emission']}")


def _benchmark_pair(members):
    """Raw and universe means over the same complete-benchmark members.

    Outcomes without a complete frozen-cohort benchmark revision (partial,
    unavailable, refused, or filled before revisions existed) are excluded
    from both sides rather than only from the denominator.
    """
    matched = [row for row in members
               if row["benchmark_status"] == "complete"
               and row["univ_ret"] is not None]
    if not matched:
        return None
    return (statistics.fmean(float(row["ret"]) for row in matched),
            statistics.fmean(float(row["univ_ret"]) for row in matched))


def _cell(rows, min_n):
    unpriceable = sum(1 for row in rows if row["status"] == "unpriceable")
    filled = [row for row in rows if row["status"] == "filled" and row["ret"] is not None]
    clusters = defaultdict(list)
    for row in filled:
        clusters[(row["ticker"], row["entry_session"])].append(row)

    cluster_rows = []
    for key, members in sorted(clusters.items()):
        raw_values = [float(row["ret"]) for row in members]
        signed = []
        for row in members:
            if row["excess"] is None:
                continue
            if row["direction"] == "long":
                signed.append(float(row["excess"]))
            elif row["direction"] == "short":
                signed.append(-float(row["excess"]))
        cluster_rows.append({
            "raw": statistics.fmean(raw_values),
            "pair": _benchmark_pair(members),
            "signed": statistics.fmean(signed) if signed else None,
            "raw_only": all(row["asset_class"] in ("future", "index") for row in members),
        })

    n = len(cluster_rows)
    n_rows = len(filled)
    signed = [row["signed"] for row in cluster_rows if row["signed"] is not None]
    raw_only = [row["raw"] for row in cluster_rows if row["raw_only"]]
    pairs = [row["pair"] for row in cluster_rows if row["pair"] is not None]
    partitions = defaultdict(int)
    for ticker, entry_session in clusters:
        exit_session = clusters[(ticker, entry_session)][0]["exit_session"]
        partitions[evaluation_partition(entry_session, exit_session)] += 1
    if not pairs:
        energy_status = "unavailable"
    elif len(pairs) < min_n:
        energy_status = "accumulating"
    else:
        energy_status = "ready"
    result = {
        "status": "ready" if n >= min_n else "accumulating",
        "n": n,
        "n_rows": n_rows,
        "n_directional": len(signed),
        "n_entry_sessions": len({row["entry_session"] for row in filled}),
        "n_benchmark_pairs": len(pairs),
        **{f"n_{name}": partitions[name] for name in PARTITIONS},
        "directional_status": "ready" if len(signed) >= min_n else "accumulating",
        "energy_status": energy_status,
        "inference_status": "ineligible",
        "inference_reason": INFERENCE_REASON,
        "dependence_warning": DEPENDENCE_WARNING,
        "n_unpriceable": unpriceable,
        "hit_rate": None,
        "mean_signed_excess": None,
        "median_signed_excess": None,
        "p25_signed_excess": None,
        "p75_signed_excess": None,
        "mean_ci95": None,
        "energy": None,
        "raw_only": {
            "n": len(raw_only),
            "mean_ret": _round(statistics.fmean(raw_only)) if raw_only else None,
            "median_ret": _round(statistics.median(raw_only)) if raw_only else None,
        },
    }
    if n < min_n:
        return result

    if len(signed) >= min_n:
        result.update({
            "hit_rate": _round(sum(value > 0 for value in signed) / len(signed)),
            "mean_signed_excess": _round(statistics.fmean(signed)),
            "median_signed_excess": _round(statistics.median(signed)),
            "p25_signed_excess": _round(_percentile(signed, 0.25)),
            "p75_signed_excess": _round(_percentile(signed, 0.75)),
        })
    if energy_status == "ready":
        numerator = statistics.median(abs(raw) for raw, _ in pairs)
        denominator = statistics.median(abs(universe) for _, universe in pairs)
        # A universe move indistinguishable from zero (floating residue of an
        # exactly offsetting basket) makes the ratio undefined, not enormous.
        if denominator > ZERO_BENCHMARK_TOLERANCE:
            result["energy"] = _round(numerator / denominator)
        else:
            result["energy_status"] = "undefined_zero_benchmark"
    return result


def build_stats(db_path=DB_PATH, min_n=MIN_N):
    conn = connect(db_path)
    try:
        rows = conn.execute(
            """SELECT r.pipeline_version,s.family,s.subtype,s.strength,
                      s.regime_at_emission,s.ticker,s.direction,s.asset_class,
                      o.horizon,o.entry_session,o.exit_session,o.ret,o.excess,
                      o.univ_ret,o.status,
                      (SELECT rv.benchmark_status FROM outcome_revisions rv
                       WHERE rv.record_id=o.record_id AND rv.horizon=o.horizon
                       ORDER BY rv.revision_number DESC LIMIT 1) AS benchmark_status
               FROM signals s JOIN runs r ON r.run_id=s.first_seen_run
               JOIN outcomes o ON o.record_id=s.record_id
               ORDER BY r.pipeline_version,s.family,s.record_id,o.horizon"""
        ).fetchall()
    finally:
        conn.close()

    grouped = defaultdict(list)
    latest_exit = None
    for row in rows:
        if row["exit_session"] and (latest_exit is None or row["exit_session"] > latest_exit):
            latest_exit = row["exit_session"]
        for grain, key in _group_keys(row):
            grouped[(row["pipeline_version"], grain, key, row["horizon"])].append(row)

    segments = defaultdict(dict)
    for (version, grain, key, horizon), members in sorted(grouped.items()):
        group_key = (grain, key)
        group = segments[version].setdefault(group_key, {
            "grain": grain,
            "key": key,
            "horizons": {},
        })
        group["horizons"][str(horizon)] = _cell(members, min_n)

    return {
        "schema_version": STATS_SCHEMA_VERSION,
        "as_of_session": latest_exit,
        "config": {
            "min_n": min_n,
            "n_definition": "unique (ticker, entry_session) clusters",
            "energy_definition": (
                "median |raw| / median |universe| over the same clusters with a "
                "complete frozen-cohort benchmark; gated on n_benchmark_pairs"
            ),
            "inference": "withheld",
            "evaluation_design": evaluation_design(),
        },
        "segments": [
            {
                "pipeline_version": version,
                "groups": [groups[key] for key in sorted(groups)],
            }
            for version, groups in sorted(segments.items())
        ],
    }


def render_markdown(stats):
    lines = [
        "# Signal Ledger Statistics",
        "",
        f"As of completed session: {stats.get('as_of_session') or 'none'}",
        "",
    ]
    for segment in stats["segments"]:
        lines.extend([f"## Pipeline {segment['pipeline_version']}", ""])
        for group in segment["groups"]:
            lines.append(f"### {group['grain']}: {group['key']}")
            lines.append("")
            lines.append("| Horizon | n | Sessions | Unpriceable | Result |")
            lines.append("|---:|---:|---:|---:|---|")
            for horizon, cell in sorted(group["horizons"].items(), key=lambda item: int(item[0])):
                if cell["status"] == "accumulating":
                    result = f"n={cell['n']} (accumulating)"
                else:
                    pieces = []
                    if cell.get("directional_status") == "accumulating":
                        pieces.append(f"directional n={cell['n_directional']} (accumulating)")
                    if cell["hit_rate"] is not None:
                        pieces.append(f"hit {cell['hit_rate']:.1%}")
                        pieces.append(f"mean excess {cell['mean_signed_excess']:.2%}")
                        pieces.append(
                            f"IQR [{cell['p25_signed_excess']:.2%}, "
                            f"{cell['p75_signed_excess']:.2%}]"
                        )
                    if cell["energy"] is not None:
                        pieces.append(
                            f"energy {cell['energy']:.2f}x "
                            f"(pairs={cell['n_benchmark_pairs']})"
                        )
                    elif cell["energy_status"] == "accumulating":
                        pieces.append(
                            f"energy pairs={cell['n_benchmark_pairs']} (accumulating)"
                        )
                    raw_only = cell["raw_only"]
                    if raw_only["n"]:
                        pieces.append(
                            f"raw-only n={raw_only['n']}, mean {raw_only['mean_ret']:.2%}"
                        )
                    result = "; ".join(pieces) or "no directional statistic"
                lines.append(
                    f"| +{horizon} | {cell['n']} | {cell['n_entry_sessions']} "
                    f"| {cell['n_unpriceable']} | {result} |"
                )
            lines.append("")
    lines.extend([
        "## Standing caveats",
        "",
        "- Evolving insider clusters can create more than one source record.",
        "- The watchlist baseline has survivorship bias.",
        "- Directional statistics have their own minimum sample gate.",
        "- n counts ticker/session clusters, not independent episodes; the Sessions column shows distinct entry sessions.",
        "- No confidence interval is published: same-session market dependence and overlapping horizons require a blocked, forward-held-out evaluation that does not yet exist.",
        (f"- Evaluation holdout {stats['config']['evaluation_design']['version']}: "
         f"entry sessions from {stats['config']['evaluation_design']['holdout_start']} "
         f"are reserved for evaluation (declared "
         f"{stats['config']['evaluation_design']['declared_at']}); windows crossing "
         "that date are purged from development."),
        "- Energy compares raw and universe moves only on clusters with a complete frozen-cohort benchmark, gated on that matched count.",
        "- This measures signals; it is not a strategy backtest and includes no costs, sizing, or fills.",
        "- Option anomalies are directionless until timestamped trade/NBBO classification exists.",
        "",
    ])
    return "\n".join(lines)


def write_stats(db_path=DB_PATH, json_path=STATS_JSON_PATH, md_path=STATS_MD_PATH,
                min_n=MIN_N):
    stats = build_stats(db_path, min_n=min_n)
    json_path, md_path = Path(json_path), Path(md_path)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(stats, sort_keys=True, indent=2, ensure_ascii=False,
                         allow_nan=False) + "\n"
    json_path.write_text(payload, encoding="utf-8", newline="\n")
    md_path.write_text(render_markdown(stats), encoding="utf-8", newline="\n")
    return stats
