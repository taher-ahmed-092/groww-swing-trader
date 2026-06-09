"""
screener.in scraper — the primary fundamental source for Indian equities.

Far richer than yfinance for NSE/BSE names (ROCE, promoter holding/pledging, 3yr
CAGRs). Best-effort HTML parsing: screener.in markup shifts over time, so every
field degrades to None rather than raising. Responses are cached for 24h and each
network request is followed by a polite 2s pause.

NOTE: this is a public-page scraper, not an API. Respect screener.in — keep request
volume low (the weekly scan touches only a handful of symbols).
"""
from __future__ import annotations

import json
import os
import re
import time

import requests
from bs4 import BeautifulSoup
from rich.console import Console

console = Console()

_CACHE_DIR = os.path.join("data", "cache")
_CACHE_TTL_SECONDS = 24 * 60 * 60  # 24 hours
_REQUEST_PAUSE_SECONDS = 2.0
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
    )
}


def _to_float(text) -> float | None:
    """Parse a number out of messy text like '₹ 1,234 Cr', '12.3 %', '-'."""
    if text is None:
        return None
    s = str(text)
    s = s.replace(",", "").replace("%", "").replace("₹", "")
    s = s.replace("Cr", "").replace("cr", "").strip()
    m = re.search(r"-?\d+\.?\d*", s)
    if not m:
        return None
    try:
        return float(m.group())
    except ValueError:
        return None


class ScreenerScraper:
    def __init__(self) -> None:
        os.makedirs(_CACHE_DIR, exist_ok=True)

    # ── caching ──────────────────────────────────────────────────────────────
    def _cache_path(self, symbol: str) -> str:
        return os.path.join(_CACHE_DIR, f"{symbol.upper()}_screener.json")

    def _read_cache(self, symbol: str) -> dict | None:
        path = self._cache_path(symbol)
        if not os.path.exists(path):
            return None
        if (time.time() - os.path.getmtime(path)) > _CACHE_TTL_SECONDS:
            return None
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return None

    def _write_cache(self, symbol: str, data: dict) -> None:
        try:
            with open(self._cache_path(symbol), "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except OSError:
            pass

    # ── fetch ────────────────────────────────────────────────────────────────
    def _fetch_html(self, symbol: str) -> str | None:
        urls = [
            f"https://www.screener.in/company/{symbol.upper()}/consolidated/",
            f"https://www.screener.in/company/{symbol.upper()}/",
        ]
        for url in urls:
            try:
                resp = requests.get(url, headers=_HEADERS, timeout=15)
                time.sleep(_REQUEST_PAUSE_SECONDS)  # be respectful
                if resp.status_code == 200 and resp.text:
                    return resp.text
            except requests.RequestException as exc:
                console.print(f"[yellow]screener fetch failed ({url}): {exc}[/yellow]")
                continue
        return None

    # ── parse ────────────────────────────────────────────────────────────────
    def _parse(self, html: str) -> dict:
        soup = BeautifulSoup(html, "lxml")
        data: dict = {
            "name": None, "sector": None, "market_cap_cr": None, "pe_ratio": None,
            "pb_ratio": None, "div_yield": None, "roce_pct": None, "roe_pct": None,
            "debt_to_equity": None, "current_ratio": None, "promoter_holding_pct": None,
            "promoter_pledged_pct": None, "sales_growth_3yr": None,
            "profit_growth_3yr": None, "revenue_ttm_cr": None, "net_profit_ttm_cr": None,
        }

        # Name.
        h1 = soup.find("h1")
        if h1:
            data["name"] = h1.get_text(strip=True)

        # Top ratios list: <li><span class="name">..</span><span class="value">..</span></li>
        label_map = {
            "market cap": "market_cap_cr",
            "stock p/e": "pe_ratio",
            "price to book": "pb_ratio",
            "book value": None,  # used to derive P/B below if needed
            "dividend yield": "div_yield",
            "roce": "roce_pct",
            "roe": "roe_pct",
            "debt to equity": "debt_to_equity",
            "current ratio": "current_ratio",
        }
        current_price = None
        book_value = None
        for li in soup.select("li"):
            name_el = li.find(class_="name")
            value_el = li.find(class_="value") or li.find(class_="number")
            if not name_el or not value_el:
                continue
            label = name_el.get_text(strip=True).lower()
            value = _to_float(value_el.get_text(" ", strip=True))
            if "current price" in label:
                current_price = value
            if "book value" in label:
                book_value = value
            for key, field in label_map.items():
                if key in label and field is not None:
                    if data.get(field) is None:
                        data[field] = value

        # Derive P/B if screener didn't expose it directly.
        if data["pb_ratio"] is None and current_price and book_value:
            data["pb_ratio"] = round(current_price / book_value, 4) if book_value else None

        self._parse_tables(soup, data)
        return data

    def _parse_tables(self, soup: BeautifulSoup, data: dict) -> None:
        text = soup.get_text("\n", strip=True)

        # Compounded growth blocks (screener renders these as small tables).
        # We grep the flattened text for "3 Years <value>%" under each heading.
        def _three_year_after(heading: str) -> float | None:
            idx = text.lower().find(heading.lower())
            if idx == -1:
                return None
            window = text[idx: idx + 400]
            m = re.search(r"3\s*Years?\s*[:\n]?\s*(-?\d+\.?\d*)\s*%", window, re.IGNORECASE)
            return _to_float(m.group(1)) if m else None

        if data["sales_growth_3yr"] is None:
            data["sales_growth_3yr"] = _three_year_after("Compounded Sales Growth")
        if data["profit_growth_3yr"] is None:
            data["profit_growth_3yr"] = _three_year_after("Compounded Profit Growth")

        # Shareholding: promoter holding + pledge.
        if data["promoter_holding_pct"] is None:
            m = re.search(r"Promoters?\s*[:\n]?\s*(\d+\.?\d*)\s*%", text, re.IGNORECASE)
            if m:
                data["promoter_holding_pct"] = _to_float(m.group(1))
        if data["promoter_pledged_pct"] is None:
            m = re.search(r"Pledged\s*(?:percentage)?\s*[:\n]?\s*(\d+\.?\d*)\s*%", text, re.IGNORECASE)
            if m:
                data["promoter_pledged_pct"] = _to_float(m.group(1))

    # ── public API ─────────────────────────────────────────────────────────────
    def get_company_data(self, symbol: str) -> dict | None:
        cached = self._read_cache(symbol)
        if cached is not None:
            return cached

        html = self._fetch_html(symbol)
        if not html:
            return None

        try:
            data = self._parse(html)
        except Exception as exc:  # parsing must never raise
            console.print(f"[yellow]screener parse failed for {symbol}: {exc}[/yellow]")
            return None

        self._write_cache(symbol, data)
        return data

    def check_hard_rejects(self, data: dict) -> list[str]:
        """Automatic disqualifiers. Empty list = no hard rejects."""
        data = data or {}
        rejects: list[str] = []

        pledged = data.get("promoter_pledged_pct")
        if pledged is not None and pledged > 30:
            rejects.append(f"High promoter pledging ({pledged}%) — manipulation risk")

        holding = data.get("promoter_holding_pct")
        if holding is not None and holding < 25:
            rejects.append(f"Very low promoter holding ({holding}%) — weak skin in game")

        dte = data.get("debt_to_equity")
        if dte is not None and dte > 3.0:
            rejects.append(f"Dangerous debt levels (D/E = {dte})")

        roce = data.get("roce_pct")
        if roce is not None and roce < 10:
            rejects.append(f"Poor capital efficiency (ROCE {roce}%)")

        return rejects
