"""FinBERT sentiment — keyword fallback path (no torch in test env)."""
from __future__ import annotations

from src.sentiment.finbert import FinBERTSentiment


def test_keyword_fallback_positive():
    out = FinBERTSentiment().analyze_headlines(["Company beats estimates, raises guidance"])
    assert out["score"] > 0
    assert out["label"] in ("POSITIVE", "VERY_POSITIVE")


def test_keyword_fallback_negative():
    out = FinBERTSentiment().analyze_headlines(["SEBI probe finds fraud at the company"])
    assert out["score"] < 0
    assert out["label"] in ("NEGATIVE", "VERY_NEGATIVE")


def test_empty_headlines_neutral():
    out = FinBERTSentiment().analyze_headlines([])
    assert out["label"] == "NEUTRAL"
    assert out["score"] == 0
