"""Diversified market-headline orchestration.

Official sources, FMP, Alpha Vantage News and GDELT form the primary pool.
Google News is fetched lazily only when the primary pool cannot fill the
publisher-diverse section. Provider adapters own transport/parsing; this class
owns relevance, freshness, syndication dedupe and deterministic selection.
"""

from collections import Counter
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import logging
import re
import unicodedata

from config import (
    ALPHA_VANTAGE_API_KEY,
    EDITORIAL_COVERAGE_MODE,
    EVIDENCE_INTAKE_MODE,
    EDITORIAL_COVERAGE_TICKERS,
    EDITORIAL_ONLY_TICKER_SET,
    FMP_API_KEY,
    GDELT_TICKER_ALIASES,
    HEADLINE_GDELT_ENABLED,
    HEADLINE_GOOGLE_FALLBACK_ENABLED,
    HEADLINE_GOOGLE_MAX_SELECTED,
    HEADLINE_MAX_PER_PUBLISHER,
    HEADLINE_OFFICIAL_FEEDS,
    HEADLINE_REQUEST_TIMEOUT_SECONDS,
    HEADLINE_TARGET_COUNT,
    MACRO_KEYWORDS,
    SIGNAL_ELIGIBLE_TICKER_SET,
    SIGNAL_ELIGIBLE_TICKERS,
    TICKER_ALIASES,
)
from agents.news_providers import (
    AlphaVantageNewsProvider,
    FMPNewsProvider,
    BatchedGDELTNewsProvider,
    GoogleNewsProvider,
    OfficialFeedProvider,
    ProviderResult,
)
from timeutil import to_utc_z

logger = logging.getLogger("NewsAgent")


class NewsAgent:
    MIN_RELEVANCE_SCORE = 1
    HEADLINES_MAX_AGE_DAYS = 3
    PRIMARY_QUERY = "Stock Market OR Global Economy OR Finance"
    FALLBACK_QUERIES = (
        "stock market today OR S&P 500 OR Nasdaq OR Dow",
    )
    TARGET_COUNT = HEADLINE_TARGET_COUNT
    SHADOW_AUDIT_LIMIT = 10
    MAX_PER_PUBLISHER = HEADLINE_MAX_PER_PUBLISHER
    GOOGLE_MAX_SELECTED = HEADLINE_GOOGLE_MAX_SELECTED
    MAX_PER_PROVIDER = 3
    MAX_PER_PROVIDER_PER_LANE = 2
    SOURCE_PRIORITY = {
        "official_central_bank": 0,
        "official_exchange": 0,
        "official_regulator": 0,
        "official": 0,
        "aggregator": 1,
        "api_news_discovery": 1,
        "global_discovery": 2,
        "press_release": 3,
    }
    TREASURY_PATTERNS = (
        r"\bu\.?s\.?\s+treasur(?:y|ies)\b",
        r"\btreasury\s+(?:department|secretary|yield|yields|bond|bonds|"
        r"note|notes|bill|bills|auction|auctions|curve|market|markets|"
        r"security|securities|debt)\b",
        r"\b(?:yield|yields|bond|bonds|note|notes|bill|bills|auction|"
        r"auctions|curve)\s+(?:on\s+)?treasur(?:y|ies)\b",
        r"\b(?:2|5|10|20|30)[- ]year\s+treasur(?:y|ies)\b",
    )
    LANE_PRIORITY = {"universe": 0, "macro": 1, "discovery": 2}
    EXCHANGE_LISTING_PATTERN = re.compile(
        r"(?P<open>\(\s*)?\b"
        r"(?P<exchange>(?i:NASDAQ|NYSEARCA|NYSE|AMEX))"
        r"\s*(?P<delimiter>:|[-\u2010-\u2015])\s*"
        r"(?P<symbol>[A-Z][A-Z0-9.=-]{0,10})"
        r"(?![A-Za-z0-9.=-])(?P<close>\s*\))?"
    )
    EXCHANGE_LISTED_PATTERN = re.compile(
        r"\b(?i:NASDAQ|NYSEARCA|NYSE|AMEX)"
        r"(?:[-\s\u2010-\u2015]+listed)\b"
    )
    EXCHANGE_LISTED_SYMBOL_PATTERN = re.compile(
        r"\b(?i:NASDAQ|NYSEARCA|NYSE|AMEX)"
        r"(?:[-\s\u2010-\u2015]+listed)\b\s+"
        r"(?P<symbol>[A-Z][A-Z0-9.=-]{0,10})"
        r"(?![A-Za-z0-9.=-])"
    )
    LISTING_SUBJECT_WORDS = {
        "COMPANY", "COMPOSITE", "EXCHANGE", "FUTURE", "FUTURES",
        "HALT", "HALTS", "INDEX", "ISSUER", "MARKET", "MARKETS",
        "NEWS", "SHARE", "SHARES", "STOCK", "STOCKS", "SYSTEM",
        "SYSTEMS", "TRADING", "UPDATE",
    }
    DISPLAY_TICKER_ALIASES = {"^VIX": ("VIX",)}
    AMBIGUOUS_TITLE_TICKERS = {
        "COIN", "SHOP", "ITA", "MRNA", "BEAM", "RARE", "MP", "HOOD", "MRK",
    }
    EXCHANGE_PREFIX_MACRO_SUBJECT_PATTERN = re.compile(
        r"^(?:the\s+)?(?:"
        r"(?:trading|trade)\s+(?:halts?|halted|resumes?|resumed|opens?|"
        r"opened|closes?|closed|stops?|stopped|pauses?|paused)\b|"
        r"(?:market|exchange|systems?|composite|index|futures?)\s+(?:"
        r"halts?|halted|resumes?|resumed|opens?|opened|closes?|closed|"
        r"rises?|rose|falls?|fell|gains?|gained|slides?|slid|drops?|"
        r"dropped|surges?|surged|outage|failure|update)\b|"
        r"(?:stock|market)\s+futures?\s+(?:rises?|rose|falls?|fell|"
        r"gains?|gained|slides?|slid|drops?|dropped|surges?|surged)\b"
        r")"
    )

    CONTEXTUAL_MACRO_KEYWORDS = {
        "treasury", "yield", "inflation", "cpi", "tariff", "payroll",
        "china", "nasdaq", "dow", "wall street", "oil", "crude",
        "crypto", "bitcoin",
    }
    EXCHANGE_SUBJECT_PATTERN = re.compile(
        r"\b(?:nasdaq|nysearca|nyse|amex)\b\s*(?::|[-\u2010-\u2015])?\s*"
        r"(?:exchange|market|trading|trade|systems?|composite|index|futures?|"
        r"halts?|halted|resumes?|resumed|opens?|opened|closes?|closed|"
        r"outage|technical|rises?|rose|falls?|fell|gains?|gained|slides?|"
        r"slid|drops?|dropped|surges?|surged)\b"
    )
    EXCHANGE_LEADING_SUBJECT_PATTERN = re.compile(
        r"^(?:nasdaq|nysearca|nyse|amex)\b\s*"
        r"(?::|[-\u2010-\u2015])?\s*(?:(?:exchange|market|trading|trade|"
        r"systems?|composite|index|futures?)\s+(?:halts?|halted|resumes?|"
        r"resumed|opens?|opened|closes?|closed|stops?|stopped|pauses?|"
        r"paused|rises?|rose|falls?|fell|declines?|declined|gains?|gained|"
        r"drops?|dropped|surges?|surged|outage|failure|update)|"
        r"(?:halts?|halted|resumes?|resumed|opens?|opened|closes?|closed|"
        r"outage|failure))\b"
    )
    EXCHANGE_REVERSE_SUBJECT_PATTERN = re.compile(
        r"\b(?:trading|trade)\s+"
        r"(?:halts?|halted|resumes?|resumed|opens?|opened|closes?|closed)"
        r"\s+(?:on|at)\s+(?:nasdaq|nysearca|nyse|amex)\b"
    )
    INFLATION_SUBJECT_PATTERNS = (
        r"\b(?:(?:u\.?s\.?|us|headline|core|consumer|annual|monthly)\s+)"
        r"{0,3}inflation\s+(?:cools?|cooled|eases?|eased|slows?|slowed|"
        r"falls?|fell|drops?|dropped|rises?|rose|climbs?|climbed|"
        r"accelerates?|accelerated|holds?|held|ticks?|ticked|comes?|came)\b",
        r"^(?:(?:nasdaq|nysearca|nyse|amex)\s*"
        r"(?::|[-\u2010-\u2015])\s*)?(?:(?:u\.?s\.?|us|headline|core|"
        r"consumer|annual)\s+){0,2}inflation\s+"
        r"(?:remains?|remained|stays?|stayed)\s+"
        r"(?:sticky|elevated|high|hot|persistent|above|below)\b",
        r"\bcpi\s+(?:cools?|cooled|eases?|eased|slows?|slowed|falls?|fell|"
        r"drops?|dropped|rises?|rose|climbs?|climbed|accelerates?|"
        r"accelerated|holds?|held|ticks?|ticked|hotter|cooler|higher|lower|"
        r"report|data|reading|print)\b",
        r"\b(?:inflation|cpi)\s+(?:rate|report|data|reading|print)\b",
    )
    PAYROLL_SUBJECT_PATTERNS = (
        r"\bpayrolls\s+(?:miss(?:es|ed)?|beat(?:s|en)?|rise|rises|rose|"
        r"fall|falls|fell|increase|increases|increased|decline|declines|"
        r"declined|add|adds|added|shed|sheds|come|comes|came|surprise|"
        r"surprises|surprised)\b",
        r"\b(?:(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
        r"jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|"
        r"nov(?:ember)?|dec(?:ember)?|u\.?s\.?|us|nonfarm)\s+){1,3}"
        r"payrolls?\s+(?:miss(?:es|ed)?|beat(?:s|en)?|rise|rises|rose|"
        r"fall|falls|fell|increase|increases|increased|decline|declines|"
        r"declined|add|adds|added|shed|sheds|come|comes|came|surprise|"
        r"surprises|surprised)\b",
        r"\b(?:nonfarm\s+payrolls?|payrolls?\s+(?:report|data))\b",
    )
    TARIFF_SUBJECT_PATTERNS = (
        r"\b(?:u\.?s\.?|us|china|eu|european\s+union|administration|"
        r"government)\s+(?:imposes?|imposed|raises?|raised|cuts?|cut|"
        r"delays?|delayed|announces?|announced|lifts?|lifted|pauses?|"
        r"paused)\s+(?:new\s+)?tariffs?\b",
        r"\btariffs?\s+(?:take|takes|took)\s+effect\b",
        r"\btariffs?\s+(?:rise|rises|rose|fall|falls|fell|increase|"
        r"increases|increased|hit|hits|target|targets|cover|covers)\b",
        r"\btariffs?\s+(?:on|against)\s+(?:imports?|exports?|china|"
        r"europe|mexico|canada|goods|steel|aluminum|semiconductors?)\b",
        r"\b(?:trade|tariff)\s+policy\b",
    )
    WALL_STREET_SUBJECT_PATTERNS = (
        r"\bwall\s+street\s+(?:falls?|fell|rises?|rose|gains?|gained|"
        r"slides?|slid|drops?|dropped|surges?|surged|rallies|rallied|"
        r"retreats?|retreated|slumps?|slumped|slips?|slipped|jumps?|"
        r"jumped|tumbles?|tumbled|opens?|opened|closes?|closed|braces|"
        r"reacts?|stocks?|markets?|futures?)\b",
    )
    WALL_STREET_SUBJECT_LEAD_PATTERN = re.compile(
        r"^wall\s+street\s+(?:falls?|fell|rises?|rose|gains?|gained|"
        r"slides?|slid|drops?|dropped|surges?|surged|rally|rallies|rallied|"
        r"retreats?|retreated|slumps?|slumped|slips?|slipped|jumps?|"
        r"jumped|tumbles?|tumbled|opens?|opened|closes?|closed|braces|"
        r"reacts?|(?:stocks?|markets?|futures?)\s+(?:rise|rises|rose|fall|"
        r"falls|fell|gain|gains|gained|drop|drops|dropped|rally|rallies|"
        r"rallied|open|opens|opened|close|closes|closed))\b"
    )


    DOW_COMPANY_PATTERN = re.compile(
        r"\bdow\s+(?:inc\.?|incorporated|chemical)\b"
    )
    DOW_SUBJECT_PATTERNS = (
        r"\bdow(?:\s+jones(?:\s+industrial\s+average)?|\s+industrials?|"
        r"\s+index|\s+futures?)\b",
        r"\bdow\b\s+(?:market\s+update|rises?|rose|falls?|fell|gains?|"
        r"gained|slides?|slid|drops?|dropped|surges?|surged|opens?|opened|"
        r"closes?|closed)\b",
        r"\b(?:market|stocks?|equities|futures?)\b.{0,30}\bdow\b",
    )
    CHINA_MACRO_PATTERNS = (
        r"\bchina(?:'s)?\s+(?:economy|economic|markets?|stocks?|equities|"
        r"growth|gdp|inflation|deflation|trade|tariffs?|exports?|imports?|"
        r"stimulus|central\s+bank|pboc|property|currency|yuan)\b",
        r"\bchina(?:'s)?\s+(?:manufacturing|industrial)\s+"
        r"(?:pmi|activity|output|data|index|survey)\b",
        r"\b(?:economy|economic|markets?|stocks?|equities|growth|gdp|"
        r"inflation|deflation|trade|tariffs?|exports?|imports?|stimulus|"
        r"central\s+bank|pboc|property|currency|yuan)\b.{0,24}\bchina\b",
        r"\b(?:manufacturing|industrial)\s+"
        r"(?:pmi|activity|output|data|index|survey)\b.{0,24}\bchina\b",
    )
    YIELD_CONTEXT_PATTERNS = (
        r"\b(?:treasury|treasuries|bond|bonds|note|notes|rates?)\s+yields?\b",
        r"\byields?\s+(?:on\s+)?(?:treasury|treasuries|bond|bonds|notes?)\b",
        r"\b(?:2|5|10|20|30)[- ]year\s+(?:treasury\s+)?yields?\b",
        r"\byields?\s+(?:rise|rises|rose|fall|falls|fell|jump|jumps|"
        r"surge|surges|drop|drops)\b.{0,30}\b(?:rates?|bonds?|treasur(?:y|ies))\b",
    )
    OIL_SUBJECT_PATTERNS = (
        r"\b(?:oil|crude(?:\s+oil)?)\s+(?:prices?|market|futures?|supply|"
        r"demand|inventor(?:y|ies)|rises?|rose|falls?|fell|gains?|gained|"
        r"slides?|slid|drops?|dropped|surges?|surged|tumbles?|tumbled|update)\b",
        r"\b(?:prices?|market|futures?|supply|demand|inventor(?:y|ies))\b"
        r".{0,24}\b(?:oil|crude)\b",
        r"\b(?:u\.?s\.?|us|opec|global|world|worldwide)\s+"
        r"(?:crude\s+)?oil\s+(?:output|production)\b",
    )
    CRYPTO_SUBJECT_PATTERNS = (
        r"\b(?:bitcoin|crypto(?:currency)?)\s+(?:prices?|market|markets|"
        r"futures?|etfs?|trading|regulation|rises?|rose|falls?|fell|gains?|"
        r"gained|slides?|slid|drops?|dropped|surges?|surged|update)\b",
        r"\b(?:prices?|market|markets|futures?|etfs?|trading|regulation)\b"
        r".{0,24}\b(?:bitcoin|crypto(?:currency)?)\b",
    )
    HUMAN_MACRO_SUBJECT_PATTERN = re.compile(
        r"^(?:the\s+)?(?:economists?|analysts?|investors?|consumers?|"
        r"policymakers?|officials?)\s+(?:"
        r"(?:brace|prepare)\s+for|"
        r"(?:watch|await|expect|forecast|predict|weigh)(?:\s+the)?|"
        r"(?:warn|signal)(?:\s+that)?"
        r")\s+(?:fed|fomc|federal\s+reserve|cpi|inflation|"
        r"payrolls?|jobs\s+report|recession|interest\s+rates?|"
        r"rate\s+(?:cut|hike)|treasury|bond\s+yields?|yields?|"
        r"oil|crude|opec|bitcoin|crypto(?:currency)?|wall\s+street|"
        r"dow|nasdaq|s&p\s+500|russell\s+2000|markets?|stocks?)\b"
    )
    EDITORIAL_SUBJECT_PREFIX_PATTERN = re.compile(
        r"^(?:(?:breaking(?:\s+news)?|update(?:\s+\d+)?|exclusive)\s*"
        r"(?::|[-\u2010-\u2015])\s*)+"
    )
    EXCHANGE_LABEL_SUBJECT_PATTERN = re.compile(
        r"^(?:nasdaq|nysearca|nyse|amex)\s*"
        r"(?::|[-\u2010-\u2015])\s*"
    )
    CPI_SUBJECT_LEAD_PATTERN = re.compile(
        r"^(?:the\s+)?(?:(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|"
        r"apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|"
        r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?|"
        r"u\.?s\.?|us|headline|core|consumer|annual|monthly|eurozone|"
        r"european)\s+){0,2}cpi\s+(?:(?:comes?|came)\s+in\s+)?(?:"
        r"hot|cold|hotter|cooler|higher|lower|stronger|weaker|"
        r"cools?|cooled|eases?|eased|slows?|slowed|falls?|fell|"
        r"drops?|dropped|rises?|rose|climbs?|climbed|accelerates?|"
        r"accelerated|holds?|held|ticks?|ticked|"
        r"(?:report|data|reading|print)\s+(?:shows?|showed|signals?|"
        r"signaled|indicates?|indicated|reveals?|revealed|hurts?|hurt|"
        r"pressures?|pressured|reshapes?|reshaped|affects?|affected|"
        r"comes?|came|rises?|rose|falls?|fell|remains?|remained|stays?|stayed)"
        r")\b"
    )
    LEADING_MARKET_ACTION_PATTERN = re.compile(
        r"^(?:the\s+)?"
        r"(?:(?:u\.?s\.?|us|global|worldwide|eurozone|european|asian)"
        r"\s+)?(?:stock\s+market\s+today\s*:|"
        r"(?:stock\s+markets?|markets?|stocks?|shares?|equities|futures?|dollar|bonds?|treasuries|"
        r"yields?)\s+(?:"
        r"rises?|rose|falls?|fell|declines?|declined|gains?|gained|"
        r"drops?|dropped|surges?|surged|slides?|slid|rally|rallies|rallied|"
        r"retreats?|retreated|slumps?|slumped|slips?|slipped|jumps?|"
        r"jumped|tumbles?|tumbled|opens?|opened|closes?|closed|"
        r"advances?|advanced|climbs?|climbed|dips?|dipped|sinks?|sank|"
        r"soars?|soared|edges?|edged|trades?|traded|holds?|held|"
        r"braces?|reacts?|points?|pointed)\b)"
    )
    POWELL_POLICY_SUBJECT_PATTERN = re.compile(
        r"^(?:jerome\s+)?powell\s+(?:says?|said|signals?|signaled|warns?|warned|"
        r"expects?|expected|sees?|saw|backs?|backed|defends?|defended)\b"
        r".{0,60}\b(?:fed|rates?|inflation|cpi|payrolls?|jobs|economy|"
        r"growth|policy)\b"
    )
    TREASURY_SUBJECT_LEAD_PATTERN = re.compile(
        r"^(?:(?:(?:u\.?s\.?|us)\s+treasur(?:y|ies)|"
        r"treasury\s+(?:department|secretary))\s+(?:announces?|announced|"
        r"updates?|updated|says?|said|signals?|signaled|issues?|issued|"
        r"releases?|released|imposes?|imposed|warns?|warned)\b|"
        r"(?:(?:u\.?s\.?|us)\s+)?(?:(?:2|5|10|20|30)[- ]year\s+)?treasury\s+"
        r"(?:yields?|bonds?|notes?|bills?|auctions?|curve|markets?)\s+"
        r"(?:rises?|rose|falls?|fell|climbs?|climbed|drops?|dropped|"
        r"jumps?|jumped|surges?|surged|slides?|slid|inverts?|inverted|"
        r"steepens?|steepened|flattens?|flattened|holds?|held|steady)\b)"
    )
    DOW_SUBJECT_LEAD_PATTERN = re.compile(
        r"^dow(?:\s+market\s+update\b|"
        r"\s+jones(?:\s+industrial\s+average)?\s+(?:"
        r"futures?\b|market\s+update\b|rises?|rose|falls?|fell|"
        r"declines?|declined|gains?|gained|drops?|dropped|surges?|"
        r"surged|slides?|slid|rally|rallies|rallied|opens?|opened|closes?|"
        r"closed|advances?|advanced|climbs?|climbed|sinks?|sank|"
        r"tumbles?|tumbled)|"
        r"\s+(?:industrials?|index|futures?)\s+(?:rises?|rose|falls?|fell|"
        r"declines?|declined|gains?|gained|drops?|dropped|surges?|surged|"
        r"slides?|slid|rally|rallies|rallied|opens?|opened|closes?|closed)|"
        r"\s+(?:rises?|rose|falls?|fell|declines?|declined|gains?|gained|"
        r"drops?|dropped|surges?|surged|slides?|slid|rally|rallies|rallied|"
        r"opens?|opened|closes?|closed))\b"
    )
    OIL_SUBJECT_LEAD_PATTERN = re.compile(
        r"^(?:(?:u\.?s\.?|us|global|worldwide|opec)\s+)?"
        r"(?:oil|crude(?:\s+oil)?)\s+"
        r"(?:(?:prices?|market|futures?|supply|demand|inventor(?:y|ies)|"
        r"output|production)\s+)?(?:rises?|rose|falls?|fell|gains?|gained|"
        r"slides?|slid|drops?|dropped|surges?|surged|tumbles?|tumbled|"
        r"rally|rallies|rallied|climbs?|climbed|eases?|eased|tightens?|tightened|"
        r"expands?|expanded|contracts?|contracted|update|outlook\s+"
        r"(?:improves?|improved|darkens?|darkened|tightens?|eases?))\b"
    )
    CRYPTO_SUBJECT_LEAD_PATTERN = re.compile(
        r"^(?:(?:u\.?s\.?|us|global|worldwide)\s+)?"
        r"(?:bitcoin|crypto(?:currency)?)\s+"
        r"(?:(?:prices?|market|markets|futures?|etfs?|trading|regulation)"
        r"\s+)?(?:rises?|rose|falls?|fell|gains?|gained|slides?|slid|"
        r"drops?|dropped|surges?|surged|tumbles?|tumbled|rallies|rallied|"
        r"climbs?|climbed|expands?|expanded|contracts?|contracted|"
        r"hits?|hit|update|rally|outlook\s+(?:improves?|improved|"
        r"darkens?|darkened|expands?|expanded|contracts?|contracted))\b"
    )

    CORPORATE_LEAD_DESIGNATOR_PATTERN = re.compile(
        r"^(?:\S+\s+){0,3}(?:inc\.?|incorporated|corp\.?|corporation|"
        r"llc|ltd\.?|limited|plc|co\.?|company|group|holdings)\b"
    )
    NOMINAL_MACRO_PREDICATE = (
        r"(?:shows?|showed|signals?|signaled|informs?|informed|continues?|"
        r"continued|changes?|changed|shifts?|shifted|tightens?|tightened|"
        r"eases?|eased|moves?|moved|looms?|loomed|remains?|remained|"
        r"stays?|stayed|raises?|raised|cuts?|cut|brightens?|brightened|"
        r"darkens?|darkened|improves?|improved|worsens?|worsened)"
    )
    NOMINAL_MACRO_SUBJECT_PATTERN = re.compile(
        r"^(?:the\s+)?(?:(?:fed|fomc|federal\s+reserve|central\s+banks?|"
        r"white\s+house|government|administration)\s+(?:decision|policy|"
        r"meeting|minutes|data|inflation|rates?|outlook|tightening|easing|"
        r"shutdown)|opec\s+(?:policy|meeting|action|decision)|"
        r"(?:(?:u\.?s\.?|us|global|worldwide)\s+)?(?:oil|crude(?:\s+oil)?|"
        r"bitcoin|crypto(?:currency)?)\s+outlook|"
        r"cpi\s+(?:report|data|reading|print)|"
        r"wall\s+street\s+(?:markets?|stocks?|futures?))\s+"
        + NOMINAL_MACRO_PREDICATE + r"\b"
    )
    POLICY_SPOKESPERSON_SUBJECT_PATTERN = re.compile(
        r"^(?:the\s+)?(?:fed|fomc|federal\s+reserve|central\s+banks?)\s+"
        r"(?:chair(?:\s+[a-z][a-z.'-]+){0,2}|governors?|officials?)\s+"
        r"(?:says?|said|signals?|signaled|warns?|warned|expects?|expected)\b"
    )

    POLICY_INSTITUTION_SUBJECT_PATTERN = re.compile(
        r"^(?:the\s+)?(?:fed|fomc|federal\s+reserve|central\s+banks?|"
        r"white\s+house|government|administration)\s+(?:"
        r"holds?|held|leaves?|left|cuts?|cut|raises?|raised|signals?|"
        r"signaled|keeps?|kept|pauses?|paused|decides?|decided|says?|said|"
        r"warns?|warned|announces?|announced|expects?|expected|sees?|saw|"
        r"meets?|met|market\s+update\b|"
        r"decision\s+(?:moves?|moved|signals?|signaled|shifts?|shifted)|"
        r"policy\s+(?:officials?\s+(?:signal|signals|signaled|say|says|said|"
        r"warn|warns|warned)|(?:outlook|stance|path)\s+(?:changes?|changed|"
        r"shifts?|shifted|tightens?|tightened|eases?|eased))|"
        r"(?:meeting|minutes|data|inflation|rates?|outlook|tightening|"
        r"easing|shutdown)\s+(?:shows?|showed|signals?|signaled|informs?|"
        r"informed|continues?|continued|changes?|changed|shifts?|shifted|"
        r"tightens?|tightened|eases?|eased|moves?|moved|looms?|loomed)|"
        r"(?:officials?|chair|governors?)\s+(?:say|says|said|signal|signals|"
        r"signaled|warn|warns|warned|expect|expects|expected))\b"
    )
    INFLATION_SUBJECT_LEAD_PATTERN = re.compile(
        r"^(?:the\s+)?(?:(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|"
        r"apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|"
        r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?|"
        r"u\.?s\.?|us|global|worldwide|eurozone|european|headline|core|"
        r"consumer|annual|monthly)\s+){0,2}inflation\s+"
        r"(?:(?:unexpectedly|surprisingly)\s+)?(?:cools?|cooled|eases?|"
        r"eased|slows?|slowed|falls?|fell|drops?|dropped|rises?|rose|"
        r"climbs?|climbed|accelerates?|accelerated|holds?|held|ticks?|"
        r"ticked|remains?|remained|stays?|stayed|persists?|persisted|"
        r"heats?|heated|surprises?|surprised|"
        r"(?:data|report|rate|reading|print|expectations?|pressures?|"
        r"outlook)\s+(?:shows?|showed|signals?|signaled|points?|pointed|"
        r"indicates?|indicated|reshapes?|reshaped|surprises?|surprised|"
        r"cools?|cooled|eases?|eased|falls?|fell|rises?|rose|comes?|came|"
        r"remains?|remained|stays?|stayed))\b"
    )
    ECONOMIC_DATA_SUBJECT_PATTERN = re.compile(
        r"^(?:the\s+)?(?:(?:u\.?s\.?|us|monthly|annual|headline|core)"
        r"\s+)?(?:(?:consumer\s+price\s+index|consumer\s+prices?)\s+"
        r"(?:(?:(?:comes?|came)\s+in\s+)?(?:hot|cold|hotter|cooler|"
        r"higher|lower|stronger|weaker)|rises?|rose|falls?|fell|climbs?|"
        r"climbed|drops?|dropped|accelerates?|accelerated|cools?|cooled|"
        r"eases?|eased)|unemployment(?:\s+rate)?\s+(?:rises?|rose|falls?|"
        r"fell|climbs?|climbed|drops?|dropped|holds?|held|ticks?|ticked|"
        r"increases?|increased|decreases?|decreased)|jobs\s+(?:report|data)"
        r"\s+(?:shows?|showed|signals?|signaled|indicates?|indicated|"
        r"reveals?|revealed|confirms?|confirmed|misses?|missed|beats?|beat|"
        r"surprises?|surprised))\b"
    )

    RATE_RECESSION_SUBJECT_PATTERN = re.compile(
        r"^(?:the\s+)?(?:(?:u\.?s\.?|us|global|worldwide|eurozone|"
        r"european)\s+)?(?:"
        r"interest\s+rates?\s+(?:hit|hits|remain|remains|remained|stay|"
        r"stays|stayed|rise|rises|rose|fall|falls|fell|climb|climbs|"
        r"climbed|drop|drops|dropped|hold|holds|held|ease|eases|eased|"
        r"peak|peaks|peaked)|"
        r"rate\s+(?:cuts?|hikes?)\s+(?:hopes?|bets?|odds?|expectations?|"
        r"outlook|prospects?|look|looks|seem|seems|remain|remains|rise|"
        r"rises|rose|fall|falls|fell|grow|grows|grew|fade|fades|faded)|"
        r"recession\s+(?:(?:fears?|risks?|odds?|concerns?|warnings?)\s+"
        r"(?:rise|rises|rose|fall|falls|fell|grow|grows|grew|ease|eases|"
        r"eased|spread|spreads|mount|mounts|fade|fades|faded)|"
        r"(?:looms?|loomed|begins?|began|ends?|ended|deepens?|deepened|"
        r"hits?|hit|threatens?|threatened))|"
        r"bond\s+yields?\s+(?:rise|rises|rose|fall|falls|fell|climb|"
        r"climbs|climbed|drop|drops|dropped|jump|jumps|jumped|surge|"
        r"surges|surged|slide|slides|slid|hold|holds|held))\b"
    )
    OPEC_SUBJECT_LEAD_PATTERN = re.compile(
        r"^opec\s+(?:(?:oil\s+)?(?:production|output|supply|demand)\s+"
        r"(?:cuts?|cut|rises?|rose|falls?|fell|grows?|grew|drops?|dropped|"
        r"tightens?|tightened|eases?|eased)|(?:policy|meeting|action|"
        r"decision)\s+(?:changes?|changed|signals?|signaled|cuts?|cut|"
        r"tightens?|tightened|eases?|eased|takes?|took)|(?:agrees?|agreed|"
        r"cuts?|cut|raises?|raised|holds?|held|signals?|signaled|"
        r"announces?|announced|meets?|met))\b"
    )
    INDEX_SUBJECT_LEAD_PATTERN = re.compile(
        r"^(?:s&p\s+500|russell\s+2000)\s+(?:(?:index|benchmark|futures?)"
        r"\s+)?(?:rises?|rose|falls?|fell|declines?|declined|gains?|"
        r"gained|drops?|dropped|surges?|surged|slides?|slid|rally|rallies|"
        r"rallied|opens?|opened|closes?|closed|advances?|advanced|"
        r"climbs?|climbed|update|outlook)\b"
    )

    ISSUER_POLICY_DRIVER_PATTERN = re.compile(
        r"\b(?:as|after|amid|on)\s+(?:fed|fomc|federal\s+reserve)\b"
        r".{0,24}\b(?:bets?|odds?|expectations?|outlook)\s+"
        r"(?:rises?|rose|falls?|fell|grows?|grew|eases?|eased|"
        r"strengthens?|strengthened|fades?|faded)\b"
    )
    POLICY_ACTOR_MACRO_PATTERN = re.compile(
        r"^(?:the\s+)?(?:u\.?s\.?|us|china|eu|european\s+union|"
        r"uk|united\s+kingdom|government|administration|white\s+house)"
        r"\s+(?:imposes?|imposed|raises?|raised|cuts?|cut|delays?|delayed|"
        r"announces?|announced|lifts?|lifted|pauses?|paused)"
        r"\s+(?:new\s+)?tariffs?\b"
    )
    COMPANY_CONTEXT_ONLY_KEYWORDS = {
        "earnings",
        "layoff",
        "fda",
        "semiconductor",
        "uranium",
    }
    VERTICAL_KEYWORDS = {
        "uranium_nuclear": (
            "uranium", "nuclear", "reactor", "fuel cycle", "enrichment",
        ),
        "semiconductors": (
            "semiconductor", "chipmaker", "chip makers", "foundry",
        ),
        "biotechnology": (
            "biotech", "pharmaceutical", "clinical trial", "drug", "fda",
        ),
        "defense_aerospace": (
            "defense", "aerospace", "missile", "aircraft",
        ),
        "mining_metals": (
            "copper", "mine", "miner", "mining", "critical mineral",
        ),
    }
    AUTHORITY_SCORES = {
        "official_central_bank": 3,
        "official_exchange": 3,
        "official_regulator": 3,
        "official": 3,
        "aggregator": 2,
        "api_news_discovery": 1,
        "global_discovery": 1,
        "press_release": 1,
    }
    # A result must state an occurred clinical outcome. Planned readouts,
    # historical quotations, and competitor outcomes fail closed so this
    # remains a narrow materiality rule rather than a broad biotech classifier.
    CLINICAL_OUTCOME_CONTEXT_PATTERN = re.compile(
        r"\b(?:phase\s*(?:2|3)|clinical trial|pivotal trial)\b"
    )
    CLINICAL_OUTCOME_OCCURRED_PATTERN = re.compile(
        r"\b(?:"
        r"met\s+(?:the\s+)?(?:primary|key secondary)\s+endpoints?|"
        r"met\s+(?:the\s+)?(?:primary and key secondary|primary and secondary)\s+endpoints|"
        r"met\s+endpoints?\s+of\s+[a-z0-9][a-z0-9 /+.-]{0,40}|"
        r"positive\s+(?:phase\s*(?:2|3)\s+)?top-?line\s+results?|"
        r"(?:did not meet|failed to meet|missed)\s+(?:the\s+)?"
        r"(?:primary|key secondary)\s+endpoints?"
        r")\b"
    )
    CLINICAL_OUTCOME_PREFIX_EXCLUSION_PATTERN = re.compile(
        r"\b(?:expects?|expecting|anticipates?|anticipated|plans?|planned|"
        r"scheduled|upcoming|will|could|may|might|aims?|designed to|"
        r"competitor(?:'s)?|rival(?:'s)?|previously|historical|history|"
        r"recalls?|recalled)\b"
    )
    CLINICAL_OUTCOME_SUFFIX_HISTORY_PATTERN = re.compile(
        r"\b(?:last year|previously|historically)\b"
    )
    IMPACT_PATTERNS = (
        (r"\b(?:bankrupt(?:cy)?|merger|takeover|acquisition)\b", 2),
        (r"\b(?:financing|offering|strategic investor|project equity)\b", 2),
        (r"\b(?:approval|approved|rejection|rejected|clinical hold)\b", 2),
        (r"\b(?:trading halt|systems halt|market-wide halt|recall)\b", 2),
        (r"\b(?:guidance cut|cuts guidance|raises guidance)\b", 2),
        (r"\b(?:surges?|jumps?|rips?|plunges?|tumbles?)\b", 2),
        (r"\b(?:downgrade|upgrade|insider sale|share sale)\b", 1),
        (r"\b(?:strategic shift|earnings preview|quarterly results)\b", 1),
    )

    def __init__(self, now=None, providers=None, google_provider=None, evidence_intake_mode=None):
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        self.now_utc = now.astimezone(timezone.utc)
        self._intake_mode = evidence_intake_mode or EVIDENCE_INTAKE_MODE
        if self._intake_mode not in ("off", "shadow"):
            raise ValueError("evidence intake mode must be off or shadow")
        self._evidence_snapshot = None
        self.base_url = (
            "https://news.google.com/rss/search?q={query}"
            "&hl=en-US&gl=US&ceid=US:en"
        )
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36"
            )
        }
        self._providers = (
            list(providers) if providers is not None else self._default_providers()
        )
        self._google_provider = google_provider or GoogleNewsProvider(
            primary_query=self.PRIMARY_QUERY,
            fallback_queries=self.FALLBACK_QUERIES,
            now=self.now_utc,
            timeout=HEADLINE_REQUEST_TIMEOUT_SECONDS,
        )
        self._google_enabled = HEADLINE_GOOGLE_FALLBACK_ENABLED
        self._scored_headlines = []
        self._dropped_headlines = []
        self._pool_diagnostics = self._summarize_raw_pool(
            [], query="multi-provider", now=self.now_utc
        )
        self._pool_diagnostics["editorial_shadow"] = {
            "mode": EDITORIAL_COVERAGE_MODE,
            "candidate_count": 0,
            "fresh_candidate_count": 0,
            "records": [],
        }

    @staticmethod
    def _build_gdelt_queries(max_query_chars=190):
        terms = [
            "stock market",
            "Federal Reserve",
            "inflation",
            "tariff",
            "earnings",
            "semiconductor",
            "uranium",
            "bitcoin",
            "oil",
        ]
        terms += [
            aliases[0] for aliases in GDELT_TICKER_ALIASES.values() if aliases
        ]
        unique = list(dict.fromkeys(term.casefold() for term in terms))
        rendered = [
            f'"{term}"' if " " in term else term for term in unique
        ]
        suffix = " sourcelang:english"

        def build_query(batch):
            return f"({' OR '.join(batch)}){suffix}"

        batches = []
        current = []
        for term in rendered:
            candidate = build_query(current + [term])
            if current and len(candidate) > max_query_chars:
                batches.append(build_query(current))
                current = [term]
                if len(build_query(current)) > max_query_chars:
                    raise ValueError("GDELT term exceeds query-size limit")
            else:
                current.append(term)
        if current:
            batches.append(build_query(current))
        if not batches:
            raise ValueError("GDELT query universe is empty")
        return tuple(batches)

    def _default_providers(self):
        from coverage_policy import fmp_acquisition_tickers
        feeds = []
        for item in HEADLINE_OFFICIAL_FEEDS:
            spec = dict(item)
            spec["parser"] = (
                "nasdaq_halts"
                if spec.pop("feed_kind", None) == "nasdaq_halts"
                else "generic"
            )
            feeds.append(spec)
        providers = [
            OfficialFeedProvider(
                feeds,
                now=self.now_utc,
                timeout=HEADLINE_REQUEST_TIMEOUT_SECONDS,
            ),
            FMPNewsProvider(
                FMP_API_KEY,
                tickers=fmp_acquisition_tickers(EDITORIAL_COVERAGE_MODE),
                now=self.now_utc,
                timeout=HEADLINE_REQUEST_TIMEOUT_SECONDS,
            ),
            AlphaVantageNewsProvider(
                ALPHA_VANTAGE_API_KEY,
                topics=("financial_markets",),
                now=self.now_utc,
                timeout=HEADLINE_REQUEST_TIMEOUT_SECONDS,
            ),
        ]
        if HEADLINE_GDELT_ENABLED:
            providers.append(
                BatchedGDELTNewsProvider(
                    self._build_gdelt_queries(),
                    now=self.now_utc,
                    timeout=HEADLINE_REQUEST_TIMEOUT_SECONDS,
                )
            )
        return providers

    @staticmethod
    def _safe_provider_error(provider, exc):
        message = str(exc)
        for candidate in (
            getattr(provider, "api_key", None),
            FMP_API_KEY,
            ALPHA_VANTAGE_API_KEY,
        ):
            if candidate:
                message = message.replace(str(candidate), "[redacted]")
        message = re.sub(
            r"(?i)(apikey|api_key|token|secret)=([^&\s]+)",
            r"\1=[redacted]",
            message,
        )
        return message[:200]
    def _fetch_primary_results(self):

        if not self._providers:
            return []
        indexed = {}
        with ThreadPoolExecutor(max_workers=min(6, len(self._providers))) as pool:
            futures = {
                pool.submit(provider.fetch): idx
                for idx, provider in enumerate(self._providers)
            }
            for future in as_completed(futures):
                idx = futures[future]
                provider = self._providers[idx]
                try:
                    result = future.result()
                    if not isinstance(result, ProviderResult):
                        raise TypeError("provider did not return ProviderResult")
                except Exception as exc:
                    result = ProviderResult(
                        getattr(provider, "name", type(provider).__name__),
                        "error",
                        [],
                        error=(
                            "unhandled_provider_error: "
                            f"{self._safe_provider_error(provider, exc)}"
                        ),
                    )
                indexed[idx] = result
        return [indexed[idx] for idx in sorted(indexed)]

    def get_global_headlines(self):
        self._evidence_snapshot = None
        primary_results = self._fetch_primary_results()
        results = list(primary_results)
        self._capture_evidence(results)
        raw_primary = [
            dict(row)
            for result in primary_results
            for row in result.records
        ]
        primary = self._process_pool(raw_primary)
        raw = raw_primary
        processed = primary

        google_fallback_used = False
        if (
            self._google_enabled
            and len(primary["selected"]) < self.TARGET_COUNT
        ):
            google_fallback_used = True
            try:
                google_result = self._google_provider.fetch()
                if not isinstance(google_result, ProviderResult):
                    raise TypeError("Google provider did not return ProviderResult")
            except Exception as exc:
                google_result = ProviderResult(
                    "Google News",
                    "error",
                    [],
                    error=(
                        "unhandled_provider_error: "
                        f"{self._safe_provider_error(self._google_provider, exc)}"
                    ),
                )
            results.append(google_result)
            self._capture_evidence(results)
            if google_result.records:
                raw = [
                    dict(row)
                    for result in results
                    for row in result.records
                ]
                processed = self._process_pool(raw)
        if (
            google_fallback_used
            and len(processed["selected"]) < self.TARGET_COUNT
            and results
            and results[-1].name == "Google News"
            and not results[-1].metadata.get("fallback_used")
        ):
            attempts = list(results[-1].metadata.get("attempts") or [])
            extra_records = []
            for query in self.FALLBACK_QUERIES:
                rows, error = self._google_provider.fetch_query(query, 40)
                attempts.append({
                    "query": query,
                    "record_count": len(rows),
                    "error": error,
                })
                extra_records.extend(rows)
                if rows:
                    break
            if extra_records:
                previous = results[-1]
                results[-1] = ProviderResult(
                    previous.name,
                    "ok",
                    previous.records + extra_records,
                    metadata={
                        **previous.metadata,
                        "fallback_used": True,
                        "query": query,
                        "attempts": attempts,
                        "eligibility_fallback_used": True,
                    },
                )
                raw = [dict(row) for result in results for row in result.records]
                self._capture_evidence(results)
                processed = self._process_pool(raw)
            else:
                results[-1].metadata["attempts"] = attempts
                results[-1].metadata["eligibility_fallback_used"] = True
        self._scored_headlines = processed["selected"]
        self._finish_evidence(processed, google_fallback_used)
        self._dropped_headlines = (
            processed["relevance_dropped"]
            + processed["stale_dropped"]
            + processed["duplicate_dropped"]
            + processed["cap_dropped"]
        )

        provider_health = [result.health() for result in results]
        partial_failures = [
            row["name"]
            for row in provider_health
            if row["configured"] and (
                row["status"] == "error" or row.get("partial_failures", 0)
            )
        ]
        configured = [row for row in provider_health if row["configured"]]
        total_failure = bool(configured) and all(
            row["status"] == "error" for row in configured
        )
        fetch_error = (
            "all configured headline providers failed"
            if total_failure else None
        )
        self._pool_diagnostics = self._summarize_raw_pool(
            raw,
            query="multi-provider",
            now=self.now_utc,
            fetch_error=fetch_error,
        )
        google_health = next(
            (row for row in provider_health if row["name"] == "Google News"),
            {},
        )
        self._pool_diagnostics.update({
            "primary_query": self.PRIMARY_QUERY,
            # Backward-compatible meaning: Google's alternate RSS query fired.
            "fallback_used": bool(google_health.get("fallback_used")),
            "attempts": [
                {
                    **row,
                    "fetched_count": row.get(
                        "fetched_count", row.get("record_count", 0)
                    ),
                }
                for row in (google_health.get("attempts") or [])
            ],
            "google_fallback_used": google_fallback_used,
            "providers": provider_health,
            "provider_failures": partial_failures,
            "provider_counts": dict(Counter(
                row.get("provider") or "unknown" for row in raw
            )),
            "publisher_counts": dict(Counter(
                self._publisher_key(row) for row in raw
            )),
            "selected_provider_counts": dict(Counter(
                row.get("provider") or "unknown"
                for row in self._scored_headlines
            )),
            "selected_provider_lane_counts": dict(Counter(
                (
                    f"{row.get('lane') or 'unknown'}:"
                    f"{row.get('provider') or 'unknown'}"
                )
                for row in self._scored_headlines
            )),
            "selected_publisher_counts": dict(Counter(
                self._publisher_key(row) for row in self._scored_headlines
            )),
            "selected_publisher_lane_counts": dict(Counter(
                (
                    f"{row.get('lane') or 'unknown'}:"
                    f"{self._publisher_key(row)}"
                )
                for row in self._scored_headlines
            )),
            "relevant_before_recency": len(processed["relevant"]),
            "fresh_relevance_zero_count": len(processed["fresh_zero"]),
            "fresh_relevance_zero_examples": processed["fresh_zero"][:5],
            "fresh_relevant_count": len(processed["fresh"]),
            "dedupe_input_count": len(processed["fresh"]),
            "dedupe_dropped_count": len(processed["duplicate_dropped"]),
            "eligible_before_caps": len(processed["deduped"]),
            "cap_dropped_count": len(processed["cap_dropped"]),
            "cap_drop_reason_counts": dict(Counter(
                row.get("drop_reason") for row in processed["cap_dropped"]
            )),
            "selected_count": len(self._scored_headlines),
            "no_approved_lane_count": len(
                processed["relevance_dropped"]
            ),
            "accounted_candidate_count": sum(
                len(processed[key])
                for key in (
                    "selected",
                    "relevance_dropped",
                    "stale_dropped",
                    "duplicate_dropped",
                    "cap_dropped",
                )
            ),
            "relevant_lane_counts": dict(Counter(
                row.get("lane") for row in processed["relevant"]
            )),
            "eligible_lane_counts": dict(Counter(
                row.get("lane") for row in processed["deduped"]
            )),
            "selected_lane_counts": dict(Counter(
                row.get("lane") for row in self._scored_headlines
            )),
            "unique_selected_publishers": len({
                self._publisher_key(row) for row in self._scored_headlines
            }),
            "selected_publisher_max_share": self._max_publisher_share(
                self._scored_headlines
            ),
            "google_selected_count": sum(
                row.get("provider") == "Google News"
                for row in self._scored_headlines
            ),
            "editorial_shadow": processed["editorial_shadow"],
        })
        logger.info(
            "Headline pool: fetched=%s fresh=%s selected=%s providers=%s "
            "google_fill=%s",
            self._pool_diagnostics["fetched_count"],
            self._pool_diagnostics["fresh_before_relevance"],
            len(self._scored_headlines),
            self._pool_diagnostics["provider_counts"],
            google_fallback_used,
        )
        return [row["text"] for row in self._scored_headlines]

    def get_scored_headlines(self):
        return [dict(row) for row in self._scored_headlines]

    def get_dropped_headlines(self):
        return [
            {
                **row,
                "as_of": row.get("as_of") or to_utc_z(self._record_time(row)),
            }
            for row in self._dropped_headlines
        ]

    def get_pool_diagnostics(self):
        diagnostics = dict(self._pool_diagnostics)
        if self._evidence_snapshot is not None:
            diagnostics["evidence_intake"] = deepcopy(self._evidence_snapshot)
        for key in (
            "fresh_relevance_zero_examples",
            "attempts",
            "providers",
        ):
            diagnostics[key] = [
                dict(row) for row in diagnostics.get(key, [])
            ]
        shadow = dict(diagnostics.get("editorial_shadow") or {})
        shadow["records"] = [
            dict(row) for row in shadow.get("records", [])
        ]
        diagnostics["editorial_shadow"] = shadow
        return diagnostics

    def _capture_evidence(self, results):
        if self._intake_mode == "off":
            return
        import evidence_intake
        try:
            self._evidence_snapshot = evidence_intake.capture(
                results, self.now_utc, EDITORIAL_COVERAGE_MODE)
        except Exception as exc:
            self._intake_failed(type(exc).__name__)

    def get_evidence_candidates(self):
        """Unreviewed pre-display metadata; never input to production focus/signals."""
        snapshot = self._evidence_snapshot or {}
        return deepcopy(snapshot.get("records", []))

    def _finish_evidence(self, processed, google_requested):
        if self._evidence_snapshot is None or self._evidence_snapshot.get("status") != "ok":
            return
        import evidence_intake
        try:
            self._evidence_snapshot = evidence_intake.finish(
                self._evidence_snapshot, processed, google_requested)
        except Exception as exc:
            self._intake_failed(type(exc).__name__)

    def _intake_failed(self, reason):
        from evidence_intake import VERSION
        logger.warning("Shadow evidence intake failed: %s", reason)
        self._evidence_snapshot = {
            "version": VERSION, "mode": "shadow", "status": "error",
            "signal_eligible": False, "reason": "intake_failed:" + reason,
        }

    @classmethod
    def _record_time(cls, item):
        if item.get("published"):
            return item.get("published")
        if item.get("source_time_kind") == "provider_seen":
            return item.get("provider_seen_at")
        return None

    @classmethod
    def _summarize_raw_pool(cls, items, query, now=None, fetch_error=None):
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        cutoff = to_utc_z(
            now.astimezone(timezone.utc)
            - timedelta(days=cls.HEADLINES_MAX_AGE_DAYS)
        )
        stamps = [to_utc_z(cls._record_time(item)) for item in items]
        dated = [stamp for stamp in stamps if stamp is not None]
        return {
            "query": query,
            "fetched_count": len(items),
            "dated_count": len(dated),
            "undated_count": len(items) - len(dated),
            "newest_as_of": max(dated, default=None),
            "oldest_as_of": min(dated, default=None),
            "recency_cutoff": cutoff,
            "fresh_before_relevance": sum(
                1 for stamp in dated if stamp >= cutoff
            ),
            "relevant_before_recency": 0,
            "fresh_relevance_zero_count": 0,
            "fresh_relevance_zero_examples": [],
            "fresh_relevant_count": 0,
            "fetch_error": fetch_error,
            "primary_query": cls.PRIMARY_QUERY,
            "fallback_used": False,
            "attempts": [],
            "google_fallback_used": False,
            "providers": [],
            "provider_failures": [],
            "provider_counts": {},
            "publisher_counts": {},
            "selected_provider_counts": {},
            "selected_provider_lane_counts": {},
            "selected_publisher_counts": {},
            "selected_publisher_lane_counts": {},
            "dedupe_input_count": 0,
            "dedupe_dropped_count": 0,
            "eligible_before_caps": 0,
            "cap_dropped_count": 0,
            "cap_drop_reason_counts": {},
            "selected_count": 0,
            "accounted_candidate_count": 0,
            "no_approved_lane_count": 0,
            "relevant_lane_counts": {},
            "eligible_lane_counts": {},
            "selected_lane_counts": {},
            "unique_selected_publishers": 0,
            "selected_publisher_max_share": 0.0,
            "google_selected_count": 0,
        }


    @classmethod
    def _drop_stale(cls, records, now=None):
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        cutoff = to_utc_z(now - timedelta(days=cls.HEADLINES_MAX_AGE_DAYS))
        fresh, dropped = [], []
        for rec in records:
            stamp = to_utc_z(cls._record_time(rec))
            if stamp is None:
                dropped.append({
                    **rec, "as_of": None, "drop_reason": "undated"
                })
            elif stamp < cutoff:
                dropped.append({
                    **rec, "as_of": stamp, "drop_reason": "stale"
                })
            else:
                fresh.append(rec)
        return fresh, dropped

    @classmethod
    def _is_listing_symbol(cls, symbol):
        return bool(symbol) and symbol.upper() not in cls.LISTING_SUBJECT_WORDS

    @classmethod
    def _is_exchange_subject_match(cls, match, title):
        if match.group("open") and match.group("close"):
            return False
        prefix = unicodedata.normalize(
            "NFKC", (title or "")[:match.start()]
        ).casefold()
        if cls._macro_subject_text(prefix):
            return False
        raw_subject = unicodedata.normalize(
            "NFKC", (title or "")[match.start("symbol"):]
        ).casefold()
        subject_text = cls._macro_subject_text(raw_subject)
        return bool(
            cls.EXCHANGE_PREFIX_MACRO_SUBJECT_PATTERN.search(subject_text)
            or cls._macro_subject_admitted(
                raw_subject, subject_text, ()
            )
        )

    @classmethod
    def _is_listing_match(cls, match, title):
        return (
            cls._is_listing_symbol(match.group("symbol"))
            and not cls._is_exchange_subject_match(match, title)
        )

    @classmethod
    def _listing_symbols(cls, title):
        symbols = []
        for match in cls.EXCHANGE_LISTING_PATTERN.finditer(title or ""):
            if cls._is_listing_match(match, title):
                symbols.append(match.group("symbol").upper())
        for match in cls.EXCHANGE_LISTED_SYMBOL_PATTERN.finditer(title or ""):
            symbol = match.group("symbol").upper()
            if cls._is_listing_symbol(symbol):
                symbols.append(symbol)
        return list(dict.fromkeys(symbols))

    @classmethod
    def _strip_exchange_listing_metadata(cls, title):
        def strip_listing(match):
            if cls._is_listing_match(match, title):
                return " "
            return match.group(0)

        normalized = cls.EXCHANGE_LISTING_PATTERN.sub(
            strip_listing, title or ""
        )
        normalized = cls.EXCHANGE_LISTED_PATTERN.sub(" ", normalized)
        return re.sub(r"\s+", " ", normalized).strip()

    @staticmethod
    def _has_keyword(text, keyword):
        return bool(re.search(
            rf"\b{re.escape(keyword)}(?:e?s)?\b", text
        ))

    @staticmethod
    def _has_any_pattern(text, patterns):
        return any(re.search(pattern, text) for pattern in patterns)

    @classmethod
    def _has_title_ticker_context(cls, text, ticker):
        escaped = re.escape(ticker)
        patterns = (
            rf"\${escaped}(?![A-Za-z0-9])",
            rf"\(\s*{escaped}\s*\)",
            rf"\b(?:ticker|symbol)\s*[:#-]?\s*{escaped}(?![A-Za-z0-9])",
            rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])\s+"
            r"(?:stock|shares?|etf|price|closes?|closed|rises?|rose|falls?|"
            r"fell|gains?|gained|trades?|trading|surges?|surged|drops?|"
            r"dropped|earnings|guidance)\b",
            r"\b(?:stock|shares?|etf|price)\b.{0,16}"
            rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])",
        )
        return any(
            re.search(pattern, text, flags=re.IGNORECASE)
            for pattern in patterns
        )

    @classmethod
    def _mapping_universe(cls):
        return (
            SIGNAL_ELIGIBLE_TICKER_SET
            if EDITORIAL_COVERAGE_MODE == "off"
            else frozenset(EDITORIAL_COVERAGE_TICKERS)
        )

    @classmethod
    def _coverage_metadata(cls, tickers):
        mapped = {
            str(ticker).strip().upper()
            for ticker in (tickers or [])
            if str(ticker).strip()
        }
        core = mapped & SIGNAL_ELIGIBLE_TICKER_SET
        editorial = mapped & EDITORIAL_ONLY_TICKER_SET
        if core and editorial:
            return {
                "coverage_tier": "mixed",
                "signal_eligible": False,
                "signal_eligibility_reason": (
                    "mixed_coverage_requires_ticker_filter"
                ),
            }
        if core:
            return {
                "coverage_tier": "instrumented",
                "signal_eligible": True,
                "signal_eligibility_reason": "instrumented_universe",
            }
        if editorial:
            return {
                "coverage_tier": "editorial_only",
                "signal_eligible": False,
                "signal_eligibility_reason": "editorial_only_coverage",
            }
        return {
            "coverage_tier": None,
            "signal_eligible": False,
            "signal_eligibility_reason": "no_mapped_ticker",
        }

    @classmethod
    def _editorial_tickers(cls, row):
        return sorted({
            str(ticker).strip().upper()
            for ticker in (row.get("universe_tickers") or [])
            if str(ticker).strip().upper() in EDITORIAL_ONLY_TICKER_SET
        })

    @classmethod
    def _project_shadow_row_to_core(cls, row):
        core_tickers = sorted(
            set(row.get("universe_tickers") or [])
            & SIGNAL_ELIGIBLE_TICKER_SET
        )
        if not core_tickers:
            return None
        projected = dict(row)
        components = dict(projected.get("score_components") or {})
        components["issuer_relevance"] = 2 * len(core_tickers)
        projected.update({
            "universe_tickers": core_tickers,
            "score_components": components,
            "relevance": cls._compatibility_relevance(components),
            **cls._coverage_metadata(core_tickers),
        })
        projected["lane"] = cls._assign_lane(projected, components)
        return projected

    @classmethod
    def _editorial_shadow_diagnostics(cls, candidates, fresh_candidates):

        records = []
        for row in sorted(fresh_candidates, key=cls._selection_key):
            editorial_tickers = cls._editorial_tickers(row)
            core_tickers = (
                set(row.get("universe_tickers") or [])
                & SIGNAL_ELIGIBLE_TICKER_SET
            )
            records.append({
                "source_record_id": row.get("source_record_id"),
                "tickers": editorial_tickers,
                "title": row.get("title"),
                "link": row.get("link"),
                "as_of": to_utc_z(cls._record_time(row)),
                "lane": row.get("lane"),
                "score_components": dict(
                    row.get("score_components") or {}
                ),
                "disposition": (
                    "core_projection" if core_tickers else "suppressed"
                ),
                "reason": (
                    "editorial_mapping_shadowed" if core_tickers
                    else "editorial_coverage_shadow_mode"
                ),
            })
        return {
            "mode": EDITORIAL_COVERAGE_MODE,
            "candidate_count": len(candidates),
            "fresh_candidate_count": len(fresh_candidates),
            "records": records[:cls.SHADOW_AUDIT_LIMIT],
        }

    @classmethod
    def _matched_universe_tickers(cls, item):
        title = item.get("title") or ""
        normalized = cls._strip_exchange_listing_metadata(title)
        normalized_lower = normalized.casefold()
        universe = cls._mapping_universe()
        metadata_kind = str(
            item.get("ticker_metadata_kind", "subject") or ""
        ).strip().casefold()
        raw_tickers = (
            item.get("tickers") or []
            if metadata_kind == "subject"
            else []
        )
        if isinstance(raw_tickers, str):
            raw_tickers = re.split(r"[,\s]+", raw_tickers)
        explicit_symbols = {
            str(ticker).strip().upper()
            for ticker in raw_tickers
            if str(ticker).strip()
        }
        explicit_symbols.update(cls._listing_symbols(title))

        matched = explicit_symbols & universe
        for ticker, aliases in cls.DISPLAY_TICKER_ALIASES.items():
            if ticker not in universe:
                continue
            display_symbols = {alias.upper() for alias in aliases}
            if explicit_symbols & display_symbols:
                matched.add(ticker)

        for ticker in sorted(universe):
            symbol_match = re.search(
                rf"(?<![A-Za-z0-9]){re.escape(ticker)}(?![A-Za-z0-9])",
                normalized,
            )
            if not symbol_match:
                continue
            if (
                ticker in cls.AMBIGUOUS_TITLE_TICKERS
                and not cls._has_title_ticker_context(normalized, ticker)
            ):
                continue
            matched.add(ticker)
        for ticker, aliases in cls.DISPLAY_TICKER_ALIASES.items():
            if ticker not in universe:
                continue
            if any(
                re.search(
                    rf"(?<![A-Za-z0-9]){re.escape(alias)}"
                    rf"(?![A-Za-z0-9])",
                    normalized,
                )
                for alias in aliases
            ):
                matched.add(ticker)
        for ticker, aliases in TICKER_ALIASES.items():
            if ticker not in universe:
                continue
            if any(
                re.search(rf"\b{re.escape(alias)}\b", normalized_lower)
                for alias in aliases
            ):
                matched.add(ticker)

        # German Merck also uses MRK on some venues. Neither a provider tag
        # nor ticker-context prose may override explicit conflicting identity.
        if "MRK" in matched and re.search(
            r"\b(?:merck\s+kgaa|darmstadt|xetra|frankfurt)\b|\b(?:etr|fra)\s*:\s*mrk\b",
            title, re.IGNORECASE,
        ):
            us_identity = re.search(
                r"\bmerck\s+(?:&|and)\s+co\b|\bnyse\s*:\s*mrk\b",
                title, re.IGNORECASE,
            )
            if not us_identity:
                matched.discard("MRK")
        return sorted(matched)
    @classmethod
    def _has_current_clinical_outcome(cls, text):
        """Return true only for a locally current, occurred trial outcome."""
        for outcome in cls.CLINICAL_OUTCOME_OCCURRED_PATTERN.finditer(text):
            context_start = max(0, outcome.start() - 80)
            context_end = min(len(text), outcome.end() + 24)
            if not cls.CLINICAL_OUTCOME_CONTEXT_PATTERN.search(
                text[context_start:context_end]
            ):
                continue

            # Only modifiers before the result change its semantics. A genuine
            # result may say it "will present data" or "beat a rival" later.
            prefix = text[max(0, outcome.start() - 120):outcome.start()]
            if cls.CLINICAL_OUTCOME_PREFIX_EXCLUSION_PATTERN.search(prefix):
                continue

            # Historical suffixes describe an old result even when the quote
            # lacks a leading verb such as "recalled".
            suffix = text[outcome.end():min(len(text), outcome.end() + 48)]
            if cls.CLINICAL_OUTCOME_SUFFIX_HISTORY_PATTERN.search(suffix):
                continue
            return True
        return False


    @classmethod
    def _macro_subject_text(cls, text):
        subject_text = (text or "").strip()
        while subject_text:
            previous = subject_text
            subject_text = cls.EDITORIAL_SUBJECT_PREFIX_PATTERN.sub(
                "", subject_text
            )
            subject_text = cls.EXCHANGE_LABEL_SUBJECT_PATTERN.sub(
                "", subject_text
            ).lstrip()
            if subject_text == previous:
                break
        return subject_text

    @classmethod
    def _has_strict_macro_subject(cls, subject_text):
        subject_text = subject_text or ""
        category_subject = re.sub(
            r"^the\s+", "", subject_text, count=1
        )
        if cls.CORPORATE_LEAD_DESIGNATOR_PATTERN.search(subject_text):
            return False
        return bool(
            cls.POLICY_INSTITUTION_SUBJECT_PATTERN.search(subject_text)
            or cls.NOMINAL_MACRO_SUBJECT_PATTERN.search(subject_text)
            or cls.POLICY_SPOKESPERSON_SUBJECT_PATTERN.search(subject_text)
            or cls.INFLATION_SUBJECT_LEAD_PATTERN.search(subject_text)
            or cls.ECONOMIC_DATA_SUBJECT_PATTERN.search(subject_text)
            or cls.RATE_RECESSION_SUBJECT_PATTERN.search(subject_text)
            or cls.OPEC_SUBJECT_LEAD_PATTERN.search(category_subject)
            or cls.INDEX_SUBJECT_LEAD_PATTERN.search(category_subject)
            or cls.CPI_SUBJECT_LEAD_PATTERN.search(subject_text)
            or cls.LEADING_MARKET_ACTION_PATTERN.search(subject_text)
            or cls.POWELL_POLICY_SUBJECT_PATTERN.search(category_subject)
            or cls.TREASURY_SUBJECT_LEAD_PATTERN.search(category_subject)
            or any(
                re.match(pattern, category_subject)
                for pattern in cls.PAYROLL_SUBJECT_PATTERNS
            )
            or any(
                re.match(pattern, category_subject)
                for pattern in cls.TARIFF_SUBJECT_PATTERNS
            )
            or any(
                re.match(pattern, category_subject)
                for pattern in cls.CHINA_MACRO_PATTERNS
            )
            or cls.WALL_STREET_SUBJECT_LEAD_PATTERN.search(category_subject)
            or cls.DOW_SUBJECT_LEAD_PATTERN.search(category_subject)
            or cls.OIL_SUBJECT_LEAD_PATTERN.search(category_subject)
            or cls.CRYPTO_SUBJECT_LEAD_PATTERN.search(category_subject)
        )

    @classmethod
    def _macro_subject_admitted(
        cls, normalized_text, subject_text, universe_tickers
    ):
        if cls.CORPORATE_LEAD_DESIGNATOR_PATTERN.search(subject_text):
            return False
        labeled_subject = cls.EDITORIAL_SUBJECT_PREFIX_PATTERN.sub(
            "", (normalized_text or "").strip()
        )
        category_subject = re.sub(
            r"^the\s+", "", subject_text or "", count=1
        )
        return bool(
            cls.HUMAN_MACRO_SUBJECT_PATTERN.search(subject_text)
            or cls.POLICY_ACTOR_MACRO_PATTERN.search(subject_text)
            or cls._has_strict_macro_subject(subject_text)
            or cls.EXCHANGE_LEADING_SUBJECT_PATTERN.match(labeled_subject)
            or cls.EXCHANGE_LEADING_SUBJECT_PATTERN.match(subject_text)
            or cls.EXCHANGE_PREFIX_MACRO_SUBJECT_PATTERN.search(subject_text)
            or cls.EXCHANGE_REVERSE_SUBJECT_PATTERN.match(category_subject)
            or (
                universe_tickers
                and cls.ISSUER_POLICY_DRIVER_PATTERN.search(normalized_text)
            )
        )

    @classmethod
    def _score_components(cls, item):
        title = item.get("title") or ""
        normalized = cls._strip_exchange_listing_metadata(title)
        normalized_lower = normalized.casefold()
        subject_text = cls._macro_subject_text(normalized_lower)
        universe_tickers = cls._matched_universe_tickers(item)
        outside_listing = bool(
            set(cls._listing_symbols(title)) - cls._mapping_universe()
        ) and not universe_tickers

        excluded_keywords = (
            cls.COMPANY_CONTEXT_ONLY_KEYWORDS
            | cls.CONTEXTUAL_MACRO_KEYWORDS
        )
        macro_hits = sum(
            1
            for keyword in MACRO_KEYWORDS
            if keyword not in excluded_keywords
            and cls._has_keyword(normalized_lower, keyword)
        )
        macro_hits += cls._has_any_pattern(
            normalized_lower, cls.TREASURY_PATTERNS
        )
        macro_hits += cls._has_any_pattern(
            normalized_lower, cls.YIELD_CONTEXT_PATTERNS
        )
        macro_hits += bool(cls.EXCHANGE_SUBJECT_PATTERN.search(normalized_lower))
        macro_hits += bool(
            cls.EXCHANGE_REVERSE_SUBJECT_PATTERN.search(normalized_lower)
        )
        macro_hits += cls._has_any_pattern(
            normalized_lower, cls.INFLATION_SUBJECT_PATTERNS
        )
        macro_hits += cls._has_any_pattern(
            normalized_lower, cls.PAYROLL_SUBJECT_PATTERNS
        )
        macro_hits += cls._has_any_pattern(
            normalized_lower, cls.TARIFF_SUBJECT_PATTERNS
        )
        macro_hits += cls._has_any_pattern(
            normalized_lower, cls.WALL_STREET_SUBJECT_PATTERNS
        )
        dow_subject_text = cls.DOW_COMPANY_PATTERN.sub(
            " ", normalized_lower
        )
        macro_hits += cls._has_any_pattern(
            dow_subject_text, cls.DOW_SUBJECT_PATTERNS
        )
        macro_hits += cls._has_any_pattern(
            normalized_lower, cls.CHINA_MACRO_PATTERNS
        )
        macro_hits += cls._has_any_pattern(
            normalized_lower, cls.OIL_SUBJECT_PATTERNS
        )
        macro_hits += cls._has_any_pattern(
            normalized_lower, cls.CRYPTO_SUBJECT_PATTERNS
        )
        macro_hits += bool(
            cls.HUMAN_MACRO_SUBJECT_PATTERN.search(subject_text)
        )
        # Macro evidence is admissible only when the headline owns a macro
        # subject. Ambiguous category names must pair with their own leading
        # action/evidence; unrelated macro phrases later in an entity title
        # cannot create a macro lane. Every admitted subject supplies one
        # evidence point even when legacy keyword patterns omit its morphology.
        macro_subject_admitted = cls._macro_subject_admitted(
            normalized_lower,
            subject_text,
            universe_tickers,
        )
        if outside_listing or not macro_subject_admitted:
            macro_hits = 0
        else:
            macro_hits = max(1, macro_hits)

        vertical_hits = sum(
            1
            for terms in cls.VERTICAL_KEYWORDS.values()
            if any(cls._has_keyword(normalized_lower, term) for term in terms)
        )
        clinical_outcome_impact = 0
        if cls._has_current_clinical_outcome(normalized_lower):
            clinical_outcome_impact = 2
        impact = min(3, sum(
            weight
            for pattern, weight in cls.IMPACT_PATTERNS
            if re.search(pattern, normalized_lower)
        ) + clinical_outcome_impact)
        return {
            "issuer_relevance": 2 * len(universe_tickers),
            "macro_relevance": macro_hits,
            "vertical_relevance": vertical_hits,
            "authority": cls.AUTHORITY_SCORES.get(
                item.get("source_class") or "aggregator", 0
            ),
            # Pool-relative novelty is injected once the complete fetched
            # candidate cohort is available.
            "novelty": 0,
            "impact": impact,
        }

    @classmethod
    def _assign_lane(cls, item, components=None):
        components = components or cls._score_components(item)
        if components["issuer_relevance"] > 0:
            return "universe"
        if components["macro_relevance"] > 0:
            return "macro"
        if (
            components["impact"] >= 2
            and (
                components["vertical_relevance"] > 0
                or components["authority"] >= 3
            )
        ):
            return "discovery"
        return None

    @staticmethod
    def _compatibility_relevance(components):
        return (
            components["issuer_relevance"]
            + components["macro_relevance"]
            + components["vertical_relevance"]
        )

    @classmethod
    def _selection_key(cls, row, stable_index=0):
        components = row.get("score_components") or {}
        return (
            cls.LANE_PRIORITY.get(row.get("lane"), 9),
            -components.get("issuer_relevance", 0),
            -components.get("macro_relevance", 0),
            -components.get("vertical_relevance", 0),
            -components.get("impact", 0),
            -components.get("authority", 0),
            -components.get("novelty", 0),
            -cls._sort_stamp(row),
            row.get("source_record_id") or "",
            stable_index,
        )

    @classmethod
    def _prepare_story(cls, row):
        """Build the normalized identity inputs once for one fetched row."""
        normalized_title = cls._normalized_title(row)
        return (
            row.get("canonical_url") or "",
            normalized_title,
            frozenset(normalized_title.split()),
        )

    @staticmethod
    def _prepared_same_story(left, right):
        left_url, left_title, left_tokens = left
        right_url, right_title, right_tokens = right
        if left_url and right_url and left_url == right_url:
            return True
        if left_title and left_title == right_title:
            return True
        smaller = min(len(left_tokens), len(right_tokens))
        larger = max(len(left_tokens), len(right_tokens))
        if smaller < 6 or smaller * 100 < larger * 85:
            return False
        intersection = len(left_tokens & right_tokens)
        union_size = len(left_tokens) + len(right_tokens) - intersection
        return intersection * 100 >= union_size * 85

    @classmethod
    def _story_group_ids(cls, items):
        """Return transitive same-story group ids using cached signatures."""
        items = list(items)
        prepared = [cls._prepare_story(item) for item in items]
        parent = list(range(len(items)))

        def find(index):
            while parent[index] != index:
                parent[index] = parent[parent[index]]
                index = parent[index]
            return index

        def union(left_index, right_index):
            left_root = find(left_index)
            right_root = find(right_index)
            if left_root == right_root:
                return
            if left_root > right_root:
                left_root, right_root = right_root, left_root
            parent[right_root] = left_root

        url_owner = {}
        title_owner = {}
        for index, (url, normalized_title, _tokens) in enumerate(prepared):
            for value, owners in (
                (url, url_owner),
                (normalized_title, title_owner),
            ):
                if not value:
                    continue
                previous = owners.setdefault(value, index)
                if previous != index:
                    union(index, previous)

        for left_index, left in enumerate(prepared):
            for right_index in range(left_index + 1, len(prepared)):
                if find(left_index) == find(right_index):
                    continue
                if cls._prepared_same_story(left, prepared[right_index]):
                    union(left_index, right_index)
        return [find(index) for index in range(len(items))]

    @classmethod
    def _score_pool_components(cls, items, story_group_ids=None):
        """Score candidates with deterministic fetched-pool novelty.

        Novelty is one only for a singleton transitive story group in this
        fetched cohort. Every syndicated/same-story group member receives zero.
        This is not a longitudinal claim about whether an event happened before.
        """
        items = list(items)
        if story_group_ids is None:
            story_group_ids = cls._story_group_ids(items)
        if len(story_group_ids) != len(items):
            raise ValueError("story group count must match candidate count")
        group_sizes = Counter(story_group_ids)
        scores = [cls._score_components(item) for item in items]
        for components, group_id in zip(scores, story_group_ids):
            components["novelty"] = int(group_sizes[group_id] == 1)
        return scores

    @classmethod
    def _select_relevant(
        cls,
        items,
        top_n=5,
        pool_components=None,
        story_group_ids=None,
    ):
        items = list(items)
        if story_group_ids is None:
            story_group_ids = cls._story_group_ids(items)
        if len(story_group_ids) != len(items):
            raise ValueError("story group count must match candidate count")
        if pool_components is None:
            pool_components = cls._score_pool_components(
                items, story_group_ids=story_group_ids
            )
        if len(pool_components) != len(items):
            raise ValueError("pool component count must match candidate count")

        scored = []
        for idx, (item, components) in enumerate(zip(items, pool_components)):
            title = item.get("title") or ""
            components = dict(components)
            lane = cls._assign_lane(item, components)
            if lane is None:
                continue
            link = item.get("link")
            record = dict(item)
            universe_tickers = cls._matched_universe_tickers(item)
            record.update({
                "text": f"{title} ({link})" if link else title,
                "title": title,
                "link": link,
                "published": item.get("published"),
                "relevance": cls._compatibility_relevance(components),
                "lane": lane,
                "universe_tickers": universe_tickers,
                "score_components": components,
                "_story_group_id": story_group_ids[idx],
                **cls._coverage_metadata(universe_tickers),
            })
            scored.append((cls._selection_key(record, idx), record))
        scored.sort(key=lambda row: row[0])
        if top_n is not None:
            scored = scored[:top_n]
        return [row[1] for row in scored]

    @staticmethod
    def _sort_stamp(record):
        stamp = to_utc_z(NewsAgent._record_time(record))
        if not stamp:
            return 0
        try:
            return int(datetime.fromisoformat(
                stamp.replace("Z", "+00:00")
            ).timestamp())
        except ValueError:
            return 0

    @classmethod
    def _score_title(cls, title):
        return cls._compatibility_relevance(
            cls._score_components({"title": title})
        )

    @classmethod
    def _normalized_title(cls, row):
        text = unicodedata.normalize("NFKC", row.get("title") or "").casefold()
        publisher = (row.get("publisher") or "").casefold().strip()
        domain = (row.get("publisher_domain") or "").casefold().strip()
        suffix = text.rsplit(" - ", 1)[-1] if " - " in text else ""
        if suffix and suffix in {publisher, domain, domain.split(".", 1)[0]}:
            text = text.rsplit(" - ", 1)[0]
        text = re.sub(r"\b(?:breaking|update|exclusive)\b", " ", text)
        text = re.sub(r"[^a-z0-9]+", " ", text)
        return " ".join(text.split())

    @classmethod
    def _title_tokens(cls, row):
        return set(cls._normalized_title(row).split())

    @classmethod
    def _same_story(cls, left, right):
        return cls._prepared_same_story(
            cls._prepare_story(left),
            cls._prepare_story(right),
        )

    @classmethod
    def _representative_key(cls, row):
        source_class = row.get("source_class") or "aggregator"
        return (
            cls.SOURCE_PRIORITY.get(source_class, 4),
            1 if row.get("provider") == "Google News" else 0,
            -row.get("relevance", 0),
            -cls._sort_stamp(row),
            row.get("provider") or "",
            row.get("canonical_url") or "",
            row.get("source_record_id") or "",
        )

    @classmethod
    def _merge_duplicate_group(cls, group):
        group.sort(key=cls._representative_key)
        representative = dict(group[0])
        representative_priority = cls.SOURCE_PRIORITY.get(
            representative.get("source_class") or "aggregator", 4
        )
        classification_group = [
            row for row in group
            if cls.SOURCE_PRIORITY.get(
                row.get("source_class") or "aggregator", 4
            ) == representative_priority
        ]
        component_names = (
            "issuer_relevance",
            "macro_relevance",
            "vertical_relevance",
            "authority",
            "novelty",
            "impact",
        )
        merged_components = {
            name: max(
                (row.get("score_components") or {}).get(name, 0)
                for row in classification_group
            )
            for name in component_names
        }
        universe_tickers = sorted({
            ticker
            for row in classification_group
            for ticker in (row.get("universe_tickers") or [])
        })
        merged_components["issuer_relevance"] = max(
            merged_components["issuer_relevance"],
            2 * len(universe_tickers),
        )
        representative.update({
            "universe_tickers": universe_tickers,
            "score_components": merged_components,
            "relevance": cls._compatibility_relevance(merged_components),
            **cls._coverage_metadata(universe_tickers),
        })
        representative["lane"] = cls._assign_lane(
            representative, merged_components
        )
        if len(group) > 1:
            representative["duplicate_providers"] = sorted({
                row.get("provider") for row in group
                if row.get("provider")
            })
            representative["duplicate_publishers"] = sorted({
                row.get("publisher") for row in group
                if row.get("publisher")
            })
        return representative

    @classmethod
    def _deduplicate(cls, rows):
        rows = list(rows)
        if not rows:
            return [], []
        if all("_story_group_id" in row for row in rows):
            story_group_ids = [
                row["_story_group_id"] for row in rows
            ]
        else:
            story_group_ids = cls._story_group_ids(rows)

        grouped = {}
        for group_id, row in zip(story_group_ids, rows):
            grouped.setdefault(group_id, []).append(row)
        groups = sorted(
            grouped.values(),
            key=lambda group: min(
                cls._representative_key(row) for row in group
            ),
        )

        kept, dropped = [], []
        for group in groups:
            representative = cls._merge_duplicate_group(group)
            representative.pop("_story_group_id", None)
            kept.append(representative)
            for row in group[1:]:
                duplicate = dict(row)
                duplicate.pop("_story_group_id", None)
                duplicate.update({
                    "drop_reason": "duplicate",
                    "kept_source_record_id": representative.get(
                        "source_record_id"
                    ),
                })
                dropped.append(duplicate)
        kept.sort(key=cls._selection_key)
        dropped.sort(key=lambda row: (
            row.get("kept_source_record_id") or "",
            cls._representative_key(row),
        ))
        return kept, dropped

    @classmethod
    def _publisher_key(cls, row):
        return (
            row.get("publisher_domain")
            or row.get("publisher")
            or row.get("provider")
            or "unknown"
        ).casefold()

    @classmethod
    def _select_diverse(cls, rows):
        selected, dropped = [], []
        publisher_lane_counts = Counter()
        provider_counts = Counter()
        provider_lane_counts = Counter()
        google_count = 0
        press_release_count = 0
        for row in rows:
            if len(selected) >= cls.TARGET_COUNT:
                dropped.append({
                    **row, "drop_reason": "selection_limit"
                })
                continue
            publisher = cls._publisher_key(row)
            provider = (row.get("provider") or "unknown").casefold()
            lane = row.get("lane") or "unknown"
            source_class = row.get("source_class")
            publisher_lane = (publisher, lane)
            if (
                publisher_lane_counts[publisher_lane]
                >= cls.MAX_PER_PUBLISHER
            ):
                dropped.append({
                    **row, "drop_reason": "publisher_cap"
                })
                continue
            if (
                row.get("provider") == "Google News"
                and google_count >= cls.GOOGLE_MAX_SELECTED
            ):
                dropped.append({
                    **row, "drop_reason": "google_provider_cap"
                })
                continue
            if source_class == "press_release" and press_release_count >= 1:
                dropped.append({
                    **row, "drop_reason": "press_release_cap"
                })
                continue
            if provider_counts[provider] >= cls.MAX_PER_PROVIDER:
                dropped.append({
                    **row, "drop_reason": "provider_cap"
                })
                continue
            provider_lane = (provider, lane)
            if (
                provider_lane_counts[provider_lane]
                >= cls.MAX_PER_PROVIDER_PER_LANE
            ):
                dropped.append({
                    **row, "drop_reason": "provider_lane_cap"
                })
                continue
            selected.append(row)
            publisher_lane_counts[publisher_lane] += 1
            provider_counts[provider] += 1
            provider_lane_counts[provider_lane] += 1
            google_count += row.get("provider") == "Google News"
            press_release_count += source_class == "press_release"
        return selected, dropped

    @classmethod
    def _max_publisher_share(cls, rows):
        if not rows:
            return 0.0
        counts = Counter(cls._publisher_key(row) for row in rows)
        return round(max(counts.values()) / len(rows), 4)

    def get_specific_news(self, query):
        return self._fetch_rss(query)

    def get_ticker_news(self, ticker):
        return self._fetch_rss(
            f"{ticker} stock price OR earnings OR analysis", limit=5
        )

    def _fetch_rss_records(self, query, limit=5):
        return self._google_provider.fetch_query(query, limit)

    def _fetch_rss(self, query, limit=5):
        records, error = self._fetch_rss_records(query, limit)
        if error:
            return [f"Error connecting to News Stream: {error}"]
        return [
            f"{row['title']} ({row['link']})"
            if row.get("link") else row["title"]
            for row in records
        ]

    def _process_pool(self, raw, *, shadow_projection=True):
        raw = list(raw)
        story_group_ids = self._story_group_ids(raw)
        pool_components = self._score_pool_components(
            raw, story_group_ids=story_group_ids
        )
        all_relevant = self._select_relevant(
            raw,
            top_n=None,
            pool_components=pool_components,
            story_group_ids=story_group_ids,
        )
        shadow_candidates = []
        shadow_suppressed = []
        relevant = []
        for row in all_relevant:
            editorial_tickers = self._editorial_tickers(row)
            if shadow_projection and EDITORIAL_COVERAGE_MODE == "shadow" and editorial_tickers:
                shadow_candidates.append(row)
                projected = self._project_shadow_row_to_core(row)
                if projected is None:
                    shadow_suppressed.append(row)
                else:
                    relevant.append(projected)
                continue
            relevant.append(row)
        shadow_fresh, _shadow_stale = self._drop_stale(
            shadow_candidates, now=self.now_utc
        )
        suppressed_fresh, suppressed_stale = self._drop_stale(
            shadow_suppressed, now=self.now_utc
        )
        editorial_shadow = self._editorial_shadow_diagnostics(
            shadow_candidates, shadow_fresh
        )
        cutoff = to_utc_z(
            self.now_utc - timedelta(days=self.HEADLINES_MAX_AGE_DAYS)
        )
        relevance_dropped = []
        fresh_zero = []
        for row in suppressed_fresh:
            relevance_dropped.append({
                **row,
                "as_of": to_utc_z(self._record_time(row)),
                "drop_reason": "editorial_shadow_suppressed",
            })
        for item, components in zip(raw, pool_components):
            stamp = to_utc_z(self._record_time(item))
            relevance = self._compatibility_relevance(components)
            if self._assign_lane(item, components) is None:
                dropped = {
                    **item,
                    "as_of": stamp,
                    "relevance": relevance,
                    "lane": None,
                    "universe_tickers": self._matched_universe_tickers(item),
                    "score_components": dict(components),
                    "drop_reason": "no_approved_lane",
                }
                relevance_dropped.append(dropped)
                if (
                    relevance == 0
                    and stamp is not None
                    and stamp >= cutoff
                ):
                    fresh_zero.append({
                        "title": item.get("title"),
                        "as_of": stamp,
                        "provider": item.get("provider"),
                        "publisher": item.get("publisher"),
                        "reason": dropped["drop_reason"],
                    })
        fresh, stale_dropped = self._drop_stale(
            relevant, now=self.now_utc
        )
        stale_dropped.extend(suppressed_stale)
        deduped, duplicate_dropped = self._deduplicate(fresh)
        selected, cap_dropped = self._select_diverse(deduped)
        for cohort in (all_relevant, fresh, stale_dropped):
            for row in cohort:
                row.pop("_story_group_id", None)
        result = {
            "relevant": all_relevant,
            "fresh": fresh,
            "fresh_zero": fresh_zero,
            "relevance_dropped": relevance_dropped,
            "stale_dropped": stale_dropped,
            "deduped": deduped,
            "duplicate_dropped": duplicate_dropped,
            "selected": selected,
            "cap_dropped": cap_dropped,
            "editorial_shadow": editorial_shadow,
        }
        if shadow_projection and EDITORIAL_COVERAGE_MODE == "shadow":
            # Separate interpretations of the SAME acquisition, without
            # mutating module globals (news collection can run concurrently).
            # Merely deleting editorial tickers after scoring is insufficient:
            # it can erase legacy discovery stories or change novelty/ranking.
            legacy = _LegacyHeadlineProjection(now=self.now_utc, providers=[])
            result = legacy._process_pool(raw, shadow_projection=False)
            shadow_rejections = {
                row.get("source_record_id"): row for row in relevance_dropped
                if row.get("drop_reason") == "editorial_shadow_suppressed"
            }
            shadow_rejections.update({
                row.get("source_record_id"): row for row in stale_dropped
                if self._editorial_tickers(row)
            })
            relevance_rows = []
            for row in result["relevance_dropped"]:
                if row.get("drop_reason") == "no_approved_lane":
                    row = shadow_rejections.get(row.get("source_record_id"), row)
                if row.get("drop_reason") in ("stale", "undated", "future"):
                    result["stale_dropped"].append(row)
                else:
                    relevance_rows.append(row)
            result["relevance_dropped"] = relevance_rows
            terminals = {}
            for key in ("selected", "relevance_dropped", "stale_dropped",
                        "duplicate_dropped", "cap_dropped"):
                for row in result[key]:
                    terminals.setdefault(row.get("source_record_id"), []).append(row)
            examples = []
            for row in editorial_shadow["records"]:
                matches = terminals.get(row.get("source_record_id"), [])
                # A bounded example must resolve unambiguously. Counts still
                # include all candidate occurrences; raw terminal rows remain.
                if len(matches) != 1:
                    continue
                terminal = matches[0]
                if any(row.get(k) != terminal.get(k) for k in ("title", "link")):
                    continue
                if terminal.get("drop_reason") == "editorial_shadow_suppressed":
                    row.update(disposition="suppressed", reason="editorial_coverage_shadow_mode")
                elif terminal.get("universe_tickers"):
                    row.update(disposition="core_projection", reason="editorial_mapping_shadowed")
                else:
                    row.update(disposition="legacy_projection", reason="editorial_mapping_shadowed")
                examples.append(row)
            editorial_shadow["records"] = examples
            result["editorial_shadow"] = editorial_shadow
        return result


class _LegacyHeadlineProjection(NewsAgent):
    """Pure off-mode mapping for shadow replay; never fetches another source."""

    @classmethod
    def _mapping_universe(cls):
        return SIGNAL_ELIGIBLE_TICKER_SET
