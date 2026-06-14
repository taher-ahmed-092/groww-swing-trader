"""
Weekly research via the Anthropic Message Batches API (~50% cheaper).

Saturday night we submit fundamental analysis for the whole watchlist as one batch;
Sunday morning we retrieve results and cache them. FundamentalAgent checks this cache
first (a hit costs ₹0). Degrades to None everywhere without an API key. Never raises.
"""
from __future__ import annotations

import json
import logging
import os
import time

from config.settings import settings

log = logging.getLogger(__name__)

_BATCH_DIR = os.path.join("data", "cache", "batches")
_LATEST = os.path.join(_BATCH_DIR, "latest_batch.json")
_RESULTS = os.path.join(_BATCH_DIR, "batch_fundamentals.json")
_MAX_AGE_SECONDS = 36 * 3600

FUNDAMENTAL_SYSTEM_PROMPT = (
    "You are an equity fundamental analyst. Given pre-computed numbers for one "
    "company, return JSON {score, proceed, reasoning, strengths, weaknesses, moat_strength}. "
    "Never invent numbers."
)


class WeeklyResearchBatch:
    def _client(self):
        from anthropic import Anthropic

        return Anthropic(api_key=settings.anthropic_api_key)

    def submit_weekly_batch(self, candidates: list[dict]) -> str | None:
        if not settings.has_anthropic_key or not candidates:
            return None
        try:
            requests = [
                {
                    "custom_id": f"fundamental-{c['symbol']}",
                    "params": {
                        "model": settings.llm_model_default, "max_tokens": 800,
                        "messages": [{"role": "user",
                                      "content": f"{FUNDAMENTAL_SYSTEM_PROMPT}\n\nData: {c}"}],
                    },
                }
                for c in candidates if c.get("symbol")
            ]
            batch = self._client().messages.batches.create(requests=requests)
            os.makedirs(_BATCH_DIR, exist_ok=True)
            with open(_LATEST, "w", encoding="utf-8") as f:
                json.dump({"batch_id": batch.id, "submitted_at": time.time()}, f)
            return batch.id
        except Exception as exc:
            log.debug("submit_weekly_batch failed: %s", exc)
            return None

    def check_and_retrieve_results(self) -> dict | None:
        if not settings.has_anthropic_key or not os.path.exists(_LATEST):
            return None
        try:
            with open(_LATEST, encoding="utf-8") as f:
                batch_id = json.load(f).get("batch_id")
            if not batch_id:
                return None
            client = self._client()
            status = client.messages.batches.retrieve(batch_id)
            if getattr(status, "processing_status", "") != "ended":
                return None
            results: dict[str, dict] = {}
            from src.llm import parse_json_response

            for entry in client.messages.batches.results(batch_id):
                cid = getattr(entry, "custom_id", "")
                symbol = cid.replace("fundamental-", "")
                try:
                    text = entry.result.message.content[0].text
                    parsed = parse_json_response(text)
                    if parsed:
                        results[symbol] = parsed
                except Exception:
                    continue
            os.makedirs(_BATCH_DIR, exist_ok=True)
            with open(_RESULTS, "w", encoding="utf-8") as f:
                json.dump(results, f)
            return results
        except Exception as exc:
            log.debug("check_and_retrieve_results failed: %s", exc)
            return None

    def get_batch_fundamental(self, symbol: str) -> dict | None:
        """Return a cached batch verdict for symbol if the cache is < 36h old."""
        if not os.path.exists(_RESULTS):
            return None
        if (time.time() - os.path.getmtime(_RESULTS)) > _MAX_AGE_SECONDS:
            return None
        try:
            with open(_RESULTS, encoding="utf-8") as f:
                return json.load(f).get(symbol)
        except (OSError, json.JSONDecodeError):
            return None
