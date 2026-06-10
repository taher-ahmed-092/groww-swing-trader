"""
News aggregator — combines all FREE sources into one signal-rich view.

Sources: Finnhub (news/insider/earnings/sentiment) + Google News RSS + Indian
financial RSS (ET/BS/Livemint) + NewsAPI + NSE bulk deals (smart money). Feeds the
SocialSentimentAgent. Every collector is defensive; the aggregator never raises.
"""
from __future__ import annotations

import logging
from datetime import date, datetime

from config.settings import settings
from src.data.finnhub_client import FinnhubClient

log = logging.getLogger(__name__)


class NewsAggregator:
    ET_RSS = "https://economictimes.indiatimes.com/markets/stocks/rss.cms"
    BS_RSS = "https://www.business-standard.com/rss/markets-106.rss"
    LIVEMINT_RSS = "https://www.livemint.com/rss/companies"

    def get_all_news(self, symbol: str, company_name: str, sector: str = "") -> dict:
        finnhub = FinnhubClient()

        items: list[dict] = []
        items += self._get_finnhub_news(finnhub, symbol)
        items += self._get_google_news_rss(company_name, symbol)
        items += self._get_india_rss(company_name)
        items += self._get_newsapi(company_name)

        seen: set[str] = set()
        unique_items: list[dict] = []
        for item in items:
            key = (item.get("headline", "")[:40]).lower()
            if key and key not in seen:
                seen.add(key)
                unique_items.append(item)

        insider_signals = self._parse_insider_signals(finnhub.get_insider_transactions(symbol))
        earnings_surprises = finnhub.get_earnings_surprises(symbol)
        calendar = finnhub.get_earnings_calendar(days_ahead=14)
        upcoming_earnings = calendar.get(symbol.replace(".NS", "").replace(".BO", ""))
        finnhub_sentiment = finnhub.get_sentiment_score(symbol)
        bulk_deals = self._get_nse_bulk_deals(symbol)

        red_flags: list[str] = []
        positive_signals: list[str] = []
        for sig in insider_signals:
            (red_flags if "SELLING" in sig else positive_signals).append(sig)

        if upcoming_earnings:
            try:
                days = (datetime.strptime(upcoming_earnings, "%Y-%m-%d").date() - date.today()).days
                if days <= 7:
                    red_flags.append(f"Earnings in {days} days — high uncertainty")
                elif days <= 14:
                    red_flags.append(f"Earnings in {days} days — watch for guidance")
            except (ValueError, TypeError):
                pass

        if earnings_surprises:
            head = earnings_surprises[:3]
            if all(s["surprise_pct"] > 0 for s in head):
                positive_signals.append("Consistent earnings beats last 3 quarters")
            if all(s["surprise_pct"] < -5 for s in head):
                red_flags.append("Consistent earnings misses last 3 quarters")

        for d in bulk_deals:
            (positive_signals if "BUY" in d.upper() else red_flags).append(d)

        return {
            "items": unique_items[:30],
            "total": len(unique_items),
            "finnhub_sentiment": finnhub_sentiment,
            "insider_signals": insider_signals,
            "earnings_surprises": earnings_surprises,
            "upcoming_earnings": upcoming_earnings,
            "red_flags": red_flags[:5],
            "positive_signals": positive_signals[:5],
            "bulk_deals": bulk_deals,
        }

    def _get_finnhub_news(self, finnhub: FinnhubClient, symbol: str) -> list[dict]:
        return [
            {"headline": i["headline"], "source": i["source"],
             "sentiment_hint": "NEUTRAL", "published_at": i["published_at"]}
            for i in finnhub.get_company_news(symbol)
        ]

    def _get_google_news_rss(self, company_name: str, symbol: str) -> list[dict]:
        try:
            import feedparser

            query = company_name.replace(" ", "+")
            url = f"https://news.google.com/rss/search?q={query}+stock+India&hl=en-IN&gl=IN"
            feed = feedparser.parse(url)
            return [
                {"headline": e.title, "source": "Google News",
                 "sentiment_hint": "NEUTRAL", "published_at": e.get("published", "")}
                for e in feed.entries[:10]
            ]
        except Exception:
            return []

    def _get_india_rss(self, company_name: str) -> list[dict]:
        results: list[dict] = []
        for rss_url in (self.ET_RSS, self.BS_RSS, self.LIVEMINT_RSS):
            try:
                import feedparser

                feed = feedparser.parse(rss_url)
                for e in feed.entries[:20]:
                    if company_name.lower() in e.title.lower():
                        results.append({
                            "headline": e.title, "source": rss_url.split("/")[2],
                            "sentiment_hint": "NEUTRAL", "published_at": e.get("published", ""),
                        })
            except Exception:
                pass
        return results[:10]

    def _get_newsapi(self, company_name: str) -> list[dict]:
        if not settings.news_api_key:
            return []
        try:
            import requests

            r = requests.get(
                "https://newsapi.org/v2/everything",
                params={"q": f"{company_name} India stock", "sortBy": "publishedAt",
                        "pageSize": 10, "apiKey": settings.news_api_key, "language": "en"},
                timeout=10,
            )
            return [
                {"headline": a["title"], "source": a["source"]["name"],
                 "sentiment_hint": "NEUTRAL", "published_at": a["publishedAt"]}
                for a in r.json().get("articles", [])[:10]
            ]
        except Exception:
            return []

    def _get_nse_bulk_deals(self, symbol: str) -> list[str]:
        try:
            import requests

            clean = symbol.replace(".NS", "").replace(".BO", "")
            r = requests.get(
                f"https://www.nseindia.com/api/bulk-deals?symbol={clean}",
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.nseindia.com"},
                timeout=10,
            )
            if r.status_code == 200:
                deals = r.json().get("data", [])
                return [
                    f"{d.get('clientName', '?')} {d.get('buySell', '?')} "
                    f"{d.get('quantity', '?')} shares @ ₹{d.get('tradePrice', '?')}"
                    for d in deals[:5]
                ]
        except Exception:
            pass
        return []

    def _parse_insider_signals(self, transactions: list) -> list[str]:
        signals: list[str] = []
        for t in transactions[:5]:
            txn_type = t.get("transaction_type", "")
            value = t.get("value", 0) or 0
            name = t.get("name", "Unknown")
            d = t.get("date", "")
            if txn_type in ("P", "A", "M"):
                signals.append(f"INSIDER BUYING: {name} bought ₹{value:,.0f} on {d}")
            elif txn_type in ("S", "D"):
                signals.append(f"INSIDER SELLING: {name} sold ₹{value:,.0f} on {d}")
        return signals
