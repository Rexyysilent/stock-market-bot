import yfinance as yf
import numpy as np
import pandas as pd
import logging
from datetime import date, datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from timeutil import build_run_context, newest, oldest, to_utc_z, utc_now_z
from yfinance_util import configure_yfinance_cache
from config import (
    WATCHLIST_STOCKS, WATCHLIST_COMMODITIES, WATCHLIST_URANIUM,
    WATCHLIST_GAUGES, WATCHLIST_VULTURE, WATCHLIST_DEFENSE,
    RSI_OVERBOUGHT, RSI_OVERSOLD,
    PRICE_ALERT_PCT, VULTURE_DROP_PCT,
    GROWTH_BASKET, DEFENSIVE_BASKET,
    OPTION_CONTRACT_VOL_OI_THRESHOLD, OPTION_CONTRACT_MAX_DTE,
    OPTION_CONTRACT_MIN_NOTIONAL, INSTRUMENT_RELATIVE_RETURN_PAIRS,
    INSTRUMENT_RELATIVE_RETURN_THRESHOLD_PCT,
)

logger = logging.getLogger("WatcherAgent")
configure_yfinance_cache(yf)


class WatcherAgent:
    def __init__(self, run_context=None):
        self.watchlist = WATCHLIST_STOCKS + WATCHLIST_COMMODITIES + WATCHLIST_URANIUM + WATCHLIST_DEFENSE + WATCHLIST_VULTURE
        self.vulture_list = WATCHLIST_VULTURE
        self.gauges = WATCHLIST_GAUGES
        self.run_context = run_context or build_run_context()
        self.reference_date = date.fromisoformat(self.run_context.utc_date)

    def _completed_daily_history(self, history):
        """Return only bars from settled NYSE sessions.

        yfinance may include a partial current daily bar. Technical and regime
        signals must never consume it, even during a manual intraday export.
        """
        session = self.run_context.latest_completed_session
        if history is None or history.empty or session is None:
            return history.iloc[0:0] if history is not None else history
        cutoff = date.fromisoformat(session)
        mask = [pd.Timestamp(value).date() <= cutoff for value in history.index]
        return history.loc[mask]

    @staticmethod
    def _exchange_session(value):
        """Return the U.S. Eastern session date represented by a timestamp."""
        if value is None:
            return None
        try:
            timestamp = pd.Timestamp(value)
            if pd.isna(timestamp):
                return None
            if timestamp.tzinfo is not None:
                timestamp = timestamp.tz_convert("America/New_York")
            return timestamp.date().isoformat()
        except (TypeError, ValueError):
            return None

    def get_price_data(self, ticker):
        """Pure data layer: returns {"price": float, "change_pct": float} or None."""
        try:
            stock = yf.Ticker(ticker)
            data = stock.history(period="1d")
            if not data.empty:
                current_price = float(data['Close'].iloc[-1])
                open_price = float(data['Open'].iloc[-1])

                change = current_price - open_price
                pct_change = (change / open_price) * 100

                return {
                    "price": round(current_price, 2),
                    "change_pct": round(pct_change, 2),
                    # the bar's own exchange time, not fetch time
                    "as_of": to_utc_z(data.index[-1]),
                    "observed_at": utc_now_z(),
                    "source_session": pd.Timestamp(data.index[-1]).date().isoformat(),
                    "session_complete": (
                        self.run_context.latest_completed_session is not None
                        and pd.Timestamp(data.index[-1]).date().isoformat()
                        <= self.run_context.latest_completed_session
                    ),
                }
            return None
        except Exception as e:
            logger.error(f"Error fetching {ticker}: {e}")
            return None

    @staticmethod
    def render_price(price_data):
        """Render layer: format price data for human display."""
        if not price_data or price_data.get("price") is None:
            return None
        pct_change = price_data.get("change_pct")
        if pct_change is None:
            return f"${price_data['price']:.2f}"

        emoji = "⚪"
        if pct_change > 0:
            emoji = "🟢"
        elif pct_change < 0:
            emoji = "🔴"

        return f"${price_data['price']:.2f} ({emoji} {pct_change:+.2f}%)"

    def get_stock_price(self, ticker):
        return self.render_price(self.get_price_data(ticker))

    def get_full_report_data(self):
        """Pure data layer: {ticker: {"price", "change_pct"} | None} for the full watchlist."""
        report = {}
        all_tickers = self.watchlist + self.gauges
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(self.get_price_data, t): t for t in all_tickers}
            for future in as_completed(futures):
                ticker = futures[future]
                try:
                    report[ticker] = future.result()
                except Exception as e:
                    logger.error(f"Price fetch failed for {ticker}: {e}")
                    report[ticker] = None
        return report

    def get_full_report(self):
        return {
            ticker: self.render_price(data) or "N/A"
            for ticker, data in self.get_full_report_data().items()
        }

    # ═══════════════════════════════════════════════════════════════════
    # TECHNICAL INDICATORS
    # ═══════════════════════════════════════════════════════════════════

    def _compute_rsi(self, closes, period=14):
        """Compute RSI from a pandas Series of closing prices."""
        delta = closes.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)
        avg_gain = gain.rolling(window=period, min_periods=period).mean()
        avg_loss = loss.rolling(window=period, min_periods=period).mean()
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return rsi

    def check_technical_indicators(self, ticker):
        """
        Returns a dict of technical signals for a given ticker:
        - RSI (14-period)
        - Volume spike (current vs 20-day avg)
        - 52-week high/low proximity
        - 50-day & 200-day SMA status
        """
        signals = {"ticker": ticker, "alerts": []}
        try:
            stock = yf.Ticker(ticker)
            hist = self._completed_daily_history(stock.history(period="1y"))

            if hist.empty or len(hist) < 50:
                signals["error"] = "Insufficient data"
                return signals

            # derived record: inherits the last bar actually used
            signals["as_of"] = to_utc_z(hist.index[-1])
            signals["observed_at"] = utc_now_z()
            signals["source_session"] = pd.Timestamp(
                hist.index[-1]
            ).date().isoformat()
            signals["session_complete"] = True

            closes = hist['Close']
            volumes = hist['Volume']
            current_price = closes.iloc[-1]

            # --- RSI ---
            rsi_series = self._compute_rsi(closes)
            rsi = rsi_series.iloc[-1]
            signals["rsi"] = round(rsi, 1)
            if rsi >= RSI_OVERBOUGHT:
                signals["alerts"].append(f"⚠️ RSI {rsi:.0f} — OVERBOUGHT")
            elif rsi <= RSI_OVERSOLD:
                signals["alerts"].append(f"🔥 RSI {rsi:.0f} — OVERSOLD")

            # --- RSI Divergence ---
            divergence = self._detect_rsi_divergence(closes, rsi_series)
            if divergence:
                signals["rsi_divergence"] = divergence
                if divergence == "BEARISH":
                    signals["alerts"].append("⚠️ BEARISH RSI DIVERGENCE — price up but momentum fading")
                elif divergence == "BULLISH":
                    signals["alerts"].append("🟢 BULLISH RSI DIVERGENCE — price down but momentum building")

            # Volume is a measurement here, never an alert. The versioned
            # baseline layer owns HIGH_VOLUME_SURPRISE / LOW_PARTICIPATION so
            # immature or mid-session readings cannot bypass its gates.
            avg_vol_20 = volumes.iloc[:-1].tail(20).mean()
            current_vol = volumes.iloc[-1]
            if avg_vol_20 > 0:
                vol_ratio = current_vol / avg_vol_20
                signals["volume_ratio"] = round(vol_ratio, 2)

            # --- 52-week high/low ---
            high_52w = closes.max()
            low_52w = closes.min()
            pct_from_high = ((current_price - high_52w) / high_52w) * 100
            pct_from_low = ((current_price - low_52w) / low_52w) * 100
            signals["pct_from_52w_high"] = round(pct_from_high, 1)
            signals["pct_from_52w_low"] = round(pct_from_low, 1)

            if abs(pct_from_high) <= 5:
                signals["alerts"].append(
                    f"🏔️ Within {abs(pct_from_high):.1f}% of 52-week HIGH"
                )
            if abs(pct_from_low) <= 10:
                signals["alerts"].append(
                    f"🕳️ Within {pct_from_low:.1f}% of 52-week LOW"
                )

            # --- Moving Averages ---
            sma_50 = closes.tail(50).mean()
            signals["sma_50"] = round(sma_50, 2)

            if len(closes) >= 200:
                sma_200 = closes.tail(200).mean()
                signals["sma_200"] = round(sma_200, 2)

                # Golden cross / death cross detection
                prev_sma50 = closes.tail(51).head(50).mean()
                prev_sma200 = closes.tail(201).head(200).mean()

                if prev_sma50 < prev_sma200 and sma_50 > sma_200:
                    signals["alerts"].append("✨ GOLDEN CROSS — 50 SMA crossed above 200 SMA")
                elif prev_sma50 > prev_sma200 and sma_50 < sma_200:
                    signals["alerts"].append("💀 DEATH CROSS — 50 SMA crossed below 200 SMA")

                if current_price > sma_50 > sma_200:
                    signals["trend"] = "BULLISH (price > 50 SMA > 200 SMA)"
                elif current_price < sma_50 < sma_200:
                    signals["trend"] = "BEARISH (price < 50 SMA < 200 SMA)"
                else:
                    signals["trend"] = "MIXED"

            # --- Price change alert ---
            if len(hist) >= 2:
                open_price = hist['Open'].iloc[-1]
                pct_change = ((current_price - open_price) / open_price) * 100
                signals["daily_change_pct"] = round(pct_change, 2)
                if abs(pct_change) >= PRICE_ALERT_PCT:
                    direction = "📈 SURGING" if pct_change > 0 else "📉 DROPPING"
                    signals["alerts"].append(
                        f"{direction} {pct_change:+.1f}% intraday"
                    )

                # --- Legacy drop-monitor threshold ---
                if ticker in self.vulture_list and pct_change <= -VULTURE_DROP_PCT:
                    signals["alerts"].insert(0,
                        f"DROP THRESHOLD — {ticker} down {pct_change:.1f}%"
                    )
                    signals["vulture"] = True

        except Exception as e:
            logger.error(f"Error computing technicals for {ticker}: {e}")
            signals["error"] = str(e)

        return signals

    def get_all_technicals(self):
        """Returns technical indicators for every ticker in the watchlist (threaded)."""
        results = {}
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(self.check_technical_indicators, t): t for t in self.watchlist}
            for future in as_completed(futures):
                ticker = futures[future]
                try:
                    results[ticker] = future.result()
                except Exception as e:
                    logger.error(f"Technicals failed for {ticker}: {e}")
                    results[ticker] = {"ticker": ticker, "error": str(e), "alerts": []}
        return results

    def get_price_alerts(self):
        """Returns only the tickers that have triggered alerts."""
        alerts = []
        for ticker in self.watchlist:
            signals = self.check_technical_indicators(ticker)
            if signals.get("alerts"):
                alerts.append(signals)
        return alerts

    def get_vulture_alerts(self):
        """Check vulture watchlist specifically for big drops."""
        alerts = []
        for ticker in self.vulture_list:
            signals = self.check_technical_indicators(ticker)
            if signals.get("vulture"):
                alerts.append(signals)
        return alerts

    def format_technicals(self, ticker):
        """Human-readable technical summary for a single ticker."""
        s = self.check_technical_indicators(ticker)
        if s.get("error"):
            return f"**{ticker}**: {s['error']}"

        lines = [f"**{ticker}** Technical Analysis"]
        if "rsi" in s:
            lines.append(f"  RSI(14): {s['rsi']}")
        if "volume_ratio" in s:
            lines.append(f"  Volume: {s['volume_ratio']}x avg")
        if "trend" in s:
            lines.append(f"  Trend: {s['trend']}")
        if "pct_from_52w_high" in s:
            lines.append(f"  52w High: {s['pct_from_52w_high']}%  |  52w Low: +{s.get('pct_from_52w_low', '?')}%")
        if s.get("vulture"):
            lines.append("  DROP MONITOR — configured decline threshold crossed")
        if s.get("alerts"):
            lines.append("  🚨 " + " | ".join(s["alerts"]))
        return "\n".join(lines)

    # ═══════════════════════════════════════════════════════════════════
    # MARKET CONTEXT — RSI DIVERGENCE
    # ═══════════════════════════════════════════════════════════════════

    def _detect_rsi_divergence(self, closes, rsi_series, lookback=30):
        """
        Detect bearish/bullish RSI divergence by comparing local peaks/troughs.
        Bearish: price makes higher high, RSI makes lower high
        Bullish: price makes lower low, RSI makes higher low
        """
        if len(closes) < lookback + 10 or len(rsi_series) < lookback + 10:
            return None

        try:
            price_window = closes.iloc[-lookback:].values
            rsi_window = rsi_series.iloc[-lookback:].dropna().values

            if len(rsi_window) < lookback - 5:
                return None

            # Find local peaks (highs) — simple: point higher than its neighbors
            price_peaks = []
            rsi_peaks = []
            price_troughs = []
            rsi_troughs = []

            min_len = min(len(price_window), len(rsi_window))
            for i in range(2, min_len - 2):
                # Peaks
                if price_window[i] > price_window[i-1] and price_window[i] > price_window[i+1] and \
                   price_window[i] > price_window[i-2] and price_window[i] > price_window[i+2]:
                    price_peaks.append((i, price_window[i]))
                    rsi_peaks.append((i, rsi_window[i]))
                # Troughs
                if price_window[i] < price_window[i-1] and price_window[i] < price_window[i+1] and \
                   price_window[i] < price_window[i-2] and price_window[i] < price_window[i+2]:
                    price_troughs.append((i, price_window[i]))
                    rsi_troughs.append((i, rsi_window[i]))

            # Check bearish divergence: last 2 peaks — price higher, RSI lower
            if len(price_peaks) >= 2 and len(rsi_peaks) >= 2:
                p1, p2 = price_peaks[-2], price_peaks[-1]
                r1, r2 = rsi_peaks[-2], rsi_peaks[-1]
                if p2[1] > p1[1] and r2[1] < r1[1]:
                    return "BEARISH"

            # Check bullish divergence: last 2 troughs — price lower, RSI higher
            if len(price_troughs) >= 2 and len(rsi_troughs) >= 2:
                p1, p2 = price_troughs[-2], price_troughs[-1]
                r1, r2 = rsi_troughs[-2], rsi_troughs[-1]
                if p2[1] < p1[1] and r2[1] > r1[1]:
                    return "BULLISH"

        except Exception as e:
            logger.error(f"RSI divergence detection error: {e}")

        return None

    # ═══════════════════════════════════════════════════════════════════
    # MARKET CONTEXT — OPTIONS FLOW (Put/Call Ratio)
    # ═══════════════════════════════════════════════════════════════════

    def get_options_flow(self, ticker):
        """
        Completed-session option volume/open-interest measurements.

        yfinance exposes a snapshot, not trade-side classification. Contract
        rows qualify only when ``lastTradeDate`` maps to the run context's
        latest settled NYSE session. ``lastPrice * session volume * 100`` is
        retained as a notional estimate for schema compatibility; it is not
        actual premium paid and CALL/PUT is not directional intent.
        """
        observed_at = utc_now_z()
        source_session = self.run_context.latest_completed_session
        result = {
            "ticker": ticker,
            "alerts": [],
            "option_contract_volume_oi_anomaly": [],
            "as_of": None,
            "observed_at": observed_at,
            "source_session": source_session,
            "signal_eligible": False,
            "eligible_volume_contracts": 0,
            "excluded_stale_volume_contracts": 0,
        }
        if source_session is None:
            result["error"] = "No settled NYSE session available"
            return result
        reference_date = date.fromisoformat(source_session)
        source_times = []
        try:
            stock = yf.Ticker(ticker)
            expirations = stock.options

            if not expirations:
                result["error"] = "No options data"
                return result

            # Aggregate totals for P/C ratio (secondary signal)
            total_call_vol = 0
            total_put_vol = 0
            total_call_oi = 0
            total_put_oi = 0

            for exp_date_str in expirations:
                # Parse expiration and compute DTE
                try:
                    exp_date = datetime.strptime(exp_date_str, "%Y-%m-%d").date()
                except ValueError:
                    continue
                dte = (exp_date - reference_date).days
                if dte < 0:
                    continue

                # Get the chain for this expiration
                try:
                    chain = stock.option_chain(exp_date_str)
                except Exception:
                    continue

                # Process both calls and puts
                for side, df in [("CALL", chain.calls), ("PUT", chain.puts)]:
                    if df.empty:
                        continue

                    for _, row in df.iterrows():
                        vol = row.get('volume', 0)
                        oi = row.get('openInterest', 0)
                        price = row.get('lastPrice', 0)
                        strike = row.get('strike', 0)
                        last_trade_raw = row.get('lastTradeDate')

                        # Handle NaN
                        vol = 0 if pd.isna(vol) else int(vol)
                        oi = 0 if pd.isna(oi) else int(oi)
                        price = 0 if pd.isna(price) else float(price)
                        last_trade_at = to_utc_z(last_trade_raw)
                        last_trade_session = self._exchange_session(last_trade_raw)

                        # OI is a chain-snapshot field (normally prior-session
                        # publication). Volume is counted only when the row's
                        # actual last trade belongs to the completed session.
                        if side == "CALL":
                            total_call_oi += oi
                        else:
                            total_put_oi += oi

                        volume_is_current = (
                            vol > 0
                            and last_trade_at is not None
                            and last_trade_session == source_session
                        )
                        if vol > 0 and not volume_is_current:
                            result["excluded_stale_volume_contracts"] += 1
                        if not volume_is_current:
                            continue
                        result["eligible_volume_contracts"] += 1
                        source_times.append(last_trade_at)
                        if side == "CALL":
                            total_call_vol += vol
                        else:
                            total_put_vol += vol

                        if dte <= OPTION_CONTRACT_MAX_DTE and oi > 0 and vol > 0:
                            vol_oi_ratio = vol / oi
                            notional_estimate = price * vol * 100

                            if (vol_oi_ratio >= OPTION_CONTRACT_VOL_OI_THRESHOLD
                                    and notional_estimate >= OPTION_CONTRACT_MIN_NOTIONAL):
                                anomaly = {
                                    "ticker": ticker,
                                    "strike": strike,
                                    "type": side,
                                    "dte": dte,
                                    "volume": vol,
                                    "open_interest": oi,
                                    "vol_oi_ratio": round(vol_oi_ratio, 1),
                                    # Backward-compatible field name. This is an
                                    # estimate, not classified transaction flow.
                                    "premium": round(notional_estimate),
                                    "notional_estimate": round(notional_estimate),
                                    "notional_estimate_method": (
                                        "last_price_x_completed_session_volume_x100"
                                    ),
                                    "premium_fmt": (
                                        f"${notional_estimate/1e6:.1f}M"
                                        if notional_estimate >= 1e6
                                        else f"${notional_estimate/1e3:.0f}K"
                                    ),
                                    "expiration": exp_date_str,
                                    "last_trade_at": last_trade_at,
                                    "as_of": last_trade_at,
                                    "observed_at": observed_at,
                                    "source_session": source_session,
                                    "signal_eligible": True,
                                }
                                result["option_contract_volume_oi_anomaly"].append(anomaly)
                                result["alerts"].append(
                                    f"🎯 OPTION CONTRACT VOLUME/OI ANOMALY — {ticker} ${strike}{side[0]} "
                                    f"DTE={dte} Vol/OI={vol_oi_ratio:.1f}x "
                                    f"Est.Notional={anomaly['premium_fmt']}"
                                )

            # Aggregate measurements. Raw absolute P/C threshold alerts are
            # intentionally absent: the versioned baseline layer owns them.
            pc_vol_ratio = (
                round(total_put_vol / total_call_vol, 2)
                if total_call_vol > 0 else None
            )
            pc_oi_ratio = (
                round(total_put_oi / total_call_oi, 2)
                if total_call_oi > 0 else None
            )

            result["put_call_vol_ratio"] = pc_vol_ratio
            result["put_call_oi_ratio"] = pc_oi_ratio
            result["total_put_volume"] = int(total_put_vol)
            result["total_call_volume"] = int(total_call_vol)
            result["total_put_oi"] = int(total_put_oi)
            result["total_call_oi"] = int(total_call_oi)
            result["as_of"] = newest(source_times)
            result["signal_eligible"] = bool(
                source_times and pc_vol_ratio is not None
            )
            result["option_contract_volume_oi_anomaly"].sort(
                key=lambda row: (
                    -float(row.get("notional_estimate") or 0),
                    str(row.get("type") or ""),
                    float(row.get("strike") or 0),
                )
            )

        except Exception as e:
            logger.error(f"Options flow error for {ticker}: {e}")
            result["error"] = str(e)

        return result

    def get_all_options_flow(self):
        """Get options flow for all stock tickers (threaded)."""
        results = {}
        options_tickers = WATCHLIST_STOCKS + WATCHLIST_VULTURE
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {pool.submit(self.get_options_flow, t): t for t in options_tickers}
            for future in as_completed(futures):
                ticker = futures[future]
                try:
                    flow = future.result()
                    if "error" not in flow:
                        results[ticker] = flow
                except Exception as e:
                    logger.error(f"Options flow thread failed for {ticker}: {e}")
        logger.info(f"Options flow checked for {len(results)} tickers")
        return results

    # ═══════════════════════════════════════════════════════════════════
    # INSTRUMENT RELATIVE-RETURN SPREAD
    # ═══════════════════════════════════════════════════════════════════

    def check_instrument_relative_return_spread(self):
        """
        Compare physical commodity proxies vs paper ETF proxies.
        Reports a threshold crossing without inferring buyer intent or cause.
        
        Pairs:
          - Uranium: SRUUF (Sprott Physical Trust) vs URA (ETF)
          - Silver:  SI=F (futures near-month) vs SLV (ETF)
        """
        results = {"pairs": [], "alerts": []}

        for pair in INSTRUMENT_RELATIVE_RETURN_PAIRS:
            phys_ticker = pair["physical"]
            paper_ticker = pair["paper"]
            commodity = pair["commodity"]

            try:
                phys = yf.Ticker(phys_ticker)
                paper = yf.Ticker(paper_ticker)

                phys_hist = self._completed_daily_history(
                    phys.history(period="10d")
                )
                paper_hist = self._completed_daily_history(
                    paper.history(period="10d")
                )

                if phys_hist.empty or paper_hist.empty:
                    continue

                phys_price = phys_hist['Close'].iloc[-1]
                paper_price = paper_hist['Close'].iloc[-1]

                # Normalize: compute 5-day % change for both
                if len(phys_hist) >= 2 and len(paper_hist) >= 2:
                    phys_5d_chg = ((phys_hist['Close'].iloc[-1] - phys_hist['Close'].iloc[0]) / phys_hist['Close'].iloc[0]) * 100
                    paper_5d_chg = ((paper_hist['Close'].iloc[-1] - paper_hist['Close'].iloc[0]) / paper_hist['Close'].iloc[0]) * 100
                else:
                    phys_5d_chg = 0
                    paper_5d_chg = 0

                # Divergence: physical outperforming paper
                spread = phys_5d_chg - paper_5d_chg

                entry = {
                    "commodity": commodity,
                    "physical_ticker": phys_ticker,
                    "paper_ticker": paper_ticker,
                    "physical_price": round(phys_price, 2),
                    "paper_price": round(paper_price, 2),
                    "physical_5d_chg": round(phys_5d_chg, 2),
                    "paper_5d_chg": round(paper_5d_chg, 2),
                    "spread": round(spread, 2),
                    "threshold_crossed": spread > INSTRUMENT_RELATIVE_RETURN_THRESHOLD_PCT,
                    # a spread is only as fresh as its stalest leg
                    "as_of": oldest(to_utc_z(phys_hist.index[-1]),
                                    to_utc_z(paper_hist.index[-1])),
                }
                results["pairs"].append(entry)

                if entry["threshold_crossed"]:
                    results["alerts"].append(
                        f"🚨 INSTRUMENT RELATIVE-RETURN SPREAD — {commodity}: "
                        f"{phys_ticker} outpacing {paper_ticker} by {spread:+.1f}% over 5d."
                    )

            except Exception as e:
                logger.error(f"Instrument relative-return spread failed for {commodity}: {e}")

        results["as_of"] = oldest(*(p["as_of"] for p in results["pairs"])) if results["pairs"] else None
        logger.info(f"Instrument spreads checked for {len(results['pairs'])} pairs, "
                    f"{len(results['alerts'])} threshold notes")
        return results

    # ═══════════════════════════════════════════════════════════════════
    # MARKET CONTEXT — EARNINGS CALENDAR
    # ═══════════════════════════════════════════════════════════════════

    def _parse_earnings_datetime(self, value):
        """Normalize yfinance's date, datetime, Timestamp, list, or string date."""
        if value is None:
            return None

        if isinstance(value, (list, tuple, pd.Series, pd.Index)):
            for item in value:
                parsed = self._parse_earnings_datetime(item)
                if parsed:
                    return parsed
            return None

        try:
            if pd.isna(value):
                return None
        except (TypeError, ValueError):
            pass

        if hasattr(value, "to_pydatetime"):
            value = value.to_pydatetime()

        if isinstance(value, datetime):
            return value.replace(tzinfo=None)

        if isinstance(value, date):
            return datetime.combine(value, datetime.min.time())

        if isinstance(value, str):
            parsed = pd.to_datetime(value, errors="coerce")
            if pd.isna(parsed):
                return None
            if hasattr(parsed, "to_pydatetime"):
                parsed = parsed.to_pydatetime()
            if isinstance(parsed, datetime):
                return parsed.replace(tzinfo=None)

        return None

    def _clean_earnings_value(self, value):
        try:
            if pd.isna(value):
                return None
        except (TypeError, ValueError):
            pass
        if isinstance(value, np.generic):
            value = value.item()
        if isinstance(value, float):
            return round(value, 4)
        return value

    def _earnings_timing_from_datetime(self, earnings_dt):
        if earnings_dt.time() == datetime.min.time():
            return None
        if earnings_dt.hour < 12:
            return "BMO"
        if earnings_dt.hour >= 16:
            return "AMC"
        return "DMT"

    def _make_earnings_entry(self, ticker, earnings_dt, source, **fields):
        if not earnings_dt:
            return None

        earnings_day = earnings_dt.date()
        days_until = (earnings_day - self.reference_date).days
        entry = {
            "ticker": ticker,
            "earnings_date": earnings_day.strftime("%Y-%m-%d"),
            "days_until": days_until,
            "is_imminent": 0 <= days_until <= 7,
            "is_past_week": -7 <= days_until < 0,
            "source": source,
        }

        if earnings_dt.time() != datetime.min.time():
            entry["earnings_datetime"] = earnings_dt.isoformat(timespec="minutes")
            entry["timing"] = self._earnings_timing_from_datetime(earnings_dt)

        for key, value in fields.items():
            clean_value = self._clean_earnings_value(value)
            if clean_value is not None:
                entry[key] = clean_value

        if entry["is_imminent"]:
            entry["alert"] = f"⚠️ EARNINGS IN {days_until} DAYS"
        elif entry["is_past_week"]:
            entry["alert"] = f"📋 Reported {abs(days_until)} days ago"

        return entry

    def _get_earnings_from_dates_api(self, ticker, stock):
        try:
            df = stock.get_earnings_dates(limit=12)
        except Exception as exc:
            logger.debug(f"get_earnings_dates failed for {ticker}: {exc}")
            return None

        if df is None or df.empty:
            return None

        candidates = []
        for idx, row in df.iterrows():
            earnings_dt = self._parse_earnings_datetime(idx)
            entry = self._make_earnings_entry(
                ticker,
                earnings_dt,
                "yfinance.get_earnings_dates",
                eps_estimate=row.get("EPS Estimate"),
                reported_eps=row.get("Reported EPS"),
                surprise_pct=row.get("Surprise(%)"),
            )
            if entry:
                candidates.append(entry)

        if not candidates:
            return None

        future = [entry for entry in candidates if entry["days_until"] >= 0]
        if future:
            return sorted(future, key=lambda x: x["days_until"])[0]

        past_week = [entry for entry in candidates if entry["days_until"] >= -7]
        if past_week:
            return sorted(past_week, key=lambda x: abs(x["days_until"]))[0]

        return None

    def _get_earnings_from_calendar(self, ticker, stock):
        try:
            cal = stock.get_calendar() if hasattr(stock, "get_calendar") else stock.calendar
        except Exception as exc:
            logger.debug(f"calendar fetch failed for {ticker}: {exc}")
            return None

        if cal is None or (isinstance(cal, pd.DataFrame) and cal.empty):
            return None

        if isinstance(cal, dict):
            earnings_dt = self._parse_earnings_datetime(cal.get("Earnings Date"))
            return self._make_earnings_entry(
                ticker,
                earnings_dt,
                "yfinance.calendar",
                eps_estimate=cal.get("Earnings Average"),
                eps_low=cal.get("Earnings Low"),
                eps_high=cal.get("Earnings High"),
                revenue_estimate=cal.get("Revenue Average"),
            )

        if isinstance(cal, pd.DataFrame) and "Earnings Date" in cal.index:
            earnings_dt = self._parse_earnings_datetime(cal.loc["Earnings Date"])
            return self._make_earnings_entry(ticker, earnings_dt, "yfinance.calendar")

        return None

    def get_earnings_calendar(self):
        """
        Check upcoming earnings dates for watchlist tickers.
        Flags tickers with earnings within 7 days.
        """
        earnings = []
        tickers = list(dict.fromkeys(WATCHLIST_STOCKS + WATCHLIST_VULTURE))

        for ticker in tickers:
            try:
                stock = yf.Ticker(ticker)
                entry = self._get_earnings_from_dates_api(ticker, stock)
                if entry is None:
                    entry = self._get_earnings_from_calendar(ticker, stock)
                if entry:
                    earnings.append(entry)
            except Exception as exc:
                logger.debug(f"No earnings data for {ticker}: {exc}")
                continue

        earnings.sort(key=lambda x: x["days_until"])
        logger.info(f"Earnings calendar: {len(earnings)} tickers with dates")
        return earnings

    # ---------------------------------------------------------------
    # MARKET CONTEXT - SECTOR ROTATION
    # ---------------------------------------------------------------

    def get_vix_term_structure(self):
        """VIX term structure regime flag: spot ^VIX vs 3-month ^VIX3M.

        ratio > 1 (spot above 3-month) = backwardation = stress/risk-off;
        ratio < 1 = contango = normal/risk-on.
        """
        result = {"vix": None, "vix3m": None, "vix_vix3m_ratio": None,
                  "structure": None, "regime": None}
        try:
            vix_history = self._completed_daily_history(
                yf.Ticker("^VIX").history(period="10d")
            )
            vix3m_history = self._completed_daily_history(
                yf.Ticker("^VIX3M").history(period="10d")
            )
            vix = vix_history['Close']
            vix3m = vix3m_history['Close']
            if vix.empty or vix3m.empty:
                result["error"] = "No VIX/VIX3M data"
                return result
            spot = float(vix.iloc[-1])
            three_month = float(vix3m.iloc[-1])
            ratio = spot / three_month
            result.update({
                "vix": round(spot, 2),
                "vix3m": round(three_month, 2),
                "vix_vix3m_ratio": round(ratio, 3),
                "structure": "BACKWARDATION" if ratio > 1.0 else "CONTANGO",
                "regime": "RISK_OFF" if ratio > 1.0 else "RISK_ON",
                "as_of": oldest(to_utc_z(vix.index[-1]), to_utc_z(vix3m.index[-1])),
            })
        except Exception as e:
            logger.error(f"VIX term structure error: {e}")
            result["error"] = str(e)
        return result

    def get_sector_rotation(self):
        """
        Compare growth vs defensive basket performance.
        Produces a descriptive risk-regime label from relative basket returns.
        """
        result = {"alerts": []}
        bar_times = []

        def _get_basket_perf(tickers, period_days):
            """Get average performance for a basket of tickers."""
            perfs = []
            for ticker in tickers:
                try:
                    stock = yf.Ticker(ticker)
                    hist = self._completed_daily_history(
                        stock.history(period=f"{period_days + 10}d")
                    )
                    if len(hist) >= period_days:
                        start_price = hist['Close'].iloc[-period_days]
                        end_price = hist['Close'].iloc[-1]
                        pct = ((end_price - start_price) / start_price) * 100
                        perfs.append(pct)
                        bar_times.append(to_utc_z(hist.index[-1]))
                except Exception:
                    continue
            return round(sum(perfs) / len(perfs), 2) if perfs else 0

        # 5-day performance
        growth_5d = _get_basket_perf(GROWTH_BASKET, 5)
        defensive_5d = _get_basket_perf(DEFENSIVE_BASKET, 5)
        spread_5d = defensive_5d - growth_5d

        # 20-day performance
        growth_20d = _get_basket_perf(GROWTH_BASKET, 20)
        defensive_20d = _get_basket_perf(DEFENSIVE_BASKET, 20)
        spread_20d = defensive_20d - growth_20d

        result["growth_5d"] = growth_5d
        result["defensive_5d"] = defensive_5d
        result["spread_5d"] = spread_5d
        result["growth_20d"] = growth_20d
        result["defensive_20d"] = defensive_20d
        result["spread_20d"] = spread_20d
        result["as_of"] = oldest(*bar_times) if bar_times else None

        # Determine rotation signal
        if spread_5d > 3.0:
            result["signal"] = "RISK_OFF"
            result["alerts"].append(
                f"🚨 RISK-OFF ROTATION — Defensives beating growth by {spread_5d:+.1f}% (5d)"
            )
        elif spread_5d > 1.5:
            result["signal"] = "CAUTIOUS"
            result["alerts"].append(
                f"⚠️ CAUTIOUS — Defensives outperforming growth by {spread_5d:+.1f}% (5d)"
            )
        elif spread_5d < -3.0:
            result["signal"] = "RISK_ON"
            result["alerts"].append(
                f"🟢 RISK-ON — Growth leading defensives by {abs(spread_5d):.1f}% (5d)"
            )
        else:
            result["signal"] = "NEUTRAL"

        # 20-day trend for context
        if spread_20d > 5.0:
            result["alerts"].append(
                f"📊 20-day trend confirms: defensives +{spread_20d:.1f}% vs growth (sustained risk-off)"
            )

        logger.info(f"Sector rotation: Growth 5d={growth_5d:+.1f}% Def 5d={defensive_5d:+.1f}% Spread={spread_5d:+.1f}%")
        return result
