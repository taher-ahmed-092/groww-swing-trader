"""
Scout agent — weekly candidate discovery.

Screens the expanded 200+ stock watchlist (src/data/watchlist.py) across all market
caps. ALL scoring is deterministic code over real data (CLAUDE.md rule 4) — the LLM
is never involved in producing numbers. Each stock passes a tier-scaled liquidity
gate, then is scored across seven dimensions (momentum, trend, volume, 1m relative
strength, 52W position, institutional FII/DII flow, 20d relative strength), adjusted
by tier (mid/small need stronger volume; small needs a breakout or deep-oversold
signal) and standing knowledge patterns, then filtered for earnings proximity.
Top 10 returned.

TODO: Add real-time news screening via SerpAPI or similar in a future iteration.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

import httpx
import pandas as pd
import yfinance as yf
from rich.console import Console

from config.risk_limits import LIMITS
from config.settings import settings
from src.agents.technical.indicators import compute_indicators
from src.data.fetcher import MarketDataFetcher
from src.data.watchlist import ALL_STOCKS, get_risk_params_for_tier, get_tier
from src.memory.journal import TradingJournal
from src.memory.lessons import LessonsRetriever
from src.utils.adaptive import get_adaptive_params

console = Console()

_CACHE_DIR = os.path.join("data", "cache")
_FII_DII_CACHE = os.path.join(_CACHE_DIR, "fii_dii_daily.json")
_NSE_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.nseindia.com"}

_NEG_CUES = ("fail", "loss", "weak", "headwind", "deteriorat", "negative", "overbought")
_POS_CUES = ("outperform", "win", "strong", "positive", "breakout", "momentum")

# Watchlist now lives in src/data/watchlist.py (200+ stocks across all market caps).
# WATCHLIST is kept as a backward-compatible {symbol: {name, sector}} view so existing
# callers (e.g. command_handler /watchlist) keep working. Tier lookups use ALL_STOCKS.
WATCHLIST: dict[str, dict[str, str]] = {
    sym: {"name": sym, "sector": d["sector"]} for sym, d in ALL_STOCKS.items()
}

# Minimum 20-day average daily volume per tier — below this we can't exit cleanly.
_MIN_VOLUME_BY_TIER = {"large": 100_000, "mid": 50_000, "small": 20_000}


class ScoutAgent:
    def __init__(self) -> None:
        self.fetcher = MarketDataFetcher()
        self.lessons = LessonsRetriever()
        self.journal = TradingJournal()
        self._unaffordable: list[str] = []
        os.makedirs(_CACHE_DIR, exist_ok=True)

    # ── helpers ──────────────────────────────────────────────────────────────
    def _nifty_returns(self) -> tuple[float | None, float | None, float | None]:
        """Return (nifty_1m_pct, nifty_20d_pct, nifty_30d_pct)."""
        try:
            df = yf.Ticker("^NSEI").history(period="3mo")
            if df is None or len(df) < 31:
                return None, None, None
            close = df["Close"]
            last = float(close.iloc[-1])
            # ~1 month ≈ 22 sessions; 20-day ≈ 21 sessions back.
            ref_1m = float(close.iloc[-22]) if len(close) >= 22 else float(close.iloc[0])
            r1m = (last / ref_1m - 1) * 100
            r20 = (last / float(close.iloc[-21]) - 1) * 100
            r30 = (last / float(close.iloc[-31]) - 1) * 100
            return r1m, r20, r30
        except Exception:
            return None, None, None

    def _get_fii_dii_data(self) -> dict:
        neutral = {
            "fii_net_buy_cr": 0.0, "dii_net_buy_cr": 0.0,
            "fii_trend": "NEUTRAL", "dii_trend": "NEUTRAL",
        }
        # 24h cache.
        if os.path.exists(_FII_DII_CACHE) and (time.time() - os.path.getmtime(_FII_DII_CACHE)) < 86400:
            try:
                with open(_FII_DII_CACHE, encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, json.JSONDecodeError):
                pass
        try:
            with httpx.Client(headers=_NSE_HEADERS, timeout=10, follow_redirects=True) as client:
                try:
                    client.get("https://www.nseindia.com", timeout=10)
                except Exception:
                    pass
                resp = client.get("https://www.nseindia.com/api/fiidiiTradeReact")
            rows = resp.json() if resp.status_code == 200 else []
            fii_net = dii_net = 0.0
            for r in rows if isinstance(rows, list) else []:
                cat = str(r.get("category", "")).upper()
                net = r.get("netValue")
                if net is None:
                    net = float(r.get("buyValue", 0) or 0) - float(r.get("sellValue", 0) or 0)
                net = float(net)
                if "FII" in cat or "FPI" in cat:
                    fii_net = net
                elif "DII" in cat:
                    dii_net = net

            def trend(net: float) -> str:
                if net > 500:
                    return "BUYING"
                if net < -500:
                    return "SELLING"
                return "NEUTRAL"

            result = {
                "fii_net_buy_cr": round(fii_net, 2),
                "dii_net_buy_cr": round(dii_net, 2),
                "fii_trend": trend(fii_net),
                "dii_trend": trend(dii_net),
            }
            try:
                with open(_FII_DII_CACHE, "w", encoding="utf-8") as f:
                    json.dump(result, f)
            except OSError:
                pass
            return result
        except Exception:
            return neutral

    def _days_to_earnings(self, symbol: str) -> int | None:
        try:
            cal = yf.Ticker(f"{symbol}.NS").calendar
            dates = None
            if isinstance(cal, dict):
                dates = cal.get("Earnings Date")
            elif isinstance(cal, pd.DataFrame) and "Earnings Date" in cal.index:
                dates = cal.loc["Earnings Date"].tolist()
            if not dates:
                return None
            next_date = dates[0] if isinstance(dates, (list, tuple)) else dates
            next_dt = pd.Timestamp(next_date).to_pydatetime()
            if next_dt.tzinfo is None:
                next_dt = next_dt.replace(tzinfo=timezone.utc)
            delta = (next_dt - datetime.now(timezone.utc)).days
            return delta if delta >= 0 else None
        except Exception:
            return None

    @staticmethod
    def is_affordable(price: float, portfolio_value_inr: float | None = None) -> bool:
        """Can 1 share of `price` even clear the 3% max-risk-per-trade gate at
        its stop distance? If not, the stock is mathematically un-tradeable at
        current capital — correct behavior is to skip it silently, not to log
        a startup warning demanding more capital (that was noise: MRF-style
        stocks are always going to be unaffordable at a retail paper account,
        so the fix is exclusion, not escalation)."""
        if price <= 0:
            return True
        portfolio_value_inr = (portfolio_value_inr if portfolio_value_inr is not None
                                else settings.paper_capital_inr)
        risk_inr = price * (LIMITS.stop_loss_pct / 100)
        max_risk_inr = portfolio_value_inr * (LIMITS.max_risk_per_trade_pct / 100)
        return risk_inr <= max_risk_inr

    @staticmethod
    def _passes_liquidity_check(df, tier: str) -> bool:
        """Reject illiquid names — if we can't exit, we're trapped."""
        if df is None or len(df) < 20:
            return False
        try:
            avg_volume = float(df["Volume"].tail(20).mean())
        except Exception:
            return False
        return avg_volume >= _MIN_VOLUME_BY_TIER.get(tier, 20_000)

    @staticmethod
    def _pattern_polarity(description: str) -> str:
        desc = (description or "").lower()
        if any(c in desc for c in _NEG_CUES):
            return "negative"
        if any(c in desc for c in _POS_CUES):
            return "positive"
        return "neutral"

    def _score_symbol(
        self, symbol: str, nifty_1m: float | None, nifty_20d: float | None,
        fii_dii: dict, params: dict, nifty_30d: float | None = None,
    ) -> dict | None:
        df = self.fetcher.get_price_history(symbol, period="1y")
        if df is None or df.empty:
            return None

        tier = get_tier(symbol)
        tier_params = get_risk_params_for_tier(tier)
        # Liquidity gate — skip names we can't reliably exit (tier-scaled).
        if not self._passes_liquidity_check(df, tier):
            return None

        # Auto-extracted rules (src/memory/auto_rules.py) — the KB→behavior
        # feedback loop. A high-confidence stock veto skips scoring entirely;
        # a lower-confidence one still scores but takes a penalty.
        from src.memory.auto_rules import AutoRuleExtractor

        rules = AutoRuleExtractor.load()
        score_penalty = 0.0
        if symbol in rules.get("stock_vetoes", {}):
            veto = rules["stock_vetoes"][symbol]
            if veto.get("confidence", 0) >= 0.85:
                return None
            score_penalty = -3

        price = float(df["Close"].iloc[-1])
        if not self.is_affordable(price):
            self._unaffordable.append(symbol)
            return None

        ind = compute_indicators(df)
        meta = WATCHLIST.get(symbol, {"name": symbol, "sector": "Unknown"})
        year_high = float(df["High"].max())
        year_low = float(df["Low"].min())
        vr = ind["volume_ratio"]
        tier_min_vr = tier_params["min_volume_ratio"]

        rsi_low = params.get("scout_rsi_low", 50)
        rsi_high = params.get("scout_rsi_high", 65)
        adx_threshold = params.get("scout_adx_threshold", 20)

        score = 0
        reasons: list[str] = []
        flags: list[str] = []

        # 1. Momentum (0-3) — RSI bounds are adaptive (tuned by the enhancer).
        rsi = ind["rsi_14"]
        if rsi is not None:
            if rsi_low <= rsi <= rsi_high:
                score += 2
                reasons.append(f"RSI {rsi} momentum zone [{rsi_low}-{rsi_high}]")
            elif (rsi_low - 10) <= rsi < rsi_low:
                score += 1
                reasons.append(f"RSI {rsi} building")
            elif rsi > 70 or rsi < 30:
                flags.append("rsi_extreme")

        # Low-ADX (choppy) advisory using the adaptive threshold.
        adx = ind.get("adx_14")
        if adx is not None and adx < adx_threshold:
            flags.append("low_adx")

        # 2. Trend (0-3) + 52W breakout / falling-knife adjustment.
        ma50, ma200 = ind["ma_50"], ind["ma_200"]
        if ma50 is not None and ma200 is not None:
            if price > ma50 > ma200:
                score += 3
                reasons.append("price>MA50>MA200 strong uptrend")
            elif price > ma50 and ma50 < ma200:
                score += 1
                reasons.append("recovering above MA50")
        elif ma50 is not None and price > ma50:
            score += 1
            reasons.append("above MA50")

        if price > year_high * 0.98 and vr is not None and vr > 1.5:
            score += 1
            reasons.append("52W breakout with volume")
            flags.append("BREAKOUT")
        if price < year_low * 1.05:
            score -= 2
            reasons.append("near 52W low (falling knife)")
            flags.append("NEAR_52W_LOW")

        # 3. Volume (0-2) — mid/small tiers need stronger volume confirmation
        #    (tier_min_vr: large 1.0, mid 1.2, small 1.5).
        if vr is not None and vr >= tier_min_vr:
            if vr > 2.0:
                score += 2
                reasons.append(f"volume {vr}x (strong)")
            elif vr >= 1.5:
                score += 1
                reasons.append(f"volume {vr}x")
        elif vr is not None:
            flags.append(f"thin_volume_{tier}")

        # 4. Relative strength 1m (0-1).
        if len(df) >= 21 and nifty_1m is not None:
            stock_1m = (price / float(df["Close"].iloc[-21]) - 1) * 100
            if stock_1m > nifty_1m:
                score += 1
                reasons.append(f"outperforming Nifty 1m ({stock_1m:.1f}% vs {nifty_1m:.1f}%)")

        # 5. Position in 52W range (0-1).
        if year_high > year_low:
            pos = (price - year_low) / (year_high - year_low)
            if 0.6 <= pos <= 0.9:
                score += 1
                reasons.append(f"{pos * 100:.0f}% of 52W range (near highs, not extended)")

        # 6. Institutional flow (0-2).
        fii_t, dii_t = fii_dii.get("fii_trend"), fii_dii.get("dii_trend")
        if fii_t == "BUYING" and dii_t == "BUYING":
            score += 2
            reasons.append("FII+DII both buying")
        elif "SELLING" in (fii_t, dii_t):
            reasons.append("institutional selling — no points")
        elif "BUYING" in (fii_t, dii_t):
            score += 1
            reasons.append("one of FII/DII buying")

        # 7. Relative strength 20d vs Nifty (0-1).
        if len(df) >= 21 and nifty_20d is not None:
            stock_20d = (price / float(df["Close"].iloc[-21]) - 1) * 100
            if stock_20d > nifty_20d * 1.1:
                score += 1
                reasons.append(f"20d RS strong ({stock_20d:.1f}% vs Nifty {nifty_20d:.1f}%)")

        # 8. Relative strength vs Nifty over 30 days — leaders in a weak market
        # become the biggest winners when the market turns (professional-grade
        # sectorial relative-strength signal).
        if len(df) >= 31 and nifty_30d is not None:
            stock_30d = (price / float(df["Close"].iloc[-31]) - 1) * 100
            rs_score = stock_30d - nifty_30d
            if rs_score > 3:
                score += 1.5
                reasons.append(f"Outperforming Nifty by {rs_score:.1f}%")
            elif rs_score < -5:
                score -= 1
                reasons.append("Lagging the index")

        # Small-caps need a strong signal (breakout OR deep-oversold bounce) to rank —
        # their volatility punishes marginal setups.
        if tier == "small":
            strong = ("BREAKOUT" in flags) or (rsi is not None and rsi < 35)
            if not strong:
                score -= 2
                flags.append("SMALLCAP_WEAK_SIGNAL")

        # Auto-extracted setup-level rules (RSI range + ADX + optional trend).
        adx_signal = ind.get("adx_signal", "NEUTRAL") or "NEUTRAL"
        trend_signal = "UPTREND" if (ind.get("ma_50") and price > ind["ma_50"]) else "SIDEWAYS"
        for veto in rules.get("setup_vetoes", []):
            if (veto.get("rsi_min", 0) <= (rsi or 50) <= veto.get("rsi_max", 100)
                    and veto.get("adx_signal", "") == adx_signal
                    and (not veto.get("trend") or veto.get("trend") == trend_signal)):
                score_penalty -= 2
                flags.append("LEARNED_VETO")
                reasons.append(f"Learned veto: {veto.get('reason', '')}")
                break

        score_bonus = 0.0
        for boost in rules.get("setup_boosts", []):
            if (boost.get("rsi_min", 0) <= (rsi or 50) <= boost.get("rsi_max", 100)
                    and boost.get("adx_signal", "") == adx_signal):
                score_bonus = boost.get("bonus", 0)
                break
        if symbol in rules.get("stock_boosts", {}):
            score_bonus += rules["stock_boosts"][symbol].get("bonus", 0)
            flags.append("MEMORY_BOOST")

        score = score + score_penalty + score_bonus

        # NSE pre-open order imbalance (9:00-9:15 AM only) — a real-time signal
        # beyond end-of-day prices.
        try:
            from datetime import datetime
            from zoneinfo import ZoneInfo

            now_ist = datetime.now(ZoneInfo("Asia/Kolkata"))
            if 9 <= now_ist.hour < 10:
                from src.data.realtime_feeds import NSEPreOpenFeed

                imbalance = NSEPreOpenFeed().get_imbalance_for_stock(symbol)
                if imbalance > 20:
                    score += 1
                    flags.append(f"PREOPEN_BUY_IMBALANCE_{imbalance:.0f}%")
                elif imbalance < -20:
                    score -= 1
                    flags.append("PREOPEN_SELL_IMBALANCE")
        except Exception:
            pass

        # Bayesian per-stock win prior (src/ml/stock_priors.py) — a stock with
        # a strong track record in simulation gets a small nudge; conservative
        # until 5+ observations exist.
        from src.ml.stock_priors import StockPriors

        priors = StockPriors()
        prior = priors.get_win_prior(symbol)
        stats = priors.get_stats(symbol)
        if stats.get("total", 0) >= 5:
            prior_adjustment = (prior - 0.5) * 4  # maps 0-1 -> -2..+2
            score += prior_adjustment * 0.3
            if prior < 0.35:
                flags.append("STOCK_TRACK_RECORD_POOR")
            elif prior > 0.65:
                flags.append("STOCK_TRACK_RECORD_STRONG")

        return {
            "symbol": symbol,
            "name": meta["name"],
            "sector": meta["sector"],
            "tier": tier,
            "score": score,
            "rationale": "; ".join(reasons) if reasons else "no strong signals",
            "flags": flags,
        }

    # ── public API ─────────────────────────────────────────────────────────────
    def scan(self, sectors: list[str] | None = None) -> list[dict]:
        try:
            failure_patterns = self.lessons.get_failure_patterns()
        except Exception:
            failure_patterns = []

        nifty_1m, nifty_20d, nifty_30d = self._nifty_returns()
        fii_dii = self._get_fii_dii_data()
        params = get_adaptive_params()

        symbols = [s for s in WATCHLIST if not sectors or WATCHLIST[s]["sector"] in sectors]

        self._unaffordable = []
        scored: list[dict] = []
        # Fix 2 (scan-timeout root cause): _score_symbol is IO-bound (a yfinance
        # network fetch dominates its runtime), so scoring 241 symbols serially
        # is almost entirely wait time. Parallelize with a bounded worker pool;
        # a slow/hanging symbol gets a per-future timeout rather than stalling
        # the whole scan.
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FutureTimeout

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {
                pool.submit(self._score_symbol, symbol, nifty_1m, nifty_20d, fii_dii, params, nifty_30d): symbol
                for symbol in symbols
            }
            for future in futures:
                symbol = futures[future]
                try:
                    candidate = future.result(timeout=20)
                except _FutureTimeout:
                    console.print(f"[dim][SCOUT] {symbol} timed out — skipped[/dim]")
                    continue
                except Exception as exc:
                    console.print(f"[dim][SCOUT] {symbol} failed: {exc} — skipped[/dim]")
                    continue
                if candidate is not None:
                    scored.append(candidate)

        if self._unaffordable:
            examples = ", ".join(self._unaffordable[:5])
            console.print(
                f"[dim][SCOUT] {len(self._unaffordable)} symbols unaffordable at current "
                f"capital ({examples}{'...' if len(self._unaffordable) > 5 else ''}) — excluded[/dim]"
            )

        scored.sort(key=lambda c: c["score"], reverse=True)

        # Graded earnings window (only on strongest names — limits slow calendar calls).
        for candidate in scored[:8]:
            days = self._days_to_earnings(candidate["symbol"])
            if days is None:
                continue
            if days < 3:
                candidate["score"] = 0
                candidate["flags"].append("EARNINGS_IMMINENT")
                candidate["rationale"] += f"; earnings in {days}d (EARNINGS_IMMINENT)"
            elif days < 7:
                candidate["score"] = max(0, candidate["score"] - 3)
                candidate["flags"].append("EARNINGS_PROXIMITY")
                candidate["rationale"] += f"; earnings in {days}d (EARNINGS_PROXIMITY)"
            elif days < 14:
                candidate["flags"].append("EARNINGS_APPROACHING")
                candidate["rationale"] += f"; earnings in {days}d (warning)"

        # Knowledge-base integration: accumulated experience adjusts selection.
        try:
            knowledge = self.journal.get_active_knowledge(min_confidence=0.3)
        except Exception:
            knowledge = []
        for candidate in scored:
            for k in knowledge:
                if k.category != "SECTOR_PATTERN":
                    continue
                if candidate["sector"].lower() not in (k.pattern_description or "").lower():
                    continue
                if k.confidence < 0.7:
                    continue
                polarity = self._pattern_polarity(k.pattern_description)
                if polarity == "negative":
                    candidate["score"] -= 2
                    candidate["flags"].append(f"KNOWN_NEGATIVE_PATTERN: {k.pattern_description}")
                elif polarity == "positive":
                    candidate["flags"].append(f"KNOWN_POSITIVE_PATTERN: {k.pattern_description}")

        if failure_patterns:
            for candidate in scored:
                candidate["rationale"] += " | watch past failures: " + "; ".join(failure_patterns)

        scored.sort(key=lambda c: c["score"], reverse=True)
        top = scored[:10]
        if top:
            summary = ", ".join("{0}({1})".format(c["symbol"], c["score"]) for c in top)
            console.print(f"[cyan][SCOUT] Top candidates: {summary}[/cyan]")
        return top
