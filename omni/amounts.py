"""Exact, narrowly scoped contract amounts; no accounting or revenue inference."""
from decimal import Decimal
import re

CONTRACT_AMOUNTS = {'contract_ceiling_amount', 'contract_option_amount', 'contract_obligation_amount'}
AMOUNT = re.compile(
    r'(?<![-\w])(?:US\$|\bUSD\b\s*\$?)\s*'
    r'((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)'
    r'(?:\s*(thousand|million|billion))?\b(?![.,]\d)', re.I)


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
    residual = text[:match.start()] + text[match.end():]
    if re.search(r'\d|[$€£%]|\b(?:EUR|GBP|CAD|AUD|JPY)\b', residual, re.I):
        return []
    if re.match(r'\s*(?:m|bn|b|k)\b', text[match.end():], re.I):
        return []
    if re.search(r'\b(?:approximately|about|estimated|estimate|less than|more than|exceeds|minimum|between|negative|minus|plus|per|annually|annual|years?|months?|monthly|weekly|daily|hours?|conditional|contingent|subject to|if)\b|[<>~]', text, re.I):
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
