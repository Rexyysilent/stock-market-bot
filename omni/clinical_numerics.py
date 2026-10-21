"""Bounded clinical statistics with explicit, sentence-local analysis scopes.

This is deliberately not a general medical-language parser. A result needs a
named trial and endpoint in the same sentence. Unknown context stays null;
incomplete or ambiguous results remain review leads. Neither a small p-value
nor a confidence interval is an assertion that an endpoint was met.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re

from .evidence import span
from .identities import digest
from .clinical_scope import bind_scope, binding_identity

VERSION = "omni-clinical-numerics-1"
EXTENDED_VERSION = "omni-clinical-numerics-2"
PREDICATE = "clinical_numeric_result"
QUALIFIERS = ["reported_statistics_not_outcome_inference"]
MAX_SENTENCE = 6000
NUMBER = r"(?:\d+(?:\.\d+)?|\.\d+)"
TOKEN_END = r"(?!\w|[.,]\d|\s*%)"
PHASE = re.compile(r"\bphase\s+(1/2|2/3|1b|1|2b|2|3|4)\b", re.I)
STATISTIC = re.compile(r"\b(?:hazard\s+ratio|risk\s+ratio|relative\s+risk|odds\s+ratio|risk\s+difference|median\s+(?:survival|RFS|PFS|OS)|censored|unevaluable|serious\s+adverse|severe\s+adverse|grade\s+[1-5])\b|\b(?:HR|RR|OR)\s*(?:[=:]|of|\d)|\bp\s*[- ]?value\b|\bp\s*[=<>≤≥]|\b\d+\s*(?:/|of)\s*\d+\s+(?:patients|participants|eyes|events)\b", re.I)
HR = re.compile(r"\b(?:hazard\s+ratio(?:\s*\(HR\))?|HR)\s*(?:of|[=:])?\s*(?P<value>" + NUMBER + r")" + TOKEN_END, re.I)
CI = re.compile(r"(?P<level>" + NUMBER + r")\s*%\s*(?:CI|confidence\s+interval)\s*[,=:]?\s*[\[(]?\s*(?P<low>[+-]?" + NUMBER + r")\s*(?:[-–—]|to|,)\s*(?P<high>[+-]?" + NUMBER + r")" + TOKEN_END + r"(?:\s*[\])]?\s*(?P<unit>percentage\s+points))?", re.I)
EFFECT = re.compile(r"\b(?P<measure>risk\s+ratio|odds\s+ratio|risk\s+difference)\s*(?:of|[=:])?\s*(?P<value>[+-]?" + NUMBER + r")" + TOKEN_END + r"(?:\s+(?P<unit>percentage\s+points))?", re.I)
PVALUE = re.compile(r"\bp(?:\s*[- ]?value)?\s*(?P<operator>[=<>≤≥])\s*(?P<value>" + NUMBER + r")" + TOKEN_END, re.I)
COUNT = re.compile(r"\b(?P<role>treatment|comparator)\s+(?P<measure>events|responders)\s*:\s*(?P<count>\d+)\s*(?:/|of)\s*(?P<denominator>\d+)\s+(?P<unit>patients|participants|eyes|events)\b", re.I)
ENDPOINTS = (
    ("distant_metastasis_free_survival", r"distant[- ]metastasis[- ]free\s+survival|DMFS"),
    ("recurrence_free_survival", r"recurrence[- ]free\s+survival|RFS"),
    ("overall_survival", r"overall\s+survival|OS"),
    ("progression_free_survival", r"progression[- ]free\s+survival|PFS"),
)
REQUIRED_SCOPE = ("trial_id", "endpoint", "analysis_population", "analysis_type", "timepoint")


def phase_compatible(reported, registered):
    """Registry PHASE2 may describe a reported Phase 2b; retain the reported b."""
    if registered is None:
        return True
    return reported == registered or (reported, registered) in {
        ("PHASE1B", "PHASE1"), ("PHASE2B", "PHASE2")}


def semantic(value):
    """Remove proof locations, retaining every value, qualifier and missingness."""
    if isinstance(value, dict):
        result = {k: semantic(v) for k, v in value.items()
                  if k not in ("support", "result_id", "analysis_scope_id", "table_proof", "source_form")}
        if "scope_binding" in value:
            result["scope_binding"] = binding_identity(value["scope_binding"])
            result["scope"] = canonical_scope(value["scope"], value["scope_binding"])
        if value.get("contract") in (VERSION, EXTENDED_VERSION):
            # Adding a proof-bearing source form must not count the same
            # statistic twice when prose and a table describe one result.
            result["contract"] = VERSION
        return result
    if isinstance(value, list):
        return [semantic(v) for v in value]
    return value


def canonical_scope(scope, binding):
    result = semantic(scope)
    if binding:
        for key, row in binding["bindings"].items():
            if row and key in result:
                result[key] = {"value": row["canonical_id"]}
        result["clinical_version"] = binding_identity(binding)
    return result


def result_projection(claims):
    """One current result per semantic ID, with a deterministic support path."""
    found = {}
    for claim in sorted(claims, key=lambda c: c.get("claim_id", "")):
        if claim.get("predicate") == PREDICATE and isinstance(claim.get("numeric_result"), dict):
            result = claim["numeric_result"]
            found.setdefault(result.get("result_id"), result)
    return [found[k] for k in sorted(found) if isinstance(k, str)] or None


def _decimal(raw):
    if len(raw) > 24:
        raise ValueError("numeric_token_too_long")
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError("invalid_decimal") from exc
    if not value.is_finite():
        raise ValueError("nonfinite_statistic")
    return format(value.normalize(), "f")


def _context_text(raw):
    # Preserve the original spelling in support.text. Presentation differences
    # cannot split otherwise identical analysis identities and hide a conflict.
    return " ".join(raw.casefold().split())


def _time_unit(raw):
    return raw.lower().rstrip("s")


def parse_sentence(doc, start, end, registry, *, known_by):
    """Return at most one fully scoped numeric result, or explicit abstentions.

    Supports labelled context fields and a few explicit prose forms. No context
    crosses a sentence, paragraph, trial, endpoint, analysis or source revision.
    Returned partial candidates are leads only and cannot be approved as claims.
    """
    text = doc["text"][start:end]
    if not STATISTIC.search(text):
        return {"results": [], "abstentions": []}
    support = span(doc, start, end)

    def atom(value, low, high):
        return {"value": value, "support": span(doc, start + low, start + high)}

    def matched(match, group, normalize=None):
        raw = match[group]
        return atom(normalize(raw) if normalize else raw, *match.span(group))

    def abstain(reason, candidate=None):
        return {"results": [], "abstentions": [{"reason": reason, "support": support,
                "candidate": candidate, "human_review_status": "pending", "signal_eligible": False}]}

    if len(text) > MAX_SENTENCE:
        return abstain("numeric_sentence_limit")
    if re.search(r"\d[\d.]{24,}", text):
        return abstain("numeric_token_too_long")
    paragraph_start = doc["text"].rfind("\n", 0, start) + 1
    paragraph_end = doc["text"].find("\n", end)
    paragraph_end = paragraph_end if paragraph_end >= 0 else len(doc["text"])
    surrounding = doc["text"][paragraph_start:start] + doc["text"][end:paragraph_end]
    if re.search(r"\b(?:censored|unevaluable|non[- ]evaluable|missing\s+assessments?|no\s+alpha|not\s+adjusted\s+for\s+multiplicity)\b", surrounding, re.I):
        return abstain("numeric_adjacent_scope_unsupported")
    if re.search(r"\b(?:censored|unevaluable|non[- ]evaluable|missing\s+assessments?|discontinued\s+patients)\b", text, re.I):
        return abstain("numeric_population_disposition_unsupported")
    if re.search(r"\b(?:serious|severe)\s+(?:adverse|events?)|\bgrade\s*[1-5]\b|\b(?:SAE|TEAE)s?\b", text, re.I):
        return abstain("numeric_safety_scope_unsupported")
    if re.search(r"\b(?:relative\s+risk|median\s+(?:survival|RFS|PFS|OS))\b|\b(?:RR|OR)\s*[=<>]", text, re.I):
        return abstain("numeric_effect_measure_unsupported")
    # The source remains data. Unsupported encodings, tables, approximate values,
    # inequalities on HR and mathematical transformations are never repaired.
    if re.search(r"[|\t\n]|<[^>]+>|\b(?:approximately|about|roughly|estimated|rounded|respectively|(?:unadjusted|adjusted)\s+(?:hazard|risk|odds))\b|[~≈]|\b(?:HR|hazard\s+ratio)\s*[<>≤≥]", text, re.I):
        return abstain("numeric_grammar_unsupported")
    if re.search(r"\b(?:will|would|could|may|expects?|plans?|anticipated|assumed|hypothetical|target\s+HR|power\s+calculation|never|unreported|unavailable|not\s+(?:reported|available|observed|measured)|withdrawn|retracted|incorrect|erroneous|superseded|not\s+\d)\b", text, re.I):
        return abstain("numeric_assertion_not_established")
    if re.search(r"\b(?:no|not|neither|without)\s+(?:an?\s+)?(?:hazard\s+ratio|risk\s+ratio|odds\s+ratio|risk\s+difference|HR|p[- ]?value|treatment\s+(?:events|responders)|comparator\s+(?:events|responders))\b", text, re.I):
        return abstain("numeric_assertion_not_established")
    resolved = registry.resolve(text, known_by=known_by)
    mentions = [m for m in resolved["mentions"] if m["kind"] == "trial"]
    trial_ids = {m["entity_id"] for m in mentions}
    explicit = set(re.findall(r"\bNCT\d{8}\b", text, re.I))
    if len(trial_ids) != 1 or any("trial:" + n.upper() not in trial_ids for n in explicit):
        return abstain("numeric_trial_ambiguous_or_unknown")
    trial_id = next(iter(trial_ids))
    first_trial = next(m for m in mentions if m["entity_id"] == trial_id)
    scope = {"trial_id": atom(trial_id, first_trial["start"], first_trial["end"])}
    issues = set()

    def field(label):
        hits = list(re.finditer(r"(?:^|;)\s*(?:" + label + r")\s*:\s*(?P<value>[^;.!?\n]{1,180})", text, re.I))
        if len(hits) > 1:
            issues.add("numeric_context_ambiguous")
        if len(hits) != 1:
            return None
        match = hits[0]
        if match.end("value") < len(text) and text[match.end("value")] not in ";.!?\n":
            issues.add("numeric_context_limit")
        raw = match["value"]
        leading = len(raw) - len(raw.lstrip())
        trimmed = raw.strip()
        if not trimmed:
            return None
        if _context_text(trimmed) in ("unknown", "unspecified", "not stated", "not reported", "none", "null", "n/a", "na", "pending", "tbd"):
            return None
        if STATISTIC.search(trimmed) or re.search(r"\b(?:HR|CI|confidence\s+interval)\b", trimmed, re.I):
            issues.add("numeric_context_contains_statistic")
        if label != "multiplicity" and re.search(r"\b(?:not|neither|without|except|excluding)\b", trimmed, re.I):
            issues.add("numeric_context_negation_unsupported")
        return atom(_context_text(trimmed), match.start("value") + leading, match.start("value") + leading + len(trimmed))

    phases = list(PHASE.finditer(text))
    if len(phases) > 1:
        issues.add("numeric_phase_ambiguous")
    scope["reported_phase"] = matched(phases[0], 1, lambda v: "PHASE" + v.upper()) if len(phases) == 1 else None
    if (scope["reported_phase"] and getattr(registry, "clinical_catalog", None) is None
            and not phase_compatible(scope["reported_phase"]["value"], registry.entities[trial_id].get("phase"))):
        issues.add("numeric_trial_phase_mismatch")
    scope["indication"] = field("indication")

    endpoints = []
    for code, pattern in ENDPOINTS:
        for match in re.finditer(r"\b(?:" + pattern + r")\b", text, re.I):
            endpoints.append((code, match))
    codes = {code for code, _ in endpoints}
    if len(codes) > 1:
        issues.add("numeric_endpoint_ambiguous")
    labelled_endpoint = field("endpoint")
    if len(codes) == 1:
        code, match = sorted(endpoints, key=lambda row: row[1].start())[0]
        scope["endpoint"] = atom(code, match.start(), match.end())
        if labelled_endpoint and not re.fullmatch(
                next(pattern for c, pattern in ENDPOINTS if c == code) + r"(?:\s*\([A-Z]+\))?", labelled_endpoint["value"], re.I):
            issues.add("numeric_endpoint_ambiguous")
    else:
        scope["endpoint"] = labelled_endpoint if not codes else None
    rank = field(r"rank|endpoint\s+rank")
    rank_matches = list(re.finditer(r"\b(primary|secondary|exploratory)\s+(?:endpoint\s+(?:of\s+)?)?(?=recurrence[- ]free\s+survival|distant[- ]metastasis[- ]free\s+survival|overall\s+survival|progression[- ]free\s+survival|RFS\b|DMFS\b|OS\b|PFS\b)", text, re.I))
    if rank is None and len(rank_matches) == 1:
        rank = matched(rank_matches[0], 1, str.lower)
    elif len(rank_matches) > 1:
        issues.add("numeric_context_ambiguous")
    elif rank and rank_matches and rank["value"].lower() != rank_matches[0][1].lower():
        issues.add("numeric_context_ambiguous")
    if rank and rank["value"].lower() not in ("primary", "secondary", "exploratory"):
        issues.add("numeric_rank_unsupported")
    if rank:
        rank["value"] = rank["value"].lower()
    scope["endpoint_rank"] = rank
    scope["analysis_population"] = field(r"population|analysis\s+population")
    populations = list(re.finditer(r"\b(?:modified\s+intention[- ]to[- ]treat|intention[- ]to[- ]treat|per[- ]protocol|safety|efficacy[- ]evaluable)\s+population\b", text, re.I))
    if scope["analysis_population"] is None:
        if len(populations) == 1:
            m = populations[0]
            scope["analysis_population"] = atom(_context_text(m[0]).removesuffix(" population"), m.start(), m.end())
        elif populations:
            issues.add("numeric_population_ambiguous")
    if scope["analysis_population"] and any(_context_text(m[0]).removesuffix(" population") !=
            scope["analysis_population"]["value"].removesuffix(" population") for m in populations):
        issues.add("numeric_population_ambiguous")
    if scope["analysis_population"]:
        scope["analysis_population"]["value"] = scope["analysis_population"]["value"].removesuffix(" population")
    scope["treatment_arm"] = field(r"treatment(?:\s+arm)?")
    scope["comparison_arm"] = field(r"comparator|comparison\s+arm")
    pairs = list(re.finditer(r"\bfor\s+(?P<treatment>[A-Za-z][A-Za-z0-9 +()/-]{1,90})\s+(?:versus|vs\.)\s+(?P<comparator>[A-Za-z][A-Za-z0-9 +()/-]{1,90}?)\s+(?:had|showed|yielded|was|resulted)\b", text, re.I))
    if scope["treatment_arm"] is None and scope["comparison_arm"] is None:
        if len(pairs) == 1:
            scope["treatment_arm"] = matched(pairs[0], "treatment", _context_text)
            scope["comparison_arm"] = matched(pairs[0], "comparator", _context_text)
        elif pairs:
            issues.add("numeric_arm_ambiguous")
    if pairs:
        if len(pairs) != 1 or any(scope[key] and scope[key]["value"] != _context_text(pairs[0][group])
                for key, group in (("treatment_arm", "treatment"), ("comparison_arm", "comparator"))):
            issues.add("numeric_arm_ambiguous")
    scope["timepoint"] = None
    timepoints = list(re.finditer(r"(?:^|;)\s*timepoint\s*:\s*(?P<value>" + NUMBER + r")\s*(?P<unit>days?|weeks?|months?|years?)\b", text, re.I))
    # A prose assessment must immediately modify the named endpoint. A dosing
    # schedule or treatment-arm description containing 'at' is not a timepoint.
    endpoint_end = scope["endpoint"]["support"]["end"] - start if scope["endpoint"] else -1
    if endpoint_end >= 0:
        timepoints += [m for m in re.finditer(r"\bat\s+(?P<value>" + NUMBER + r")\s*(?P<unit>days?|weeks?|months?|years?)\b", text, re.I)
                       if m.start() >= endpoint_end and re.fullmatch(r"\s+(?:endpoint\s+)?", text[endpoint_end:m.start()])]
    if len(timepoints) == 1:
        m = timepoints[0]
        scope["timepoint"] = {"value": matched(m, "value", _decimal), "unit": matched(m, "unit", _time_unit)}
    elif timepoints:
        issues.add("numeric_timepoint_ambiguous")
    # Follow-up duration is a different field; never use it as assessment time.
    followup = list(re.finditer(r"\bmedian\s+follow[- ]up(?:\s+of|\s*:)\s*(?P<value>" + NUMBER + r")\s*(?P<unit>days?|weeks?|months?|years?)\b", text, re.I))
    scope["follow_up"] = None
    if len(followup) == 1:
        m = followup[0]
        scope["follow_up"] = {"value": matched(m, "value", _decimal), "unit": matched(m, "unit", _time_unit)}
    elif followup:
        issues.add("numeric_timepoint_ambiguous")
    scope["data_cutoff"] = field(r"data\s+cutoff")
    if scope["data_cutoff"]:
        from .identities import time_interval
        if time_interval(scope["data_cutoff"]["value"])["precision"] != "date":
            issues.add("numeric_cutoff_precision_unsupported")
    analyses = list(re.finditer(r"\b(?P<type>post[- ]?hoc|ad\s+hoc|pre[- ]?specified|exploratory|descriptive)\s+(?:analysis|analyses)\b|\banalysis\s*:\s*(?P<label>post[- ]?hoc|ad\s+hoc|pre[- ]?specified|exploratory|descriptive)\b", text, re.I))
    scope["analysis_type"] = None
    analysis_label = field("analysis")
    if analysis_label and not re.fullmatch(
            r"post[- ]?hoc|ad\s+hoc|pre[- ]?specified|exploratory|descriptive",
            analysis_label["value"], re.I):
        # Matching the first word of a compound label would discard scope.
        issues.add("numeric_analysis_unsupported")
    if len(analyses) == 1:
        m = analyses[0]
        clause_start = text.rfind(";", 0, m.start()) + 1
        clause_end = text.find(";", m.end())
        clause = text[clause_start:clause_end if clause_end >= 0 else len(text)]
        if re.search(r"\b(?:not|never|neither|no|without)\b", clause, re.I):
            issues.add("numeric_analysis_not_established")
        g = "type" if m["type"] else "label"
        normalize = lambda v: {"posthoc": "post_hoc", "adhoc": "post_hoc", "prespecified": "prespecified"}.get(re.sub(r"[- ]", "", v.lower()), v.lower())
        scope["analysis_type"] = matched(m, g, normalize)
    elif analyses:
        issues.add("numeric_analysis_ambiguous")
    scope["analysis_milestone"] = field(r"analysis\s+milestone")
    cohort = field("cohort")
    if cohort is not None:
        scope["cohort"] = cohort
    multiplicity = field("multiplicity")
    if multiplicity:
        if multiplicity["value"] not in ("no alpha allocated", "not adjusted for multiplicity"):
            issues.add("numeric_multiplicity_scope_unsupported")
        else:
            scope["multiplicity"] = multiplicity
    elif re.search(r"\b(?:no\s+alpha|alpha\s+(?:was\s+)?not\s+allocated|not\s+adjusted\s+for\s+multiplicity)\b", text, re.I):
        issues.add("numeric_multiplicity_scope_unsupported")

    statistics = {"hazard_ratio": None, "confidence_interval": None, "p_value": None, "absolute_counts": []}
    hrs, cis, ps, counts, effects = (list(pattern.finditer(text)) for pattern in (HR, CI, PVALUE, COUNT, EFFECT))
    for match in hrs + cis + ps + counts + effects:
        # Closing interval/parenthesis punctuation cannot conceal a unit after
        # the statistic, e.g. CI [0.3, 0.9] percent is not a ratio interval.
        suffix = re.sub(r"^\s*[\])]+\s*", "", text[match.end():])
        if not re.match(r"\s*(?:[,;(\[.!?]|$)", suffix):
            issues.add("numeric_unit_or_suffix_unsupported")
    if len(hrs) + len(effects) > 1 or len(cis) > 1 or len(ps) > 1 or len(counts) > 2:
        issues.add("numeric_statistic_ambiguous")
    try:
        if len(hrs) == 1:
            m = hrs[0]
            hr = Decimal(m["value"])
            if not 0 < hr <= 1000:
                issues.add("numeric_hazard_ratio_invalid")
            statistics["hazard_ratio"] = {"estimate": matched(m, "value", _decimal),
                "unit": atom("ratio", m.start(), m.start("value"))}
        if len(effects) == 1:
            m = effects[0]
            measure = re.sub(r"\s+", "_", m["measure"].lower())
            number = Decimal(m["value"])
            if measure == "risk_difference":
                if not m["unit"] or not -100 <= number <= 100:
                    issues.add("numeric_effect_unit_or_value_invalid")
                unit = matched(m, "unit", lambda _: "percentage_points") if m["unit"] else None
            else:
                if m["unit"] or not 0 < number <= 1000:
                    issues.add("numeric_effect_unit_or_value_invalid")
                unit = matched(m, "measure", lambda _: "ratio")
            statistics["effect_measure"] = {"measure": matched(m, "measure", lambda _: measure),
                                             "estimate": matched(m, "value", _decimal), "unit": unit}
        if len(cis) == 1:
            m = cis[0]
            level, low, high = (Decimal(m[g]) for g in ("level", "low", "high"))
            if len(hrs) + len(effects) != 1:
                issues.add("numeric_ci_estimand_unknown")
            difference = len(effects) == 1 and "difference" in effects[0]["measure"].lower()
            if not (0 < level < 100 and ((-100 <= low <= high <= 100) if difference else (0 <= low <= high <= 1000))):
                issues.add("numeric_confidence_interval_invalid")
            estimates = hrs + effects
            if len(estimates) == 1 and not low <= Decimal(estimates[0]["value"]) <= high:
                issues.add("numeric_estimate_outside_interval")
            if bool(m["unit"]) != difference:
                issues.add("numeric_ci_unit_unsupported")
            statistics["confidence_interval"] = {"level_percent": matched(m, "level", _decimal),
                "lower": matched(m, "low", _decimal), "upper": matched(m, "high", _decimal),
                "unit": matched(m, "unit", lambda _: "percentage_points") if difference and m["unit"] else
                        atom("ratio", estimates[0].start(), estimates[0].start("value")) if len(estimates) == 1 else None}
            ci_side = re.search(r"\b(one|two)[- ]sided\s*$", text[:m.start()], re.I)
            if ci_side:
                statistics["confidence_interval"]["sidedness"] = atom(ci_side[1].lower() + "_sided",
                    ci_side.start(), ci_side.end())
        if len(ps) == 1:
            m = ps[0]
            value = Decimal(m["value"])
            if not 0 <= value <= 1 or (m["operator"] in ("<", "≤") and value == 0) or (m["operator"] in (">", "≥") and value == 1):
                issues.add("numeric_p_value_invalid")
            # Restrict p-value qualifiers to their clause; do not borrow a
            # one/two-sided label from another statistic or a confidence interval.
            clause_start = max(text.rfind(";", 0, m.start()), text.rfind("(", 0, m.start())) + 1
            prefix = text[clause_start:m.start()]
            side_pattern = r"\b(one|two)[- ]sided\b"
            adjustment_pattern = r"\b(nominal|unadjusted|multiplicity[- ]adjusted|adjusted)\b"
            sides = list(re.finditer(side_pattern, prefix, re.I))
            adjustments = list(re.finditer(adjustment_pattern, prefix, re.I))
            clause_end = text.find(";", m.end())
            suffix = text[m.end():clause_end if clause_end >= 0 else len(text)]
            suffix_sides = list(re.finditer(side_pattern, suffix, re.I))
            suffix_adjustments = list(re.finditer(adjustment_pattern, suffix, re.I))
            if len(sides) + len(suffix_sides) > 1 or len(adjustments) + len(suffix_adjustments) > 1:
                issues.add("numeric_p_qualifier_ambiguous")
            elif (suffix_sides or suffix_adjustments
                  or ((sides or adjustments) and re.search(
                      r"\b(?:HR|hazard\s+ratio|CI|confidence\s+interval)\b", prefix, re.I))):
                # The supported form puts qualifiers immediately before p.
                # Preserve unsupported associations as leads instead of erasing
                # suffix qualifiers or borrowing one from an earlier interval.
                issues.add("numeric_p_qualifier_unsupported")
            sidedness = atom(sides[0][1].lower() + "_sided", clause_start + sides[0].start(), clause_start + sides[0].end()) if len(sides) == 1 else None
            adjustment = atom(adjustments[0][0].lower(), clause_start + adjustments[0].start(), clause_start + adjustments[0].end()) if len(adjustments) == 1 else None
            statistics["p_value"] = {"value": matched(m, "value", _decimal),
                "operator": matched(m, "operator"), "sidedness": sidedness, "adjustment": adjustment}
            if (multiplicity and multiplicity["value"] == "not adjusted for multiplicity"
                    and adjustment and adjustment["value"] not in ("nominal", "unadjusted")):
                issues.add("numeric_multiplicity_conflict")
            methods = list(re.finditer(r"\b(Holm|Bonferroni)[- ]adjusted\b", prefix, re.I))
            unknown_methods = re.findall(r"\b([A-Za-z][A-Za-z-]*)[- ]adjusted\b", prefix, re.I)
            if any(name.casefold() not in ("holm", "bonferroni", "multiplicity", "one-sided", "two-sided", "sided") for name in unknown_methods):
                issues.add("numeric_multiplicity_method_unsupported")
            if re.search(r"\b(?:stratified|log[- ]rank|Wald|Cox|Fisher)\b", prefix, re.I):
                issues.add("numeric_statistical_test_unsupported")
            if methods:
                if len(methods) != 1 or len(adjustments) != 1:
                    issues.add("numeric_p_adjustment_method_ambiguous")
                else:
                    method = methods[0]
                    statistics["p_value"]["adjustment_method"] = atom(method[1].lower(),
                        clause_start + method.start(1), clause_start + method.end(1))
            if re.search(r"\b(?:Hochberg|Benjamini|FDR|alpha\s+spending|hierarch\w*)\b", text, re.I):
                issues.add("numeric_multiplicity_method_unsupported")
        roles = set()
        for m in counts:
            role = m["role"].lower()
            count, denominator = int(m["count"]), int(m["denominator"])
            if max(len(m["count"]), len(m["denominator"])) > 9 or denominator <= 0 or count > denominator:
                issues.add("numeric_count_denominator_invalid")
            if role in roles:
                issues.add("numeric_arm_count_ambiguous")
            roles.add(role)
            # Events per patients are not supported by this bounded proportion
            # grammar. Recurrent events may exceed participants and need a rate.
            if m["unit"].lower() == "events":
                issues.add("numeric_count_unit_unsupported")
            statistics["absolute_counts"].append({"arm_role": matched(m, "role", str.lower),
                "measure": matched(m, "measure", str.lower), "count": matched(m, "count", _decimal),
                "denominator": matched(m, "denominator", _decimal), "denominator_unit": matched(m, "unit", str.lower)})
    except (ValueError, InvalidOperation):
        issues.add("numeric_value_invalid")

    # Any unconsumed numeric/statistic expression is a reason to abstain, not a
    # licence to silently drop a competing result (e.g. a negative or % HR).
    residual = list(text)
    spans = [m.span() for m in hrs + cis + ps + counts + effects + phases + timepoints + followup]
    spans.extend((m["start"], m["end"]) for m in mentions)
    for key in ("indication", "analysis_population", "treatment_arm", "comparison_arm", "data_cutoff", "analysis_milestone", "endpoint", "cohort"):
        if scope.get(key):
            s = scope[key]["support"]
            spans.append((s["start"] - start, s["end"] - start))
    for low, high in spans:
        residual[low:high] = " " * (high - low)
    remaining = "".join(residual)
    if re.search(r"\d|\b(?:hazard|risk|odds)\s+ratio\b|\brisk\s+difference\b|\bHR\b|\bCI\b|\bconfidence\s+interval\b|\bp\s*[=<>≤≥]", remaining, re.I):
        issues.add("numeric_unparsed_competing_value")
    if not hrs and not ps and not counts and not effects:
        issues.add("numeric_statistic_unsupported")
    required = list(REQUIRED_SCOPE)
    if hrs or counts or effects:
        required += ["treatment_arm", "comparison_arm"]
    missing = [field_name for field_name in required if not scope.get(field_name)]
    if scope["treatment_arm"] and scope["comparison_arm"] and scope["treatment_arm"]["value"].casefold() == scope["comparison_arm"]["value"].casefold():
        issues.add("numeric_comparison_arms_identical")
    binding, binding_issues = bind_scope(scope, doc, registry, known_by=known_by)
    issues.update(binding_issues)
    extended = bool(effects or cohort or multiplicity or binding or
                    any("adjustment_method" in v for v in statistics.values() if isinstance(v, dict)) or
                    statistics["confidence_interval"] and "sidedness" in statistics["confidence_interval"])
    result = {"contract": EXTENDED_VERSION if extended else VERSION, "scope": scope, "statistics": statistics,
              "human_review_status": "pending", "signal_eligible": False, "support": support,
              "missing_requirements": missing}
    if binding is not None:
        result["scope_binding"] = binding
    result["analysis_scope_id"] = "analysis:" + digest(canonical_scope(scope, binding))
    result["result_id"] = "numeric:" + digest(semantic(result))
    if issues:
        return abstain(sorted(issues)[0], dict(result, abstention_reasons=sorted(issues)))
    if missing:
        return abstain("numeric_scope_incomplete", result)
    return {"results": [result], "abstentions": []}
