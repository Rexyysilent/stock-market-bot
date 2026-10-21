"""Exact, narrowly scoped contract amounts; no accounting or revenue inference."""
from decimal import Decimal, InvalidOperation
import re

from .evidence import span
from .identities import digest, time_interval

CONTRACT_AMOUNTS = {'contract_ceiling_amount', 'contract_option_amount', 'contract_obligation_amount'}
AMOUNT = re.compile(
    r'(?<![-\w])(?:US\$|\bUSD\b\s*\$?)\s*'
    r'((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)'
    r'(?:\s*(thousand|million|billion))?\b(?![.,]\d)', re.I)
POSTPOSED_BOUND = re.compile(
    r'\s*[,(:]?\s*(?:or\s+(?:more|less)|(?:at\s+(?:a\s+)?)?(?:minimum|maximum))\b', re.I)


def contract_amounts(text, *, stage):
    """One explicit USD amount and one stage, with no competing numeric scope.

    Bare dollar signs, currency conversions, multiple amounts, dates/durations,
    arithmetic, unsupported abbreviations and conflicting stages are left for
    review. Preserve the original sentence as the evidence span.
    """
    if stage not in ('ceiling', 'option', 'obligation'):
        return []
    matches = list(AMOUNT.finditer(text))
    if len(matches) != 1:
        return []
    match = matches[0]
    if POSTPOSED_BOUND.match(text, match.end()):
        return []
    residual = text[:match.start()] + text[match.end():]
    if re.search(r'\d|[$€£%]|\b(?:EUR|GBP|CAD|AUD|JPY)\b', residual, re.I):
        return []
    if re.match(r'\s*(?:m|bn|b|k)\b', text[match.end():], re.I):
        return []
    if re.search(r'\b(?:approximately|about|roughly|estimated|estimate|less than|more than|at least|at most|up to|no more than|no less than|over|under|exceeds|minimum|between|negative|minus|plus|per|annually|annual|years?|months?|monthly|weekly|daily|hours?|conditional|contingent|subject to|if)\b|[<>~≤≥≈]', text, re.I):
        return []
    if re.search(r'\b(?:not|no|never|neither|denied|denies|expected|projected|planned|potential|withdrawn|revoked|terminated)\b|\bto\s+be\s+(?:obligated|received|raised|paid|exercised)\b', text, re.I):
        return []
    roles = {
        'ceiling': bool(re.search(r'\b(?:ceiling|up to|maximum)\b', text, re.I)),
        'option': bool(re.search(r'\boptions?\b', text, re.I)),
        'obligation': bool(re.search(r'\b(?:obligated|obligation)\b', text, re.I)),
    }
    if sum(roles.values()) != 1 or not roles[stage]:
        return []
    scale = {'thousand': 1000, 'million': 1000000, 'billion': 1000000000, None: 1}
    raw = match[1].replace(',', '')
    if len(raw) > 18:
        return []
    amount = Decimal(raw) * scale[match[2].lower() if match[2] else None]
    if amount < 0 or amount > Decimal('1000000000000000') or amount != amount.quantize(Decimal('.01')):
        return []
    return [('contract_' + stage + '_amount', format(amount.normalize(), 'f'))]


# The legacy function above remains an intentionally small compatibility path.
# New atoms have a separate discriminator and never supply a legacy scalar.
SCOPED_VERSION = 'omni-scoped-amount-1'
SCOPED_PREDICATE = 'scoped_amount'
SCOPED_QUALIFIERS = ['source_scoped_amount_no_aggregation_or_revenue_inference']
MONEY_CUE = re.compile(r'\b(?:USD|EUR|GBP|CAD|AUD|JPY|CHF|CNY|INR)\b|US\$|[$€£]', re.I)
MONEY = re.compile(
    r'(?<![\w])(?P<currency>US\$|USD\b|EUR\b|GBP\b|CAD\b|AUD\b|JPY\b|CHF\b|CNY\b|INR\b|[$€£])\s*'
    r'(?P<number>[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)'
    r'(?:\s*(?P<scale>thousand|million|billion))?(?![\w]|[.,]\d)', re.I)
ROLE_PATTERNS = {
    'ceiling': r'\b(?:ceiling|maximum\s+(?:contract\s+)?value)\b',
    'option': r'\boptions?\b',
    'obligation': r'\b(?:obligated|obligation(?:\s+change)?|deobligation)\b',
    'financing_authority': r'\b(?:financing\s+authority|ATM|at[- ]the[- ]market|shelf)\b',
    'gross_proceeds': r'\bgross\s+proceeds\b',
    'net_proceeds': r'\bnet\s+proceeds\b',
}
CONNECTOR = re.compile(r';|,\s*(?=including\b|plus\b|and\b)|\b(?:including|plus|against|and)\b', re.I)
BOUND = re.compile(r'(?P<qualifier>less\s+than|more\s+than|at\s+most|at\s+least|up\s+to|'
                   r'no\s+more\s+than|no\s+less\s+than|approximately|about|roughly|[<>]=?|[≤≥≈~])\s*$', re.I)


def amount_semantic(value):
    """Semantic values and unknowns, without evidence paths or generated IDs."""
    if isinstance(value, dict):
        return {k: amount_semantic(v) for k, v in value.items()
                if k not in ('support', 'amount_id', 'amount_scope_id', 'source_evidence_id', 'source_revision_id')}
    if isinstance(value, list):
        return [amount_semantic(v) for v in value]
    return value


def amount_projection(claims):
    """Deterministic display payload; no sums, conversion or default currency."""
    found = {}
    for claim in sorted(claims, key=lambda c: c.get('claim_id', '')):
        atom = claim.get('amount_atom')
        if claim.get('predicate') == SCOPED_PREDICATE and isinstance(atom, dict):
            key = atom.get('amount_id')
            if isinstance(key, str):
                found.setdefault(key, atom)
    return [found[k] for k in sorted(found)] or None


def parse_amount_sentence(doc, start, end):
    """Parse explicit money atoms in a bounded complete source sentence.

    Roles must attach locally to each amount. Unknown currency, period and
    identity stay null. Ambiguous associations refuse the complete sentence;
    neither a caller-supplied stage nor a nearby amount fills missing scope.
    The validator must reparse the original sentence, not a cropped prefix.
    """
    text = doc['text'][start:end]
    if not MONEY_CUE.search(text):
        return {'atoms': [], 'abstentions': []}
    whole = span(doc, start, end)

    def field(value, lo, hi):
        return {'value': value, 'support': span(doc, start + lo, start + hi)}

    def refuse(reason):
        return {'atoms': [], 'abstentions': [{'reason': reason, 'support': whole,
                'human_review_status': 'pending', 'signal_eligible': False}]}

    if len(text) > 6000:
        return refuse('amount_sentence_limit')
    if re.search(r'[|\t\n]|<[^>]+>', text):
        return refuse('amount_table_scope_unsupported')
    unnegated = re.sub(r'\bno\s+(?:more|less)\s+than\b', '', text, flags=re.I)
    if re.search(r'\b(?:not|no|never|neither|without|denied|denies|revoked|rescinded|terminated|cancelled|canceled|withdrawn|retracted|incorrect|superseded)\b|n[\'’]t\b', unnegated, re.I):
        return refuse('amount_assertion_not_established')
    if re.search(r'\b(?:expected|projected|planned|potential)\b|\bto\s+be\s+(?:obligated|received|raised|paid|exercised)\b', text, re.I):
        return refuse('amount_realization_scope_unsupported')
    if re.search(r'\b(?:converted|conversion|equivalent|exchange\s+rate|annualized|annualised|sum\s+of)\b', text, re.I):
        return refuse('amount_derived_value_unsupported')
    if re.search(r'\b(?:estimated|estimate|exceeding|exceeds|approximately\s+between|minus|negative|excluding|inclusive\s+of|totaling|totalling|respectively)\b', text, re.I):
        return refuse('amount_qualifier_or_association_unsupported')
    matches = list(MONEY.finditer(text))
    if not matches or len(matches) > 8:
        return refuse('amount_token_or_count_unsupported')
    # Only prefix operators have an exact scoped representation. A trailing
    # bound must not disappear and turn a lower/upper limit into an equality.
    if any(POSTPOSED_BOUND.match(text, match.end()) for match in matches):
        return refuse('amount_operator_unsupported')
    if len(matches) != len(list(MONEY_CUE.finditer(text))):
        return refuse('amount_currency_or_scale_unsupported')
    occupied = [m.span() for m in matches]
    identity = None
    identities = list(re.finditer(r'\b(?:contract|financing)\s+id\s*:\s*([A-Za-z0-9](?:[A-Za-z0-9_./-]{0,78}[A-Za-z0-9])?)(?![\w/-])', text, re.I))
    if len(identities) > 1:
        return refuse('amount_identity_ambiguous')
    if identities:
        m = identities[0]
        identity = field(m[1], *m.span(1)); occupied.append(m.span())
    scale_values = {None: Decimal(1), 'thousand': Decimal(1000),
                    'million': Decimal(1000000), 'billion': Decimal(1000000000)}
    parsed = []
    try:
        for m in matches:
            raw = m['number'].replace(',', '')
            if len(raw) > 24:
                return refuse('amount_decimal_limit')
            number = Decimal(raw) * scale_values[m['scale'].lower() if m['scale'] else None]
            if not number.is_finite() or abs(number) > Decimal('1000000000000000000'):
                return refuse('amount_value_unsupported')
            value = format(number.normalize(), 'f')
            currency = {'US$': 'USD', '€': 'EUR', '$': None, '£': None}.get(m['currency'].upper(), m['currency'].upper())
            parsed.append((m, field(currency, *m.span('currency')) if currency else None,
                           field(value, m.start('number'), m.end('scale') if m['scale'] else m.end('number')),
                           field(m['scale'].lower(), *m.span('scale')) if m['scale'] else None))
    except (InvalidOperation, ValueError, OverflowError):
        return refuse('amount_value_unsupported')

    # A range must explicitly denominate both bounds. Shared abbreviated units,
    # mixed currencies and inferred currency conversions are not reconstructed.
    is_range = (len(parsed) == 2 and re.search(r'\bbetween\s*$', text[:matches[0].start()], re.I)
                and re.fullmatch(r'\s+and\s+', text[matches[0].end():matches[1].start()], re.I))
    if is_range:
        if parsed[0][1] != parsed[1][1] and ((parsed[0][1] or {}).get('value') != (parsed[1][1] or {}).get('value')):
            return refuse('amount_range_currency_mismatch')
        if Decimal(parsed[0][2]['value']) > Decimal(parsed[1][2]['value']):
            return refuse('amount_range_reversed')
    elif re.search(r'\bbetween\b|\b(?:USD|EUR|GBP|CAD|AUD|JPY)\s*\d+(?:\.\d+)?\s*[-–]\s*\d', text, re.I):
        return refuse('amount_range_grammar_unsupported')

    atoms = []
    for index, (m, currency, value, scale) in enumerate(parsed[:1] if is_range else parsed):
        left = matches[index - 1].end() if index else 0
        right = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        if is_range:
            left, right = 0, len(text)
        prefix = text[left:m.start()]
        suffix_start = parsed[1][0].end() if is_range else m.end()
        suffix = text[suffix_start:right]
        p_separators, s_separators = list(CONNECTOR.finditer(prefix)), list(CONNECTOR.finditer(suffix))
        role_left = left + p_separators[-1].end() if p_separators else left
        role_right = suffix_start + s_separators[0].start() if s_separators else right
        role_regions = [(role_left, m.start()), (suffix_start, role_right)]
        roles = []
        for role, pattern in ROLE_PATTERNS.items():
            for lo, hi in role_regions:
                for hit in re.finditer(pattern, text[lo:hi], re.I):
                    roles.append((role, lo + hit.start(), lo + hit.end()))
        # Single financing-authority or ceiling clauses can place their noun
        # before a labelled identity, but never lend it to another money atom.
        if not roles and len(parsed) == 1:
            for role, pattern in ROLE_PATTERNS.items():
                for hit in re.finditer(pattern, text, re.I):
                    roles.append((role, hit.start(), hit.end()))
        if not roles and re.search(r'\b(?:contract|award|agreement)\b', text, re.I):
            hit = re.search(r'\bup\s+to\s*$', text[:m.start()], re.I)
            if hit:
                roles.append(('ceiling', hit.start(), hit.end()))
        if len({r[0] for r in roles}) != 1:
            return refuse('amount_role_ambiguous_or_missing')
        role, rlo, rhi = min(roles, key=lambda r: min(abs(m.start()-r[2]), abs(m.end()-r[1])))
        if Decimal(value['value']) < 0 and role != 'obligation':
            return refuse('amount_negative_role_unsupported')

        qualifier = BOUND.search(text[:m.start()])
        if re.search(r'\b(?:nearly|almost|around|over|under|upwards\s+of|in\s+excess\s+of|less\s+than\s+approximately)\s*$', text[:m.start()], re.I):
            return refuse('amount_operator_unsupported')
        operator = 'equal'; operator_support = (m.start(), m.end())
        if qualifier:
            q = ' '.join(qualifier['qualifier'].lower().split())
            operator = {'less than': '<', 'more than': '>', 'at most': '<=', 'at least': '>=',
                        'up to': '<=', 'no more than': '<=', 'no less than': '>=',
                        'approximately': 'approximate', 'about': 'approximate', 'roughly': 'approximate',
                        '≤': '<=', '≥': '>=', '≈': 'approximate', '~': 'approximate'}.get(q, q)
            operator_support = qualifier.span('qualifier')
            occupied.append(operator_support)
            if BOUND.search(text[:qualifier.start()]):
                return refuse('amount_operator_ambiguous')
        lower = upper = None
        if is_range:
            q = re.search(r'\bbetween\s*$', text[:m.start()], re.I)
            operator, operator_support = 'range', (q.start(), q.start() + len('between'))
            lower, upper = parsed[0][2], parsed[1][2]
            value = None
        elif operator in ('<', '<='):
            upper, value = value, None
        elif operator in ('>', '>='):
            lower, value = value, None

        additivity = condition = period = None
        additivity_target = None
        # These fields are supported only within the same amount's local clause.
        link = re.search(r'\b(including|included|incremental|additional|alternative)\b[^;\d]*$', prefix, re.I)
        if link:
            additivity = field({'including':'included', 'included':'included', 'additional':'incremental',
                                'incremental':'incremental', 'alternative':'alternative'}[link[1].lower()],
                               left + link.start(1), left + link.end(1))
            occupied.append((left + link.start(1), left + link.end(1)))
            if atoms:
                additivity_target = {key: atoms[-1][key] for key in
                    ('role', 'currency', 'operator', 'value', 'lower', 'upper', 'contract_or_financing_id')}
        cond = re.search(r'\b(?:subject\s+to|conditional\s+on|contingent\s+(?:on|upon)|provided\s+that|if)\s+[^;.!?]+', suffix, re.I)
        if cond:
            if len(cond[0]) > 160:
                return refuse('amount_condition_limit')
            raw = cond[0].strip()
            condition = field(raw, suffix_start + cond.start(), suffix_start + cond.start() + len(raw))
            occupied.append((suffix_start + cond.start(), suffix_start + cond.end()))
        period_text = list(suffix)
        if cond:
            period_text[cond.start():cond.end()] = ' ' * (cond.end() - cond.start())
        period_text = ''.join(period_text)
        local_periods = []
        for pattern, kind in ((r'\bper\s+(year|month|week|day)\b', 'rate'),
                              (r'\bthrough\s+(\d{4}(?:-\d{2}(?:-\d{2})?)?)\b', 'through'),
                              (r'\bfor\s+(\d+)\s+(years?|months?|weeks?|days?)\b', 'duration')):
            for hit in re.finditer(pattern, period_text, re.I):
                local_periods.append((kind, hit))
        if len(local_periods) > 1:
            return refuse('amount_period_ambiguous')
        if local_periods:
            kind, hit = local_periods[0]
            lo, hi = suffix_start + hit.start(), suffix_start + hit.end()
            period = {'kind': field(kind, lo, hi),
                      'value': field(hit[1], suffix_start + hit.start(1), suffix_start + hit.end(1)),
                      'unit': None, 'precision': None}
            if kind == 'through':
                precision = time_interval(hit[1])['precision']
                if precision == 'unknown':
                    return refuse('amount_period_invalid')
                period['precision'] = field(precision, suffix_start + hit.start(1), suffix_start + hit.end(1))
            else:
                group = 2 if kind == 'duration' else 1
                period['unit'] = field(hit[group].lower().rstrip('s'), suffix_start + hit.start(group), suffix_start + hit.end(group))
            occupied.append((lo, hi))
        option_status = None
        status_text = list(text[role_left:role_right])
        if cond:
            lo, hi = max(role_left, suffix_start + cond.start()), min(role_right, suffix_start + cond.end())
            if lo < hi:
                status_text[lo-role_left:hi-role_left] = ' ' * (hi-lo)
        status_text = ''.join(status_text)
        if role == 'option':
            statuses = list(re.finditer(r'\b(unexercised|exercised)\b', status_text, re.I))
            if len(statuses) > 1:
                return refuse('amount_option_status_ambiguous')
            if statuses:
                hit = statuses[0]
                option_status = field(hit[0].lower(), role_left + hit.start(), role_left + hit.end())
        obligation_kind = None
        if role == 'obligation':
            changes = list(re.finditer(r'\b(deobligation|obligation\s+change|currently\s+obligated|current\s+obligation)\b',
                                      status_text, re.I))
            if len(changes) > 1:
                return refuse('amount_obligation_kind_ambiguous')
            if changes:
                hit = changes[0]
                meaning = ('reduction' if hit[0].lower() == 'deobligation' else
                           'transaction_change' if hit[0].lower().startswith('obligation change') else 'current')
                obligation_kind = field(meaning, role_left + hit.start(), role_left + hit.end())
        atom = {'contract': SCOPED_VERSION, 'currency': currency,
                'operator': field(operator, *operator_support), 'value': value, 'lower': lower, 'upper': upper,
                'scale': scale, 'role': field(role, rlo, rhi), 'contract_or_financing_id': identity,
                'period': period, 'condition': condition, 'additivity': additivity, 'option_status': option_status,
                'additivity_target': additivity_target,
                'obligation_kind': obligation_kind,
                'source_evidence_id': doc['evidence_id'], 'source_revision_id': doc['revision_id'],
                'support': whole, 'human_review_status': 'pending', 'signal_eligible': False,
                'inferred_revenue': None, 'inferred_cash_received': None, 'inferred_runway': None,
                'missing_fields': [k for k,v in (('currency',currency),('period',period),('contract_or_financing_id',identity)) if v is None]}
        scope = {k:atom[k] for k in ('currency','role','contract_or_financing_id','period','condition','additivity','additivity_target','option_status','obligation_kind')}
        atom['amount_scope_id'] = 'amount-scope:' + digest(amount_semantic(scope))
        atom['amount_id'] = 'amount:' + digest(amount_semantic(atom))
        atoms.append(atom)
    residual = list(text)
    for lo, hi in occupied:
        residual[lo:hi] = ' ' * (hi-lo)
    if re.search(r'\d|[$€£%]|\b(?:USD|EUR|GBP|CAD|AUD|JPY|CHF|CNY|INR)\b', ''.join(residual), re.I):
        return refuse('amount_unconsumed_numeric_scope')
    if re.search(r'\b(?:subject\s+to|conditional|contingent|provided\s+that|if|unless|per|through|years?|months?|weeks?|days?|annually|annual|monthly|weekly|daily|including|included|incremental|additional|alternative|approximately|about|roughly)\b|[<>≤≥≈~]', ''.join(residual), re.I):
        return refuse('amount_unconsumed_qualifier_scope')
    return {'atoms': atoms, 'abstentions': []}
