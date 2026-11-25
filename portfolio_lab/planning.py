"""Bounded, offline open-loop planning research on supplied return scenarios.

This module estimates NO market model and makes NO live recommendation. It
compares search algorithms conditional on an operator-supplied finite scenario
distribution for one hypothetical risky asset and cash. Every action sequence
is identical across scenarios: it cannot select a different action after seeing
which future path was realized. ``replay_sequence`` is a separate retrospective
evaluation and never passes realized returns to a planner.

The objective is expected terminal log wealth, after an explicit proportional
one-way cost. Cash earns zero. Target weights are post-cost weights; fees are
funded from cash with no borrowing. The risky weight drifts after a return.
``hold`` means no transaction, not constant-weight daily rebalancing. Optional
terminal liquidation is charged consistently. There are no fixed ticket fees,
liquidity/impact models, taxes, FX, actual fills or distributional guarantees.

Timestamp checks validate supplied assertions, not historical availability.
The result is a simulator diagnostic, not empirical predictive accuracy.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product
import math
import random

from .data import digest, utc


VERSION = "portfolio-lab.planning.v1"
HOLD = "hold"
MAX_HORIZON = 20
MAX_SCENARIOS = 128
MAX_ACTIONS = 9
MAX_SIMULATIONS = 50_000
MAX_EXHAUSTIVE_SEQUENCES = 100_000
MAX_SCENARIO_STEPS = 10_000_000
_EPS = 1e-13


def _number(value, name, *, lower=None, upper=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    try:
        value = float(value)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be finite") from exc
    if (not math.isfinite(value) or (lower is not None and value < lower)
            or (upper is not None and value > upper)):
        raise ValueError(f"{name} is outside its finite supported range")
    return value


def _integer(value, name, lower, upper):
    if isinstance(value, bool) or not isinstance(value, int) or not lower <= value <= upper:
        raise ValueError(f"{name} must be an integer between {lower} and {upper}")
    return value


def _returns(values, *, length=None):
    if not isinstance(values, (list, tuple)) or not 1 <= len(values) <= MAX_HORIZON:
        raise ValueError("returns must be a bounded, nonempty sequence")
    result = tuple(_number(value, "return") for value in values)
    if any(value <= -1 for value in result):
        raise ValueError("returns must exceed -1; total-loss states are outside this prototype")
    if length is not None and len(result) != length:
        raise ValueError("all return paths must have exactly the declared horizon")
    return result


def validate_problem(problem):
    """Copy and normalize a finite scenario specification; reject unknown fields.

    Required fields are shown by ``synthetic_problem``. ``archived_forecast``
    requires the same provenance timestamps as synthetic data, but assertions
    alone do not prove a point-in-time forecast. Scenario probabilities must
    already sum to one; no empirical probability estimation happens here.
    """
    fields = {"schema_version", "scenario_kind", "scenario_id", "information_cutoff",
              "created_at", "decision_at", "data_through", "scenarios", "actions",
              "initial_weight", "cost_bps", "liquidate_at_end"}
    if not isinstance(problem, dict) or set(problem) != fields:
        raise ValueError("planning problem has missing or unknown fields")
    if problem["schema_version"] != VERSION:
        raise ValueError("unsupported planning schema")
    if problem["scenario_kind"] not in ("synthetic", "archived_forecast"):
        raise ValueError("scenario_kind must be synthetic or archived_forecast")
    scenario_id = problem["scenario_id"]
    if (not isinstance(scenario_id, str) or not scenario_id.strip()
            or len(scenario_id) > 256 or any(ord(c) < 32 for c in scenario_id)):
        raise ValueError("scenario_id must be a nonempty bounded identifier")
    times = {name: utc(problem[name]) for name in
             ("information_cutoff", "created_at", "decision_at", "data_through")}
    if not (times["data_through"] <= times["information_cutoff"]
            <= times["created_at"] <= times["decision_at"]):
        raise ValueError("scenario information and creation must precede the decision")
    actions = problem["actions"]
    if not isinstance(actions, (list, tuple)) or not 2 <= len(actions) <= MAX_ACTIONS:
        raise ValueError("actions must include hold and a bounded target-weight grid")
    if sum(action == HOLD for action in actions) != 1:
        raise ValueError("actions must contain hold exactly once")
    targets = [_number(action, "target weight", lower=0, upper=1)
               for action in actions if action != HOLD]
    if len(set(targets)) != len(targets):
        raise ValueError("target weights must be unique")
    initial = _number(problem["initial_weight"], "initial_weight", lower=0, upper=1)
    cost = _number(problem["cost_bps"], "cost_bps", lower=0, upper=9999)
    if not isinstance(problem["liquidate_at_end"], bool):
        raise ValueError("liquidate_at_end must be a boolean")
    rows = problem["scenarios"]
    if not isinstance(rows, (list, tuple)) or not 1 <= len(rows) <= MAX_SCENARIOS:
        raise ValueError("scenarios must be a bounded nonempty sequence")
    scenarios, horizon = [], None
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"probability", "returns"}:
            raise ValueError("each scenario needs exactly probability and returns")
        probability = _number(row["probability"], "probability", lower=0, upper=1)
        if probability == 0:
            raise ValueError("scenario probabilities must be strictly positive")
        path = _returns(row["returns"], length=horizon)
        horizon = len(path)
        # Bound floating-point wealth for every admissible allocation path.
        low = math.fsum(min(0.0, math.log1p(r)) for r in path)
        low += (horizon + 1) * math.log1p(-cost / 10_000)
        high = math.fsum(max(0.0, math.log1p(r)) for r in path)
        if low < -600 or high > 600:
            raise ValueError("scenario exceeds the supported finite wealth range")
        scenarios.append({"probability": probability, "returns": list(path)})
    total = math.fsum(row["probability"] for row in scenarios)
    if not math.isclose(total, 1.0, rel_tol=0, abs_tol=1e-12):
        raise ValueError("scenario probabilities must sum to one")
    # Correct only summation dust after the explicit normalization check.
    for row in scenarios:
        row["probability"] /= total
    return {**problem, "actions": [HOLD, *sorted(targets)], "scenarios": scenarios,
            "initial_weight": initial, "cost_bps": cost}


def _sequence(problem, sequence):
    horizon = len(problem["scenarios"][0]["returns"])
    if not isinstance(sequence, (list, tuple)) or len(sequence) != horizon:
        raise ValueError("an open-loop sequence needs one action per horizon step")
    result = []
    for action in sequence:
        if action != HOLD:
            action = _number(action, "sequence action", lower=0, upper=1)
        if action not in problem["actions"]:
            raise ValueError("sequence contains an action outside the frozen grid")
        result.append(action)
    return tuple(result)


def _rebalance(cash, asset, action, rate):
    if action == HOLD:
        return cash, asset, 0.0, 0.0
    wealth = cash + asset
    delta = action * wealth - asset
    if abs(delta) <= _EPS * wealth:
        return cash, asset, 0.0, 0.0
    if delta > 0:
        amount = delta / (1 + action * rate)
        fee = amount * rate
        cash, asset = cash - amount - fee, asset + amount
    else:
        amount = -delta / (1 - action * rate)
        fee = amount * rate
        cash, asset = cash + amount - fee, asset - amount
    if cash < -_EPS * wealth or asset < -_EPS * wealth:
        raise ValueError("scenario transaction would create borrowing or a short")
    return max(0.0, cash), max(0.0, asset), fee, amount


def _path(problem, sequence, returns):
    cash, asset = 1 - problem["initial_weight"], problem["initial_weight"]
    rate = problem["cost_bps"] / 10_000
    fees, turnover, peak, drawdown = 0.0, 0.0, 1.0, 0.0
    for action, change in zip(sequence, returns):
        cash, asset, fee, amount = _rebalance(cash, asset, action, rate)
        fees += fee
        turnover += amount
        drawdown = max(drawdown, 1 - (cash + asset) / peak)
        asset *= 1 + change
        wealth = cash + asset
        peak = max(peak, wealth)
        drawdown = max(drawdown, 1 - wealth / peak)
    if problem["liquidate_at_end"]:
        fee = asset * rate
        fees += fee
        turnover += asset
        cash, asset = cash + asset - fee, 0.0
        drawdown = max(drawdown, 1 - cash / peak)
    wealth = cash + asset
    if not math.isfinite(wealth) or wealth <= 0:
        raise ValueError("scenario wealth became nonpositive or nonfinite")
    return {"terminal_wealth": wealth, "log_growth": math.log(wealth),
            "net_return": wealth - 1, "cost_per_initial_wealth": fees,
            "turnover_per_initial_wealth": turnover, "max_drawdown": drawdown,
            "ending_risky_weight": asset / wealth}


def _score(problem, sequence):
    return math.fsum(row["probability"] * _path(problem, sequence, row["returns"])["log_growth"]
                     for row in problem["scenarios"])


def evaluate_sequence(problem, sequence):
    """Evaluate one shared sequence over the supplied forecast distribution."""
    problem = validate_problem(problem)
    sequence = _sequence(problem, sequence)
    rows = [{"probability": row["probability"], **_path(problem, sequence, row["returns"])}
            for row in problem["scenarios"]]
    metrics = {name: math.fsum(row["probability"] * row[name] for row in rows)
               for name in ("log_growth", "net_return", "cost_per_initial_wealth",
                            "turnover_per_initial_wealth", "max_drawdown")}
    return {"scenario_expected": metrics, "scenario_results": rows,
            "interpretation": "conditional model values, not observed performance"}


def replay_sequence(problem, sequence, realized_returns):
    """Retrospectively score an already chosen sequence; never optimize it."""
    problem = validate_problem(problem)
    sequence = _sequence(problem, sequence)
    returns = _returns(realized_returns, length=len(sequence))
    result = _path(problem, sequence, returns)
    return {"schema_version": VERSION, "problem_sha256": digest(problem),
            "actions": list(sequence), "realized_path_sha256": digest(list(returns)),
            "evaluation_kind": "retrospective_supplied_path",
            "point_in_time_availability_verified": False, **result}


def _work(problem, evaluations):
    horizon = len(problem["scenarios"][0]["returns"])
    if evaluations * horizon * len(problem["scenarios"]) > MAX_SCENARIO_STEPS:
        raise ValueError("requested search exceeds the bounded scenario-step budget")


def _report(problem, sequence, *, method, evaluations, simulations=None, seed=None):
    horizon = len(sequence)
    possible = len(problem["actions"]) ** horizon
    return {"schema_version": VERSION, "method": method,
            "problem_sha256": digest(problem), "scenario_kind": problem["scenario_kind"],
            "scenario_id": problem["scenario_id"], "horizon": horizon,
            "actions": list(sequence), "first_action": sequence[0],
            "seed": seed, "simulations": simulations,
            "evaluated_sequences": evaluations, "possible_sequences": possible,
            "scenario_path_evaluations": evaluations * len(problem["scenarios"]),
            "finite_model_optimum_proven": evaluations == possible,
            "information_structure": "one open-loop sequence shared by all scenarios",
            "research_only": True, "broker_execution": False,
            "predictive_accuracy_established": False,
            **evaluate_sequence(problem, sequence)}


def hold_plan(problem):
    problem = validate_problem(problem)
    sequence = (HOLD,) * len(problem["scenarios"][0]["returns"])
    return _report(problem, sequence, method="no_transaction_hold", evaluations=1)


def myopic_plan(problem):
    """Greedy one-step expected-log increments, with shared forecast actions.

    This is an open-loop greedy comparator, not path-specific perfect foresight.
    Only the final step includes a terminal liquidation charge when requested.
    """
    problem = validate_problem(problem)
    horizon = len(problem["scenarios"][0]["returns"])
    prefix = ()
    for step in range(horizon):
        partial = {**problem, "liquidate_at_end": problem["liquidate_at_end"] and step == horizon - 1}
        best, value = None, -math.inf
        for action in problem["actions"]:
            trial = prefix + (action,)
            score = _score(partial, trial)
            if score > value:
                best, value = trial, score
        prefix = best
    report = _report(problem, prefix, method="greedy_one_step_open_loop",
                     evaluations=len(problem["actions"]))
    report["prefix_evaluations"] = (horizon - 1) * len(problem["actions"])
    report["scenario_path_evaluations"] = horizon * len(problem["actions"]) * len(problem["scenarios"])
    report["finite_model_optimum_proven"] = horizon == 1
    return report


def exhaustive_plan(problem, *, max_sequences=MAX_EXHAUSTIVE_SEQUENCES):
    """Exact finite-model optimum over shared sequences, not a market oracle."""
    problem = validate_problem(problem)
    _integer(max_sequences, "max_sequences", 1, MAX_EXHAUSTIVE_SEQUENCES)
    horizon = len(problem["scenarios"][0]["returns"])
    count = len(problem["actions"]) ** horizon
    if count > max_sequences:
        raise ValueError("exhaustive search exceeds max_sequences")
    _work(problem, count)
    best, value = None, -math.inf
    for sequence in product(problem["actions"], repeat=horizon):
        score = _score(problem, sequence)
        if score > value:
            best, value = sequence, score
    return _report(problem, best, method="exhaustive_open_loop", evaluations=count)


@dataclass
class _Node:
    visits: int = 0
    total: float = 0.0
    children: dict = field(default_factory=dict)
    untried: list = field(default_factory=list)


def _search(problem, *, simulations, seed, exploration, tree):
    problem = validate_problem(problem)
    _integer(simulations, "simulations", 1, MAX_SIMULATIONS)
    _integer(seed, "seed", 0, 2 ** 32 - 1)
    exploration = _number(exploration, "exploration", lower=0, upper=100)
    _work(problem, simulations)
    actions = problem["actions"]
    horizon = len(problem["scenarios"][0]["returns"])
    rng = random.Random(seed)
    root = _Node(untried=list(range(len(actions))))
    cache, best, value = {}, None, -math.inf
    for iteration in range(simulations):
        node, nodes, indexes = root, [root], []
        if iteration == 0:
            # The no-transaction control consumes one of the declared rollouts.
            indexes = [0] * horizon
            if tree:
                for index in indexes:
                    if index not in node.children:
                        node.untried.remove(index)
                        node.children[index] = _Node(untried=list(range(len(actions))))
                    node = node.children[index]
                    nodes.append(node)
        elif not tree:
            indexes = [rng.randrange(len(actions)) for _ in range(horizon)]
        else:
            while len(indexes) < horizon:
                if node.untried:
                    index = node.untried.pop(rng.randrange(len(node.untried)))
                    child = _Node(untried=list(range(len(actions))))
                    node.children[index] = child
                    indexes.append(index)
                    nodes.append(child)
                    break
                index, child = max(node.children.items(), key=lambda item: (
                    item[1].total / item[1].visits
                    + exploration * math.sqrt(math.log(node.visits) / item[1].visits),
                    -item[0]))
                indexes.append(index)
                node = child
                nodes.append(node)
            indexes.extend(rng.randrange(len(actions)) for _ in range(horizon - len(indexes)))
        key = tuple(indexes)
        sequence = tuple(actions[index] for index in indexes)
        if key not in cache:
            cache[key] = _score(problem, sequence)
        score = cache[key]
        if score > value or (score == value and (best is None or key < best)):
            best, value = key, score
        if tree:
            for visited in nodes:
                visited.visits += 1
                visited.total += score
    report = _report(problem, tuple(actions[index] for index in best),
                     method="mcts_open_loop" if tree else "random_shooting_open_loop",
                     evaluations=len(cache), simulations=simulations, seed=seed)
    if tree:
        report["exploration"] = exploration
        report["root_diagnostics"] = [
            {"action": actions[index], "visits": child.visits,
             "mean_rollout_log_growth": child.total / child.visits}
            for index, child in sorted(root.children.items())]
        report["selection_rule"] = "best fully evaluated shared sequence; stable action-order tie break"
    return report


def mcts_plan(problem, *, simulations=512, seed=0, exploration=0.1):
    """Seeded UCT sequence optimization with uniform random completion.

    A rollout scores one complete sequence on ALL supplied scenarios. This
    integrates the declared finite distribution exactly rather than reporting
    Monte Carlo path samples as independent market evidence. UCT node averages
    mix searched completions; they are not calibrated confidence intervals.
    """
    return _search(problem, simulations=simulations, seed=seed, exploration=exploration, tree=True)


def random_shooting_plan(problem, *, simulations=512, seed=0):
    return _search(problem, simulations=simulations, seed=seed, exploration=0, tree=False)


def synthetic_problem():
    """Fabricated three-step example; no ticker, source request or real prices."""
    return {"schema_version": VERSION, "scenario_kind": "synthetic",
            "scenario_id": "fabricated-three-step-v1",
            "data_through": "2026-01-02T21:00:00Z",
            "information_cutoff": "2026-01-02T21:00:00Z",
            "created_at": "2026-01-02T21:01:00Z", "decision_at": "2026-01-02T21:02:00Z",
            "scenarios": [
                {"probability": 0.5, "returns": [0.02, 0.03, -0.02]},
                {"probability": 0.3, "returns": [-0.01, 0.02, -0.01]},
                {"probability": 0.2, "returns": [-0.03, -0.02, 0.02]}],
            "actions": [HOLD, 0.0, 0.5, 1.0], "initial_weight": 0.0,
            "cost_bps": 10.0, "liquidate_at_end": True}


def compare_planners(problem, *, simulations=512, seed=0, include_exhaustive=True):
    """Pure-data comparison for a CLI or notebook; performs no I/O."""
    problem = validate_problem(problem)
    if not isinstance(include_exhaustive, bool):
        raise ValueError("include_exhaustive must be a boolean")
    plans = {"hold": hold_plan(problem), "myopic": myopic_plan(problem),
             "random_shooting": random_shooting_plan(problem, simulations=simulations, seed=seed),
             "mcts": mcts_plan(problem, simulations=simulations, seed=seed)}
    if include_exhaustive:
        plans["exhaustive"] = exhaustive_plan(problem)
    result = {"schema_version": VERSION, "problem_sha256": digest(problem),
              "scenario_kind": problem["scenario_kind"], "plans": plans,
              "accuracy_claim": "none; supplied-scenario planning comparison only",
              "limitations": ["scenario probabilities are supplied, not calibrated here",
                              "open-loop schedules do not adapt to future observations",
                              "one hypothetical asset and cash; proportional costs only",
                              "no empirical signal-quality or market-return validation"]}
    if include_exhaustive:
        optimum = plans["exhaustive"]["scenario_expected"]["log_growth"]
        result["finite_model_log_growth_gap"] = {
            name: optimum - plan["scenario_expected"]["log_growth"]
            for name, plan in plans.items()}
    return result
