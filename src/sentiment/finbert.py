"""
FinBERT — finance-tuned sentiment (ProsusAI/finbert).

Understands financial language ("beat estimates" = positive, "debt restructuring"
= negative). Three tiers, all ₹0: local model (only if torch installed) → free
HuggingFace Inference API → keyword fallback. Never raises.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

_POSITIVE_KW = [
    "beat", "beats", "raises guidance", "record", "surge", "buyback", "order win",
    "upgrade", "strong", "profit", "expansion", "partnership", "dividend",
]
_NEGATIVE_KW = [
    "miss", "misses", "cut guidance", "fraud", "scam", "sebi", "probe", "raid",
    "default", "downgrade", "loss", "restructuring", "npa", "insolvency", "fire",
]


def _label_from_score(avg: float) -> str:
    if avg > 0.5:
        return "VERY_POSITIVE"
    if avg > 0.15:
        return "POSITIVE"
    if avg < -0.5:
        return "VERY_NEGATIVE"
    if avg < -0.15:
        return "NEGATIVE"
    return "NEUTRAL"


class FinBERTSentiment:
    _pipeline = None
    HF_API = "https://api-inference.huggingface.co/models/ProsusAI/finbert"

    def _get_local_pipeline(self):
        if self._pipeline is None:
            try:
                from transformers import pipeline  # requires torch backend

                self._pipeline = pipeline("sentiment-analysis", model="ProsusAI/finbert")
            except Exception as exc:
                log.debug("FinBERT local load unavailable: %s", exc)
                self._pipeline = False
        return self._pipeline

    def analyze_headlines(self, headlines: list[str], use_api: bool = False) -> dict:
        """Score headlines. Tiers: local FinBERT (if torch) → keyword fallback.
        The slow per-headline HF Inference API tier is opt-in (use_api=True) so the
        live pipeline stays fast on a laptop without torch installed."""
        headlines = [h for h in (headlines or []) if h]
        if not headlines:
            return {"score": 0, "label": "NEUTRAL", "source": "no_data",
                    "positive_count": 0, "negative_count": 0, "neutral_count": 0,
                    "most_positive": "", "most_negative": ""}

        pipe = self._get_local_pipeline()
        if pipe and pipe is not False:
            try:
                results = pipe(headlines[:20])
                return self._aggregate(headlines, results, "local_finbert")
            except Exception as exc:
                log.debug("FinBERT local inference failed: %s", exc)

        if not use_api:
            return self._keyword_fallback(headlines)

        # HF Inference API (opt-in — slower, network).
        try:
            import requests

            scored = []
            for h in headlines[:10]:
                r = requests.post(self.HF_API, json={"inputs": h}, timeout=15)
                if r.status_code == 200:
                    scored.append((h, r.json()))
            if scored:
                return self._aggregate_api(scored, "hf_api")
        except Exception as exc:
            log.debug("FinBERT HF API failed: %s", exc)

        return self._keyword_fallback(headlines)

    def _aggregate(self, headlines, results, source) -> dict:
        pos = neg = neu = 0
        scores: list[float] = []
        most_pos = most_neg = ""
        max_pos = max_neg = 0.0
        for headline, res in zip(headlines, results):
            label = str(res.get("label", "")).lower()
            conf = float(res.get("score", 0))
            if label == "positive":
                pos += 1
                scores.append(conf)
                if conf > max_pos:
                    max_pos, most_pos = conf, headline
            elif label == "negative":
                neg += 1
                scores.append(-conf)
                if conf > max_neg:
                    max_neg, most_neg = conf, headline
            else:
                neu += 1
                scores.append(0.0)
        avg = sum(scores) / len(scores) if scores else 0.0
        return {"score": round(avg, 3), "label": _label_from_score(avg),
                "positive_count": pos, "negative_count": neg, "neutral_count": neu,
                "most_positive": most_pos, "most_negative": most_neg, "source": source}

    def _aggregate_api(self, scored, source) -> dict:
        # HF API returns [[{label, score}, ...]] per input — pick the top label.
        flat_results = []
        headlines = []
        for headline, payload in scored:
            row = payload[0] if isinstance(payload, list) and payload else payload
            if isinstance(row, list):  # nested [[{...}]]
                row = max(row, key=lambda d: d.get("score", 0)) if row else {}
            flat_results.append(row if isinstance(row, dict) else {})
            headlines.append(headline)
        return self._aggregate(headlines, flat_results, source)

    def _keyword_fallback(self, headlines) -> dict:
        pos = neg = neu = 0
        scores: list[float] = []
        most_pos = most_neg = ""
        for h in headlines:
            text = h.lower()
            p = sum(1 for kw in _POSITIVE_KW if kw in text)
            n = sum(1 for kw in _NEGATIVE_KW if kw in text)
            if p > n:
                pos += 1
                scores.append(1.0)
                most_pos = most_pos or h
            elif n > p:
                neg += 1
                scores.append(-1.0)
                most_neg = most_neg or h
            else:
                neu += 1
                scores.append(0.0)
        avg = sum(scores) / len(scores) if scores else 0.0
        return {"score": round(avg, 3), "label": _label_from_score(avg),
                "positive_count": pos, "negative_count": neg, "neutral_count": neu,
                "most_positive": most_pos, "most_negative": most_neg,
                "source": "keyword_fallback"}
