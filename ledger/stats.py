"""Deterministic per-family Signal Ledger statistics."""
from __future__ import annotations

import hashlib
import json
import math
import random
import statistics
from collections import defaultdict
from pathlib import Path

from .config import (
    BOOTSTRAP_SAMPLES, DB_PATH, MIN_N, STATS_JSON_PATH, STATS_MD_PATH,
)
from .db import connect


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


def _bootstrap_ci(values, seed_key, samples):
    if not values:
        return None
    if len(values) == 1:
        return [_round(values[0]), _round(values[0])]
    seed = int(hashlib.sha256(seed_key.encode("utf-8")).hexdigest()[:16], 16)
    rng = random.Random(seed)
    means = []
    length = len(values)
    for _ in range(samples):
        means.append(sum(values[rng.randrange(length)] for _ in range(length)) / length)
    return [_round(_percentile(means, 0.025)), _round(_percentile(means, 0.975))]


def _group_keys(row):
    yield ("family", row["family"])
    if row["subtype"]:
        yield ("subtype", f"{row['family']}|{row['subtype']}")
    if row["strength"]:
        yield ("strength", f"{row['family']}|{row['strength']}")
    if row["regime_at_emission"]:
        yield ("regime", f"{row['family']}|{row['regime_at_emission']}")


def _cell(rows, seed_key, min_n, bootstrap_samples):
    unpriceable = sum(1 for row in rows if row["status"] == "unpriceable")
    filled = [row for row in rows if row["status"] == "filled" and row["ret"] is not None]
    clusters = defaultdict(list)
    for row in filled:
        clusters[(row["ticker"], row["entry_session"])].append(row)

    cluster_rows = []
    for key, members in sorted(clusters.items()):
        raw_values = [float(row["ret"]) for row in members]
        universe_values = [float(row["univ_ret"]) for row in members if row["univ_ret"] is not None]
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
            "universe": statistics.fmean(universe_values) if universe_values else None,
            "signed": statistics.fmean(signed) if signed else None,
            "raw_only": all(row["asset_class"] in ("future", "index") for row in members),
        })

    n = len(cluster_rows)
    n_rows = len(filled)
    signed = [row["signed"] for row in cluster_rows if row["signed"] is not None]
    raw_only = [row["raw"] for row in cluster_rows if row["raw_only"]]
    result = {
        "status": "ready" if n >= min_n else "accumulating",
        "n": n,
        "n_rows": n_rows,
        "n_directional": len(signed),
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

    if signed:
        result.update({
            "hit_rate": _round(sum(value > 0 for value in signed) / len(signed)),
            "mean_signed_excess": _round(statistics.fmean(signed)),
            "median_signed_excess": _round(statistics.median(signed)),
            "p25_signed_excess": _round(_percentile(signed, 0.25)),
            "p75_signed_excess": _round(_percentile(signed, 0.75)),
            "mean_ci95": _bootstrap_ci(signed, seed_key, bootstrap_samples),
        })
    numerator = statistics.median(abs(row["raw"]) for row in cluster_rows)
    denominators = [abs(row["universe"]) for row in cluster_rows
                    if row["universe"] is not None]
    denominator = statistics.median(denominators) if denominators else None
    if denominator and denominator > 0:
        result["energy"] = _round(numerator / denominator)
    return result


def build_stats(db_path=DB_PATH, min_n=MIN_N, bootstrap_samples=BOOTSTRAP_SAMPLES):
    conn = connect(db_path)
    try:
        rows = conn.execute(
            """SELECT r.pipeline_version,s.family,s.subtype,s.strength,
                      s.regime_at_emission,s.ticker,s.direction,s.asset_class,
                      o.horizon,o.entry_session,o.exit_session,o.ret,o.excess,
                      o.univ_ret,o.status
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
        seed_key = f"{version}|{grain}|{key}|{horizon}"
        group["horizons"][str(horizon)] = _cell(
            members, seed_key, min_n, bootstrap_samples
        )

    return {
        "schema_version": "2.5",
        "as_of_session": latest_exit,
        "config": {
            "min_n": min_n,
            "bootstrap_samples": bootstrap_samples,
            "n_definition": "unique (ticker, entry_session) clusters",
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
            lines.append("| Horizon | n | Unpriceable | Result |")
            lines.append("|---:|---:|---:|---|")
            for horizon, cell in sorted(group["horizons"].items(), key=lambda item: int(item[0])):
                if cell["status"] == "accumulating":
                    result = f"n={cell['n']} (accumulating)"
                else:
                    pieces = []
                    if cell["hit_rate"] is not None:
                        pieces.append(f"hit {cell['hit_rate']:.1%}")
                        pieces.append(f"mean excess {cell['mean_signed_excess']:.2%}")
                        pieces.append(f"CI [{cell['mean_ci95'][0]:.2%}, {cell['mean_ci95'][1]:.2%}]")
                    if cell["energy"] is not None:
                        pieces.append(f"energy {cell['energy']:.2f}x")
                    raw_only = cell["raw_only"]
                    if raw_only["n"]:
                        pieces.append(
                            f"raw-only n={raw_only['n']}, mean {raw_only['mean_ret']:.2%}"
                        )
                    result = "; ".join(pieces) or "no directional statistic"
                lines.append(f"| +{horizon} | {cell['n']} | {cell['n_unpriceable']} | {result} |")
            lines.append("")
    lines.extend([
        "## Standing caveats",
        "",
        "- Evolving insider clusters can create more than one source record.",
        "- The watchlist baseline has survivorship bias.",
        "- This measures signals; it is not a strategy backtest and includes no costs, sizing, or fills.",
        "- Option anomalies are directionless until timestamped trade/NBBO classification exists.",
        "",
    ])
    return "\n".join(lines)


def write_stats(db_path=DB_PATH, json_path=STATS_JSON_PATH, md_path=STATS_MD_PATH,
                min_n=MIN_N, bootstrap_samples=BOOTSTRAP_SAMPLES):
    stats = build_stats(db_path, min_n=min_n, bootstrap_samples=bootstrap_samples)
    json_path, md_path = Path(json_path), Path(md_path)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(stats, sort_keys=True, indent=2, ensure_ascii=False,
                         allow_nan=False) + "\n"
    json_path.write_text(payload, encoding="utf-8", newline="\n")
    md_path.write_text(render_markdown(stats), encoding="utf-8", newline="\n")
    return stats
