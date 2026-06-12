"""
Research Agent - Deep source scraper for specialized targets
- CEO.ca (via Google News proxy - direct scraping prohibited by TOS)
- ClinicalTrials.gov (public API v2)
- FDA PDUFA catalyst scanner (ClinicalTrials.gov Phase 3 + Google News RSS)
- Cash runway / bankruptcy risk checker (yfinance financials)
"""
import requests
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
import logging

logger = logging.getLogger("ResearchAgent")

# Try importing yfinance for cash runway checks
try:
    import yfinance as yf
    HAS_YFINANCE = True
except ImportError:
    HAS_YFINANCE = False
    logger.warning("yfinance not installed — cash runway checks disabled")


class ResearchAgent:
    def __init__(self):
        self.headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        
        # Uranium tickers to monitor on CEO.ca
        self.uranium_tickers = ["UUUU", "CCJ", "NXE", "DNN"]
        
        # CRISPR/Biotech sponsors to track
        self.biotech_sponsors = ["Vertex", "CRISPR Therapeutics", "Editas", "Intellia"]

        # Biotech tickers from watchlist (PRIORITY for PDUFA scanning)
        self.priority_biotech = ["CRSP", "NTLA", "RGNX"]
    
    # ═══════════════════════════════════════════════════════════════════
    # CEO.CA PROXY (via Google News RSS - respects TOS)
    # Screening filter: regex extraction for geological context
    # ═══════════════════════════════════════════════════════════════════

    # Regex patterns for actual geological data (not promotional fluff)
    GRADE_PATTERN = re.compile(
        r'(\d+\.?\d*)\s*%\s*U3O8'             # Uranium grade: "1.5% U3O8"
        r'|(\d+\.?\d*)\s*g/t\s*(?:Au|Ag)'     # Gold/Silver grade: "10.2 g/t Au"
        r'|(\d+\.?\d*)\s*m\s*@'               # Intercept length: "5.4 m @"
        r'|(\d+\.?\d*)\s*(?:metres?|meters?)\s*(?:of|@|grading)'  # "12 metres of"
    , re.IGNORECASE)

    # Minimum thresholds for structural alpha (not promotional mud)
    URANIUM_GRADE_MIN = 1.0     # ≥ 1.0% U3O8
    INTERCEPT_LENGTH_MIN = 5.0  # > 5m intercept

    def _extract_grades(self, text):
        """
        Extract geological grade values from text using regex.
        Returns dict with extracted values or None if no match.
        """
        if not text:
            return None

        matches = self.GRADE_PATTERN.findall(text)
        if not matches:
            return None

        grades = {"uranium_pct": [], "gold_gpt": [], "intercept_m": []}
        for m in matches:
            if m[0]:  # U3O8 %
                grades["uranium_pct"].append(float(m[0]))
            if m[1]:  # g/t Au/Ag
                grades["gold_gpt"].append(float(m[1]))
            if m[2]:  # m @ intercept
                grades["intercept_m"].append(float(m[2]))
            if m[3]:  # metres of/grading
                grades["intercept_m"].append(float(m[3]))

        return grades if any(grades.values()) else None

    def _passes_drill_filter(self, grades):
        """
        Check if extracted grades meet minimum thresholds.
        Returns True if the item passes the geological relevance filter.
        """
        if not grades:
            return False

        # Any uranium grade ≥ 1.0% U3O8 passes
        if any(g >= self.URANIUM_GRADE_MIN for g in grades.get("uranium_pct", [])):
            return True

        # Any intercept > 5m passes
        if any(g >= self.INTERCEPT_LENGTH_MIN for g in grades.get("intercept_m", [])):
            return True

        # Any gold grade > 5 g/t passes (significant)
        if any(g >= 5.0 for g in grades.get("gold_gpt", [])):
            return True

        return False

    def get_ceo_ca_signals(self):
        """
        Fetches CEO.ca related discussions via Google News RSS proxy.
        Direct scraping of CEO.ca is prohibited by their TOS.

        Screening notes:
        - Query operators include actual geological terms (assay, intercept, % U3O8)
        - Regex extraction for real decimal grade values
        - Threshold filter: ≥1% U3O8, >5m intercept
        - Unverified results tagged with ⚠️
        """
        signals = []

        for ticker in self.uranium_tickers:
            # Upgraded query with geological precision terms
            query = f'site:ceo.ca {ticker} ("% U3O8" OR "g/t Au" OR "intercept" OR "assay" OR "drill" OR "core sample")'
            url = f"https://news.google.com/rss/search?q={requests.utils.quote(query)}&hl=en-US&gl=US&ceid=US:en"

            try:
                response = requests.get(url, headers=self.headers, timeout=10)
                if response.status_code == 200:
                    root = ET.fromstring(response.content)
                    for item in root.findall('.//item')[:5]:  # Top 5 per ticker
                        title = item.find('title').text or ""
                        link = item.find('link').text or ""
                        pub_date = item.find('pubDate')
                        date_str = pub_date.text[:16] if pub_date is not None else ""
                        desc_el = item.find('description')
                        desc = desc_el.text if desc_el is not None else ""

                        # Regex extraction on title + description
                        combined_text = f"{title} {desc}"
                        grades = self._extract_grades(combined_text)
                        passes_filter = self._passes_drill_filter(grades)

                        signal = {
                            'source': f'CEO.ca/{ticker}',
                            'title': title,
                            'link': link,
                            'date': date_str,
                            'ticker': ticker,
                            'grades': grades,
                            'verified': passes_filter,
                        }

                        if passes_filter:
                            # Format the grade tag
                            grade_tags = []
                            if grades:
                                for g in grades.get("uranium_pct", []):
                                    grade_tags.append(f"{g}% U3O8")
                                for g in grades.get("intercept_m", []):
                                    grade_tags.append(f"{g}m intercept")
                                for g in grades.get("gold_gpt", []):
                                    grade_tags.append(f"{g} g/t Au")
                            signal['grade_tag'] = " | ".join(grade_tags) if grade_tags else ""
                            signal['title'] = f"✅ {title}"
                        else:
                            signal['grade_tag'] = ""
                            signal['title'] = f"⚠️ UNVERIFIED: {title}"

                        signals.append(signal)
            except Exception as e:
                logger.error(f"Error fetching CEO.ca proxy for {ticker}: {e}")

        # General uranium geology/permit news (with grade filter)
        geology_query = 'uranium mine ("% U3O8" OR "intercept" OR "assay results" OR "drill results" OR "core sample" OR "permit delay")'
        url = f"https://news.google.com/rss/search?q={requests.utils.quote(geology_query)}&hl=en-US&gl=US&ceid=US:en"

        try:
            response = requests.get(url, headers=self.headers, timeout=10)
            if response.status_code == 200:
                root = ET.fromstring(response.content)
                for item in root.findall('.//item')[:5]:
                    title = item.find('title').text or ""
                    link = item.find('link').text or ""
                    desc_el = item.find('description')
                    desc = desc_el.text if desc_el is not None else ""

                    grades = self._extract_grades(f"{title} {desc}")
                    passes_filter = self._passes_drill_filter(grades)

                    signal = {
                        'source': 'Uranium/Mining',
                        'title': f"{'✅' if passes_filter else '⚠️ UNVERIFIED:'} {title}",
                        'link': link,
                        'date': '',
                        'ticker': 'SECTOR',
                        'grades': grades,
                        'verified': passes_filter,
                        'grade_tag': '',
                    }

                    if grades and passes_filter:
                        tags = []
                        for g in grades.get("uranium_pct", []):
                            tags.append(f"{g}% U3O8")
                        for g in grades.get("intercept_m", []):
                            tags.append(f"{g}m intercept")
                        signal['grade_tag'] = " | ".join(tags)

                    signals.append(signal)
        except Exception as e:
            logger.error(f"Error fetching uranium geology news: {e}")

        # Sort: verified items first
        signals.sort(key=lambda s: (0 if s.get('verified') else 1))

        print(f"[ResearchAgent] Gathered {len(signals)} CEO.ca/Uranium indicators ({sum(1 for s in signals if s.get('verified'))} verified)")
        return signals
    
    # ═══════════════════════════════════════════════════════════════════
    # CLINICALTRIALS.GOV (Public API v2)
    # ═══════════════════════════════════════════════════════════════════
    def get_clinical_trials(self):
        """
        Fetches CRISPR and gene therapy clinical trials from ClinicalTrials.gov API.
        Focus: Vertex/CRISPR Therapeutics joint trials, gene editing therapies.
        """
        trials = []
        
        # Search queries for biotech trials
        queries = [
            ("CRISPR", "Vertex"),           # Vertex + CRISPR joint
            ("CRISPR", ""),                  # All CRISPR trials
            ("gene editing", ""),           # Gene editing trials
            ("sickle cell", "CRISPR"),      # Casgevy-related
        ]
        
        for term, sponsor in queries:
            try:
                url = "https://clinicaltrials.gov/api/v2/studies"
                params = {
                    'query.term': term,
                    'pageSize': 10,
                    'sort': 'LastUpdatePostDate:desc'  # Most recently updated
                }
                if sponsor:
                    params['query.spons'] = sponsor
                
                response = requests.get(url, params=params, headers=self.headers, timeout=15)
                
                if response.status_code == 200:
                    data = response.json()
                    studies = data.get('studies', [])
                    
                    for study in studies[:5]:  # Top 5 per query
                        protocol = study.get('protocolSection', {})
                        id_module = protocol.get('identificationModule', {})
                        status_module = protocol.get('statusModule', {})
                        sponsor_module = protocol.get('sponsorCollaboratorsModule', {})
                        
                        nct_id = id_module.get('nctId', 'N/A')
                        title = id_module.get('briefTitle', 'No title')
                        status = status_module.get('overallStatus', 'Unknown')
                        
                        # Get lead sponsor
                        lead_sponsor = sponsor_module.get('leadSponsor', {}).get('name', 'Unknown')
                        
                        # Get phase
                        phases = protocol.get('designModule', {}).get('phases', [])
                        phase = phases[0] if phases else 'N/A'
                        
                        trials.append({
                            'nct_id': nct_id,
                            'title': title[:150],
                            'status': status,
                            'sponsor': lead_sponsor,
                            'phase': phase,
                            'search_term': term,
                            'link': f"https://clinicaltrials.gov/study/{nct_id}"
                        })
                        
            except Exception as e:
                logger.error(f"Error fetching trials for '{term}': {e}")
        
        # Deduplicate by NCT ID
        seen = set()
        unique_trials = []
        for t in trials:
            if t['nct_id'] not in seen:
                seen.add(t['nct_id'])
                unique_trials.append(t)
        
        print(f"[ResearchAgent] Gathered {len(unique_trials)} clinical trials")
        return unique_trials

    # ═══════════════════════════════════════════════════════════════════
    # FDA PDUFA CATALYST SCANNER
    # ═══════════════════════════════════════════════════════════════════
    def get_pdufa_catalysts(self, days_ahead=60):
        """
        Scans for upcoming PDUFA dates / FDA catalysts via two sources:
        1. ClinicalTrials.gov — Phase 3 trials completing in the next N days
        2. Google News RSS — "PDUFA" / "FDA approval date" announcements
        
        Priority tickers (CRSP, NTLA, RGNX) get extra search queries.
        Returns list of catalysts sorted by: watchlist first, then date.
        """
        catalysts = []
        today = datetime.now()
        cutoff = today + timedelta(days=days_ahead)
        
        # ── Source 1: ClinicalTrials.gov Phase 3 nearing completion ──
        phase3_queries = [
            "PDUFA",
            "FDA approval",
            "new drug application",
            "biologics license application",
            "gene therapy",
            "CRISPR",
            "gene editing",
        ]
        
        seen_ncts = set()
        
        for query in phase3_queries:
            try:
                url = "https://clinicaltrials.gov/api/v2/studies"
                params = {
                    'query.term': query,
                    'filter.advanced': 'AREA[Phase](PHASE3)',
                    'pageSize': 20,
                    'sort': 'LastUpdatePostDate:desc',
                }
                response = requests.get(url, params=params, headers=self.headers, timeout=15)
                
                if response.status_code == 200:
                    data = response.json()
                    for study in data.get('studies', []):
                        protocol = study.get('protocolSection', {})
                        id_mod = protocol.get('identificationModule', {})
                        status_mod = protocol.get('statusModule', {})
                        sponsor_mod = protocol.get('sponsorCollaboratorsModule', {})
                        design_mod = protocol.get('designModule', {})
                        
                        nct_id = id_mod.get('nctId', '')
                        if nct_id in seen_ncts:
                            continue
                        seen_ncts.add(nct_id)
                        
                        title = id_mod.get('briefTitle', 'No title')
                        status = status_mod.get('overallStatus', 'Unknown')
                        sponsor = sponsor_mod.get('leadSponsor', {}).get('name', 'Unknown')
                        phases = design_mod.get('phases', [])
                        phase = phases[0] if phases else 'N/A'
                        
                        # Get primary completion date
                        completion_info = status_mod.get('completionDateStruct', {})
                        completion_date_str = completion_info.get('date', '')
                        
                        # Also check primary completion date
                        primary_info = status_mod.get('primaryCompletionDateStruct', {})
                        primary_date_str = primary_info.get('date', '')
                        
                        # Use whichever is sooner
                        target_date = primary_date_str or completion_date_str
                        
                        # Parse the date (format: "YYYY-MM-DD" or "YYYY-MM" or "YYYY")
                        parsed_date = None
                        if target_date:
                            for fmt in ('%Y-%m-%d', '%Y-%m', '%Y'):
                                try:
                                    parsed_date = datetime.strptime(target_date, fmt)
                                    break
                                except ValueError:
                                    continue
                        
                        # Check if within our window
                        days_until = None
                        in_window = False
                        if parsed_date:
                            delta = (parsed_date - today).days
                            days_until = delta
                            if -30 <= delta <= days_ahead:  # Include recently passed (last 30 days)
                                in_window = True
                        
                        # Check if this is a priority ticker
                        is_priority = any(t.lower() in sponsor.lower() or t.lower() in title.lower() 
                                        for t in self.priority_biotech + self.biotech_sponsors)
                        
                        # Include if: in window OR priority ticker OR actively completing
                        if in_window or is_priority or status in ('COMPLETED', 'ACTIVE_NOT_RECRUITING'):
                            catalysts.append({
                                'nct_id': nct_id,
                                'title': title[:150],
                                'sponsor': sponsor,
                                'status': status,
                                'phase': phase,
                                'target_date': target_date or 'TBD',
                                'days_until': days_until,
                                'is_priority': is_priority,
                                'source': 'ClinicalTrials.gov',
                                'link': f"https://clinicaltrials.gov/study/{nct_id}",
                            })
            except Exception as e:
                logger.error(f"Error in PDUFA trial scan for '{query}': {e}")

        # ── Source 2: Google News RSS for PDUFA announcements ──
        pdufa_queries = [
            '"PDUFA" FDA approval date 2026',
            '"FDA decision" biotech drug approval',
            '"NDA" OR "BLA" FDA 2026 approval',
        ]
        
        # Priority tickers get dedicated queries
        for ticker in self.priority_biotech:
            pdufa_queries.append(f'{ticker} FDA approval OR PDUFA OR "NDA" OR "BLA"')
        
        for query in pdufa_queries:
            try:
                url = f"https://news.google.com/rss/search?q={requests.utils.quote(query)}&hl=en-US&gl=US&ceid=US:en"
                response = requests.get(url, headers=self.headers, timeout=10)
                if response.status_code == 200:
                    root = ET.fromstring(response.content)
                    for item in root.findall('.//item')[:5]:
                        title = item.find('title').text or ''
                        link = item.find('link').text or ''
                        pub_date = item.find('pubDate')
                        date_str = pub_date.text[:16] if pub_date is not None else ''
                        
                        # Check if this mentions a priority ticker
                        is_priority = any(t.lower() in title.lower() for t in self.priority_biotech)
                        
                        catalysts.append({
                            'nct_id': '',
                            'title': title[:150],
                            'sponsor': '',
                            'status': 'NEWS',
                            'phase': '',
                            'target_date': date_str,
                            'days_until': None,
                            'is_priority': is_priority,
                            'source': 'Google News/FDA',
                            'link': link,
                        })
            except Exception as e:
                logger.error(f"Error fetching PDUFA news for '{query}': {e}")
        
        # Sort: priority first, then by days_until (soonest first)
        catalysts.sort(key=lambda c: (
            0 if c['is_priority'] else 1,
            c['days_until'] if c['days_until'] is not None else 9999,
        ))
        
        # Deduplicate by title similarity (exact match)
        seen_titles = set()
        unique = []
        for c in catalysts:
            key = c['title'][:80].lower()
            if key not in seen_titles:
                seen_titles.add(key)
                unique.append(c)
        
        print(f"[ResearchAgent] Gathered {len(unique)} PDUFA catalysts")
        return unique

    # ═══════════════════════════════════════════════════════════════════
    # CASH RUNWAY / BANKRUPTCY RISK CHECKER
    # ═══════════════════════════════════════════════════════════════════
    def check_cash_runway(self, ticker):
        """
        Checks a company's financial health using yfinance quarterly data.
        
        Returns dict with:
          - cash_and_equivalents: Total cash + short-term investments
          - total_debt: Total debt obligations
          - quarterly_burn: Avg quarterly cash burn (operating cash flow)
          - runway_quarters: Estimated quarters of cash remaining
          - risk_level: GREEN (>6Q), YELLOW (4-6Q), RED (<4Q)
          - market_cap: Current market cap
        """
        if not HAS_YFINANCE:
            return {'error': 'yfinance not installed', 'ticker': ticker}
        
        try:
            stock = yf.Ticker(ticker)
            
            # Get quarterly balance sheet
            bs = stock.quarterly_balance_sheet
            if bs is None or bs.empty:
                return {'error': 'No balance sheet data', 'ticker': ticker}
            
            # Get quarterly cash flow
            cf = stock.quarterly_cashflow
            
            # Get the most recent quarter
            latest = bs.iloc[:, 0]  # Most recent column
            
            # Extract cash position (try multiple field names)
            cash = 0
            cash_fields = [
                'Cash And Cash Equivalents',
                'Cash Cash Equivalents And Short Term Investments',
                'Cash Equivalents',
                'Cash And Short Term Investments',
                'Cash Financial',
            ]
            for field in cash_fields:
                if field in latest.index and latest[field] is not None:
                    try:
                        val = float(latest[field])
                        if val > cash:
                            cash = val
                    except (ValueError, TypeError):
                        continue
            
            # Extract total debt
            total_debt = 0
            debt_fields = ['Total Debt', 'Long Term Debt', 'Total Non Current Liabilities Net Minority Interest']
            for field in debt_fields:
                if field in latest.index and latest[field] is not None:
                    try:
                        total_debt = float(latest[field])
                        break
                    except (ValueError, TypeError):
                        continue
            
            # Calculate quarterly burn rate from operating cash flow
            quarterly_burn = 0
            if cf is not None and not cf.empty:
                ocf_row = None
                for field in ['Operating Cash Flow', 'Free Cash Flow', 'Cash Flow From Continuing Operating Activities']:
                    if field in cf.index:
                        ocf_row = cf.loc[field]
                        break
                
                if ocf_row is not None:
                    # Average of last 4 quarters (or however many we have)
                    valid_vals = [float(v) for v in ocf_row.values[:4] if v is not None]
                    if valid_vals:
                        quarterly_burn = sum(valid_vals) / len(valid_vals)
            
            # Calculate runway
            runway_quarters = None
            if quarterly_burn < 0 and cash > 0:
                # Company is burning cash — calculate quarters until empty
                runway_quarters = round(cash / abs(quarterly_burn), 1)
            elif quarterly_burn >= 0:
                # Company is cash-flow positive — not at risk
                runway_quarters = float('inf')
            
            # Risk level
            if runway_quarters is None:
                risk_level = "UNKNOWN"
            elif runway_quarters == float('inf'):
                risk_level = "GREEN"  # Cash flow positive
            elif runway_quarters > 6:
                risk_level = "GREEN"
            elif runway_quarters >= 4:
                risk_level = "YELLOW"  # Caution zone
            else:
                risk_level = "RED"  # High bankruptcy risk
            
            # Market cap
            info = stock.info or {}
            market_cap = info.get('marketCap', 0)
            
            # Format large numbers
            def fmt_money(val):
                if val is None:
                    return "N/A"
                if abs(val) >= 1e9:
                    return f"${val/1e9:.1f}B"
                elif abs(val) >= 1e6:
                    return f"${val/1e6:.1f}M"
                else:
                    return f"${val:,.0f}"
            
            result = {
                'ticker': ticker,
                'cash_and_equivalents': cash,
                'cash_formatted': fmt_money(cash),
                'total_debt': total_debt,
                'debt_formatted': fmt_money(total_debt),
                'quarterly_burn': quarterly_burn,
                'burn_formatted': fmt_money(quarterly_burn),
                'runway_quarters': runway_quarters if runway_quarters != float('inf') else 999,
                'risk_level': risk_level,
                'market_cap': market_cap,
                'mcap_formatted': fmt_money(market_cap),
            }
            
            return result
            
        except Exception as e:
            logger.error(f"Error checking cash runway for {ticker}: {e}")
            return {'error': str(e), 'ticker': ticker}

    # ═══════════════════════════════════════════════════════════════════
    # PDUFA + CASH RUNWAY CROSS-REFERENCE
    # ═══════════════════════════════════════════════════════════════════
    def get_pdufa_with_financials(self, days_ahead=60):
        """
        Master method: finds PDUFA catalysts, then checks each company's
        cash runway. Flags companies with upcoming catalysts but low cash.

        Returns:
          - catalysts: list of PDUFA catalysts
          - financials: dict of {ticker: cash_runway_data} for checked companies
          - alerts: list of RED/YELLOW cash-runway notes
        """
        catalysts = self.get_pdufa_catalysts(days_ahead)
        
        # Build a unique list of tickers to check financials for
        # Priority tickers always get checked
        tickers_to_check = set(self.priority_biotech)
        
        # Also extract tickers mentioned in catalyst sponsor names
        # Map common sponsor names to tickers
        sponsor_ticker_map = {
            'vertex': 'VRTX',
            'crispr therapeutics': 'CRSP',
            'intellia': 'NTLA',
            'editas': 'EDIT',
            'regenxbio': 'RGNX',
            'bluebird': 'BLUE',
            'sarepta': 'SRPT',
            'biomarin': 'BMRN',
            'ultragenyx': 'RARE',
            'beam': 'BEAM',
            'verve': 'VERV',
            'prime medicine': 'PRME',
            'caribou': 'CRBU',
        }
        
        for catalyst in catalysts:
            sponsor_lower = catalyst.get('sponsor', '').lower()
            title_lower = catalyst.get('title', '').lower()
            for name, tick in sponsor_ticker_map.items():
                if name in sponsor_lower or name in title_lower:
                    tickers_to_check.add(tick)
                    catalyst['mapped_ticker'] = tick
        
        # Check cash runway for each unique ticker
        financials = {}
        for ticker in tickers_to_check:
            print(f"[ResearchAgent] Checking cash runway for {ticker}...")
            result = self.check_cash_runway(ticker)
            if 'error' not in result:
                financials[ticker] = result
        
        # Build cash-runway notes list
        alerts = []
        for ticker, data in financials.items():
            if data['risk_level'] in ('RED', 'YELLOW'):
                risk_emoji = "🔴" if data['risk_level'] == 'RED' else "🟡"
                runway_str = f"{data['runway_quarters']}Q" if data['runway_quarters'] < 999 else "N/A"
                alerts.append({
                    'ticker': ticker,
                    'risk_level': data['risk_level'],
                    'risk_emoji': risk_emoji,
                    'cash': data['cash_formatted'],
                    'burn': data['burn_formatted'],
                    'runway': runway_str,
                    'debt': data['debt_formatted'],
                    'message': f"{risk_emoji} {ticker}: {data['risk_level']} RISK — Cash: {data['cash_formatted']}, "
                              f"Burn: {data['burn_formatted']}/Q, Runway: {runway_str}, Debt: {data['debt_formatted']}"
                })
        
        print(f"[ResearchAgent] Cash runway checked for {len(financials)} tickers, {len(alerts)} risk notes")
        return {
            'catalysts': catalysts,
            'financials': financials,
            'alerts': alerts,
        }

    # ═══════════════════════════════════════════════════════════════════
    # COMBINED DUMP
    # ═══════════════════════════════════════════════════════════════════
    def get_full_research_dump(self):
        """Returns all research data as formatted strings for the daily dump."""
        output = []
        
        # CEO.ca / Uranium
        output.append("=" * 50)
        output.append("CEO.CA / URANIUM INTELLIGENCE")
        output.append("Tickers: UUUU, CCJ, NXE, DNN")
        output.append("=" * 50)
        
        ceo_signals = self.get_ceo_ca_signals()
        for s in ceo_signals:
            output.append(f"[{s['source']}] {s['title']}")
            output.append(f"   Link: {s['link']}")
        
        # Clinical Trials
        output.append("")
        output.append("=" * 50)
        output.append("CLINICALTRIALS.GOV / CRISPR & GENE THERAPY")
        output.append("Focus: Vertex, CRISPR Therapeutics, Gene Editing")
        output.append("=" * 50)
        
        trials = self.get_clinical_trials()
        for t in trials:
            status_emoji = "🟢" if t['status'] == "RECRUITING" else "🟡" if "ACTIVE" in t['status'] else "⚪"
            output.append(f"{status_emoji} [{t['nct_id']}] {t['title']}")
            output.append(f"   Status: {t['status']} | Phase: {t['phase']} | Sponsor: {t['sponsor']}")
            output.append(f"   Link: {t['link']}")
        
        # PDUFA Catalysts + Cash Runway
        output.append("")
        output.append("=" * 50)
        output.append("FDA PDUFA CATALYSTS + CASH RUNWAY")
        output.append("Priority: CRSP, NTLA, RGNX | Window: 60 days")
        output.append("Risk: GREEN (>6Q) | YELLOW (4-6Q) | RED (<4Q)")
        output.append("=" * 50)
        
        pdufa_data = self.get_pdufa_with_financials()
        
        # Print risk notes first
        if pdufa_data['alerts']:
            output.append("")
            output.append("--- CASH RUNWAY RISK NOTES ---")
            for alert in pdufa_data['alerts']:
                output.append(alert['message'])
        
        # Print financial summaries
        if pdufa_data['financials']:
            output.append("")
            output.append("--- CASH RUNWAY SUMMARY ---")
            for ticker, data in sorted(pdufa_data['financials'].items()):
                risk_emoji = {"GREEN": "🟢", "YELLOW": "🟡", "RED": "🔴"}.get(data['risk_level'], "⚪")
                runway = f"{data['runway_quarters']}Q" if data['runway_quarters'] < 999 else "CF+"
                output.append(
                    f"{risk_emoji} {ticker}: Cash {data['cash_formatted']} | "
                    f"Burn {data['burn_formatted']}/Q | Runway {runway} | "
                    f"Debt {data['debt_formatted']} | MCap {data['mcap_formatted']}"
                )
        
        # Print catalysts
        if pdufa_data['catalysts']:
            output.append("")
            output.append("--- UPCOMING CATALYSTS ---")
            for c in pdufa_data['catalysts'][:30]:  # Cap at 30
                priority_tag = " [WATCHLIST]" if c['is_priority'] else ""
                days_str = f"({c['days_until']}d)" if c['days_until'] is not None else ""
                output.append(
                    f"[{c['source']}] {c['title']}{priority_tag} {days_str}"
                )
                if c.get('sponsor'):
                    output.append(f"   Sponsor: {c['sponsor']} | Status: {c['status']} | Phase: {c['phase']}")
                if c.get('link'):
                    output.append(f"   Link: {c['link']}")
        
        return output


# Quick test
if __name__ == "__main__":
    import sys
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    
    agent = ResearchAgent()
    
    print("\n" + "=" * 60)
    print("RESEARCH AGENT - LIVE DUMP")
    print("=" * 60)
    
    dump = agent.get_full_research_dump()
    for line in dump:
        print(line)
