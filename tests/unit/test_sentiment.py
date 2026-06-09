"""Social sentiment scoring behavior (keyword path — no network, no API key)."""
from __future__ import annotations

from src.agents.sentiment.agent import SocialSentimentAgent


def test_keyword_scoring_negative():
    agent = SocialSentimentAgent()
    score, red_flags, _ = agent._score_sentiment([{"text": "Company gets SEBI notice today"}])
    assert score < 0
    assert any("sebi" in f.lower() for f in red_flags)


def test_keyword_scoring_positive():
    agent = SocialSentimentAgent()
    score, _, positive = agent._score_sentiment([{"text": "Board buyback announced for shareholders"}])
    assert score > 0
    assert any("buyback" in p.lower() for p in positive)


def test_empty_source_returns_neutral(monkeypatch):
    agent = SocialSentimentAgent()
    # Force every collector to return no items.
    for name in ("_get_yahoo_news", "_get_google_news_rss", "_get_nse_announcements",
                 "_get_bse_announcements", "_get_reddit_sentiment", "_get_stocktwits"):
        monkeypatch.setattr(agent, name, lambda *a, **k: [])
    result = agent.analyze("NOSUCH", "No Such Co")
    assert result["sentiment_score"] == 0.0
    assert result["sentiment_label"] == "NEUTRAL"


def test_red_flag_extraction():
    agent = SocialSentimentAgent()
    _, red_flags, _ = agent._score_sentiment([{"text": "Reports say promoter sold shares last week"}])
    assert any("promoter sold" in f.lower() for f in red_flags)
