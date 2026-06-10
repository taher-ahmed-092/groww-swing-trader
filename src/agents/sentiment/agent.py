"""
Social sentiment sub-agent — called from FundamentalAgent.

Checks only FREE public sources (Yahoo news, Google News RSS, NSE/BSE announcements,
Reddit, StockTwits), runs them concurrently, and produces a structured sentiment
report for judge context. Every collector is wrapped: this module NEVER raises and
returns a neutral result when nothing is found.
"""
from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import feedparser
import httpx
import yfinance as yf
from rich.console import Console

from config.settings import settings
from src.llm import get_llm, parse_json_response

console = Console()

_CACHE_DIR = os.path.join("data", "cache")
_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.nseindia.com"}

RED_FLAG_KEYWORDS = [
    "fraud", "scam", "sebi notice", "ed raid", "promoter sold", "pledged shares increase",
    "earnings miss", "guidance cut", "regulatory action", "default", "npa", "insolvency",
]
POSITIVE_KEYWORDS = [
    "buyback", "dividend", "beat estimates", "order win", "partnership", "expansion",
    "upgrade", "strong q", "promoter buys",
]
CORPORATE_ACTION_KEYWORDS = [
    "dividend", "bonus", "split", "buyback", "rights issue", "agm", "board meeting", "results",
]

# NSE symbol → BSE 6-digit code (subset of the watchlist; skip BSE if absent).
BSE_CODES = {
    "RELIANCE": "500325", "TCS": "532540", "INFY": "500209", "HDFCBANK": "500180",
    "ICICIBANK": "532174", "SBIN": "500112", "ITC": "500875", "LT": "500510",
    "WIPRO": "507685", "HINDUNILVR": "500696", "MARUTI": "532500", "SUNPHARMA": "524715",
}

# StockTwits is US-focused; only useful for ADR-listed names.
STOCKTWITS_SUPPORTED = {"INFY", "WIPRO", "HDFCBANK", "ICICIBANK", "WIT", "HDB", "IBN"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SocialSentimentAgent:
    FREE_SOURCES = [
        "yahoo_finance_news", "google_news_rss", "nse_announcements",
        "bse_announcements", "reddit_india_invest", "stocktwits",
    ]

    def __init__(self) -> None:
        os.makedirs(_CACHE_DIR, exist_ok=True)

    # ── collectors (each returns list[dict]; never raises) ─────────────────────
    def _get_yahoo_news(self, symbol: str) -> list[dict]:
        try:
            news = yf.Ticker(f"{symbol}.NS").news or []
            items = []
            for n in news:
                content = n.get("content", n)  # newer yfinance nests under "content"
                title = content.get("title") or n.get("title", "")
                summary = content.get("summary", "") or content.get("description", "")
                url = (
                    n.get("link")
                    or (content.get("canonicalUrl") or {}).get("url")
                    or content.get("clickThroughUrl", {}).get("url", "")
                )
                pub = content.get("pubDate") or n.get("providerPublishTime")
                items.append({"source": "yahoo", "text": f"{title}. {summary}".strip(),
                              "url": url, "published_at": pub})
            return items
        except Exception:
            return []

    def _get_google_news_rss(self, company_name: str, symbol: str) -> list[dict]:
        try:
            q = f"{company_name}+stock+India".replace(" ", "+")
            url = f"https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"
            feed = feedparser.parse(url)
            items = []
            for e in feed.entries[:15]:
                title = getattr(e, "title", "")
                if "stock" not in title.lower() and symbol.lower() not in title.lower():
                    continue
                items.append({
                    "source": "google_news",
                    "text": f"{title}. {getattr(e, 'summary', '')}",
                    "url": getattr(e, "link", ""),
                    "published_at": getattr(e, "published", None),
                })
            return items[:10]
        except Exception:
            return []

    def _cached_json(self, path: str, ttl_seconds: int) -> list[dict] | None:
        if os.path.exists(path) and (time.time() - os.path.getmtime(path)) < ttl_seconds:
            try:
                with open(path, encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, json.JSONDecodeError):
                return None
        return None

    def _write_json(self, path: str, data: list[dict]) -> None:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, default=str)
        except OSError:
            pass

    def _get_nse_announcements(self, symbol: str) -> list[dict]:
        cache = os.path.join(_CACHE_DIR, f"{symbol}_nse_announcements.json")
        cached = self._cached_json(cache, 6 * 3600)
        if cached is not None:
            return cached
        try:
            url = (
                "https://www.nseindia.com/api/corp-info"
                f"?symbol={symbol}&corpType=announcements"
            )
            with httpx.Client(headers=_HEADERS, timeout=10, follow_redirects=True) as client:
                # Prime the session cookie (NSE rejects cold API calls).
                try:
                    client.get("https://www.nseindia.com", timeout=10)
                except Exception:
                    pass
                resp = client.get(url)
            data = resp.json() if resp.status_code == 200 else []
            rows = data if isinstance(data, list) else data.get("data", []) if isinstance(data, dict) else []
            items = []
            for r in rows[:10]:
                if not isinstance(r, dict):
                    continue
                text = r.get("desc") or r.get("subject") or r.get("attchmntText") or str(r)
                items.append({
                    "source": "nse",
                    "text": str(text),
                    "url": r.get("attchmntFile", ""),
                    "published_at": r.get("an_dt") or r.get("dt"),
                })
            self._write_json(cache, items)
            return items
        except Exception:
            return []

    def _get_bse_announcements(self, symbol: str) -> list[dict]:
        bse_code = BSE_CODES.get(symbol)
        if not bse_code:
            return []
        try:
            today = datetime.now().strftime("%Y%m%d")
            url = (
                "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
                f"?strCat=-1&strPrevDate={today}&strScrip={bse_code}"
                f"&strSearch=P&strToDate={today}&strType=C"
            )
            headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.bseindia.com"}
            with httpx.Client(headers=headers, timeout=10) as client:
                resp = client.get(url)
            data = resp.json() if resp.status_code == 200 else {}
            rows = data.get("Table", []) if isinstance(data, dict) else []
            items = []
            for r in rows[:5]:
                items.append({
                    "source": "bse",
                    "text": str(r.get("NEWSSUB") or r.get("HEADLINE") or r),
                    "url": r.get("ATTACHMENTNAME", ""),
                    "published_at": r.get("NEWS_DT"),
                })
            return items
        except Exception:
            return []

    def _get_reddit_sentiment(self, symbol: str, company_name: str) -> list[dict]:
        if not (settings.reddit_client_id and settings.reddit_client_secret):
            console.print("[yellow][SENTIMENT] Reddit skipped: no credentials[/yellow]")
            return []
        try:
            import praw

            reddit = praw.Reddit(
                client_id=settings.reddit_client_id,
                client_secret=settings.reddit_client_secret,
                user_agent="groww-swing-trader/1.0 (sentiment)",
                check_for_async=False,
            )
            reddit.read_only = True
            items = []
            subs = reddit.subreddit("IndiaInvestments+IndianStockMarket")
            for post in subs.search(f"{symbol} {company_name}", sort="new",
                                    time_filter="week", limit=10):
                items.append({
                    "source": "reddit",
                    "text": f"{post.title}. {(post.selftext or '')[:200]}",
                    "url": f"https://reddit.com{post.permalink}",
                    "published_at": post.created_utc,
                    "score": post.score,
                })
            return items
        except Exception:
            return []

    def _get_stocktwits(self, symbol: str) -> list[dict]:
        if symbol not in STOCKTWITS_SUPPORTED:
            return []
        try:
            url = f"https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json"
            with httpx.Client(timeout=10) as client:
                resp = client.get(url)
            data = resp.json() if resp.status_code == 200 else {}
            items = []
            for m in (data.get("messages", []) or [])[:20]:
                entities = m.get("entities", {}) or {}
                sentiment = (entities.get("sentiment") or {}).get("basic")
                items.append({
                    "source": "stocktwits",
                    "text": m.get("body", ""),
                    "url": "",
                    "published_at": m.get("created_at"),
                    "st_sentiment": sentiment,
                })
            return items
        except Exception:
            return []

    # ── scoring ────────────────────────────────────────────────────────────────
    def _score_sentiment(self, all_items: list[dict]) -> tuple[float, list[str], list[str]]:
        red_flags: list[str] = []
        positive_signals: list[str] = []
        scores: list[int] = []

        for item in all_items:
            text = (item.get("text") or "").lower()
            item_score = 0
            for kw in RED_FLAG_KEYWORDS:
                if kw in text:
                    item_score -= 1
                    red_flags.append(kw)
            for kw in POSITIVE_KEYWORDS:
                if kw in text:
                    item_score += 1
                    positive_signals.append(kw)
            scores.append(item_score)

        keyword_score = sum(scores) / max(len(all_items), 1)
        keyword_score = max(-1.0, min(1.0, keyword_score))
        final_score = keyword_score

        # Optional LLM blend when there's enough signal and a key is configured.
        if len(all_items) > 3 and settings.has_anthropic_key:
            headlines = [i.get("text", "")[:160] for i in all_items[:10]]
            prompt = (
                "Score overall market sentiment for these recent headlines from -1.0 "
                "(very negative) to +1.0 (very positive).\n"
                f"Headlines: {headlines}\n\n"
                'Return JSON: {"score": <float -1..1>, "red_flags": [<str>], '
                '"positive_signals": [<str>]}'
            )
            try:
                resp = get_llm(temperature=0).invoke(
                    [("system", "You are a financial sentiment analyst."), ("human", prompt)]
                )
                parsed = parse_json_response(getattr(resp, "content", "") or "")
            except Exception:
                parsed = {}
            if parsed and parsed.get("score") is not None:
                llm_score = max(-1.0, min(1.0, float(parsed["score"])))
                final_score = 0.4 * keyword_score + 0.6 * llm_score
                red_flags += [str(x) for x in parsed.get("red_flags", []) or []]
                positive_signals += [str(x) for x in parsed.get("positive_signals", []) or []]

        return round(max(-1.0, min(1.0, final_score)), 4), sorted(set(red_flags)), sorted(set(positive_signals))

    def _extract_corporate_actions(self, nse_items: list[dict]) -> list[str]:
        found = set()
        for item in nse_items:
            text = (item.get("text") or "").lower()
            for kw in CORPORATE_ACTION_KEYWORDS:
                if kw in text:
                    found.add(kw)
        return sorted(found)

    @staticmethod
    def _label(score: float) -> str:
        if score >= 0.5:
            return "VERY_POSITIVE"
        if score >= 0.15:
            return "POSITIVE"
        if score <= -0.5:
            return "VERY_NEGATIVE"
        if score <= -0.15:
            return "NEGATIVE"
        return "NEUTRAL"

    @staticmethod
    def _recent(item: dict) -> bool:
        """Keep items from the last 7 days; keep items with unparseable dates."""
        pub = item.get("published_at")
        if pub is None:
            return True
        try:
            if isinstance(pub, (int, float)):
                dt = datetime.fromtimestamp(float(pub), tz=timezone.utc)
            else:
                # feedparser/ISO strings — best-effort parse.
                from email.utils import parsedate_to_datetime
                try:
                    dt = parsedate_to_datetime(str(pub))
                except (TypeError, ValueError):
                    dt = datetime.fromisoformat(str(pub).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt >= _now() - timedelta(days=7)
        except Exception:
            return True

    # ── public API ─────────────────────────────────────────────────────────────
    def _neutral(self, sources_checked: list[str]) -> dict:
        return {
            "sentiment_score": 0.0, "sentiment_label": "NEUTRAL", "total_items_found": 0,
            "red_flags": [], "positive_signals": [], "key_events": [],
            "sources_checked": sources_checked, "sources_with_data": [],
            "raw_headlines": [], "nse_announcements_count": 0, "corporate_actions": [],
        }

    def analyze(self, symbol: str, company_name: str) -> dict:
        collectors = {
            "yahoo_finance_news": lambda: self._get_yahoo_news(symbol),
            "google_news_rss": lambda: self._get_google_news_rss(company_name, symbol),
            "nse_announcements": lambda: self._get_nse_announcements(symbol),
            "bse_announcements": lambda: self._get_bse_announcements(symbol),
            "reddit_india_invest": lambda: self._get_reddit_sentiment(symbol, company_name),
            "stocktwits": lambda: self._get_stocktwits(symbol),
        }

        results: dict[str, list[dict]] = {}
        try:
            with ThreadPoolExecutor(max_workers=6) as pool:
                futures = {pool.submit(fn): name for name, fn in collectors.items()}
                for fut in as_completed(futures):
                    name = futures[fut]
                    try:
                        results[name] = fut.result() or []
                    except Exception:
                        results[name] = []
        except Exception:
            results = {name: [] for name in collectors}

        sources_checked = list(collectors.keys())
        sources_with_data = [name for name, items in results.items() if items]

        # Finnhub/NewsAggregator enrichment — only when a key is configured, so
        # keyless/demo runs and unit tests keep the original lightweight behavior.
        agg = self._finnhub_enrich(symbol, company_name) if settings.has_finnhub else {}

        # Flatten, dedupe by URL (keep URL-less items), filter to last 7 days.
        seen_urls: set[str] = set()
        all_items: list[dict] = []
        for items in results.values():
            for item in items:
                url = item.get("url")
                if url and url in seen_urls:
                    continue
                if url:
                    seen_urls.add(url)
                if self._recent(item):
                    all_items.append(item)

        if not all_items and not agg:
            return self._neutral(sources_checked)

        score, red_flags, positive_signals = self._score_sentiment(all_items)
        nse_items = results.get("nse_announcements", [])
        corporate_actions = self._extract_corporate_actions(nse_items)
        key_events = [i.get("text", "")[:120] for i in nse_items[:5]]
        raw_headlines = [i.get("text", "")[:160] for i in all_items[:5]]

        # Merge aggregator signals + blend Finnhub's own sentiment score.
        if agg:
            red_flags = sorted(set(red_flags + agg.get("red_flags", [])))
            positive_signals = sorted(set(positive_signals + agg.get("positive_signals", [])))
            sources_with_data = sorted(set(sources_with_data + ["finnhub"]))
            fh_score = agg.get("finnhub_sentiment", {}).get("score")
            if fh_score is not None:
                score = round(max(-1.0, min(1.0, 0.7 * score + 0.3 * fh_score)), 4)
            if agg.get("insider_signals"):
                key_events = (key_events + agg["insider_signals"])[:8]

        return {
            "sentiment_score": score,
            "sentiment_label": self._label(score),
            "total_items_found": len(all_items),
            "red_flags": red_flags,
            "positive_signals": positive_signals,
            "key_events": key_events,
            "sources_checked": sources_checked,
            "sources_with_data": sources_with_data,
            "raw_headlines": raw_headlines,
            "nse_announcements_count": len(nse_items),
            "corporate_actions": corporate_actions,
        }

    def _finnhub_enrich(self, symbol: str, company_name: str) -> dict:
        """Pull richer signals via NewsAggregator (insider/earnings/finnhub sentiment)."""
        try:
            from src.data.news_aggregator import NewsAggregator

            return NewsAggregator().get_all_news(symbol, company_name)
        except Exception:
            return {}
