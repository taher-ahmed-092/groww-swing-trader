"""
Finnhub API wrapper — free tier (60 req/min), India coverage.

Provides company news, insider transactions, earnings surprises/calendar, and a
social sentiment score. Gracefully disabled when no key is set; every method returns
a safe default and never raises.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from config.settings import settings

log = logging.getLogger(__name__)


class FinnhubClient:
    def __init__(self) -> None:
        self._client = None
        if settings.has_finnhub:
            try:
                import finnhub

                self._client = finnhub.Client(api_key=settings.finnhub_api_key)
            except Exception as exc:  # pragma: no cover - import/init guard
                log.debug("Finnhub init failed: %s", exc)
                self._client = None

    @staticmethod
    def _sym(symbol: str) -> str:
        return symbol.replace(".NS", "").replace(".BO", "")

    def get_company_news(self, symbol: str, days_back: int = 7) -> list[dict]:
        if not self._client:
            return []
        try:
            from_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
            to_date = datetime.now().strftime("%Y-%m-%d")
            news = self._client.company_news(self._sym(symbol), _from=from_date, to=to_date)
            return [
                {
                    "headline": item.get("headline", ""),
                    "summary": item.get("summary", ""),
                    "url": item.get("url", ""),
                    "source": item.get("source", ""),
                    "published_at": item.get("datetime", 0),
                }
                for item in (news or [])[:20]
            ]
        except Exception as exc:
            log.debug("Finnhub news failed for %s: %s", symbol, exc)
            return []

    def get_insider_transactions(self, symbol: str) -> list[dict]:
        if not self._client:
            return []
        try:
            txns = self._client.stock_insider_transactions(symbol=self._sym(symbol))
            return [
                {
                    "name": t.get("name", ""),
                    "shares": t.get("share", 0),
                    "transaction_type": t.get("transactionCode", ""),
                    "value": t.get("value", 0),
                    "date": t.get("transactionDate", ""),
                }
                for t in (txns.get("data") or [])[:10]
            ]
        except Exception as exc:
            log.debug("Finnhub insider data failed for %s: %s", symbol, exc)
            return []

    def get_earnings_surprises(self, symbol: str) -> list[dict]:
        if not self._client:
            return []
        try:
            surprises = self._client.company_earnings(self._sym(symbol), limit=4)
            result = []
            for s in surprises or []:
                actual = s.get("actual", 0) or 0
                estimate = s.get("estimate", 0) or 0
                surprise_pct = ((actual - estimate) / abs(estimate) * 100) if estimate else 0
                result.append({
                    "period": s.get("period", ""),
                    "actual": actual,
                    "estimate": estimate,
                    "surprise_pct": round(surprise_pct, 2),
                })
            return result
        except Exception as exc:
            log.debug("Finnhub earnings failed for %s: %s", symbol, exc)
            return []

    def get_earnings_calendar(self, days_ahead: int = 14) -> dict[str, str]:
        if not self._client:
            return {}
        try:
            from_date = datetime.now().strftime("%Y-%m-%d")
            to_date = (datetime.now() + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
            calendar = self._client.earnings_calendar(
                _from=from_date, to=to_date, symbol="", international=False
            )
            result = {}
            for item in calendar.get("earningsCalendar") or []:
                sym, date = item.get("symbol", ""), item.get("date", "")
                if sym and date:
                    result[sym] = date
            return result
        except Exception as exc:
            log.debug("Finnhub calendar failed: %s", exc)
            return {}

    def get_sentiment_score(self, symbol: str) -> dict:
        if not self._client:
            return {"score": 0, "buzz": 0, "label": "NEUTRAL", "source": "none"}
        try:
            sentiment = self._client.stock_social_sentiment(self._sym(symbol), _from="", to="")
            all_data = (sentiment.get("reddit", []) or []) + (sentiment.get("twitter", []) or [])
            if all_data:
                avg = sum(d.get("score", 0) for d in all_data) / len(all_data)
                avg = max(-1, min(1, avg))
            else:
                avg = 0
            label = "POSITIVE" if avg > 0.2 else "NEGATIVE" if avg < -0.2 else "NEUTRAL"
            return {"score": round(avg, 3), "label": label, "source": "finnhub"}
        except Exception as exc:
            log.debug("Finnhub sentiment failed: %s", exc)
            return {"score": 0, "buzz": 0, "label": "NEUTRAL", "source": "error"}
