"""Company dossiers with enough trade history automatically populate
stock_vetoes (poor win rate) / stock_boosts (strong win rate) in
AutoRuleExtractor, without any KB pattern involved."""
from __future__ import annotations

from src.memory.auto_rules import AutoRuleExtractor


def _dossier(symbol, wins, losses):
    total = wins + losses
    return {
        "symbol": symbol,
        "aggregates": {
            "total_trades": total, "wins": wins, "losses": losses,
            "win_rate": round(wins / total * 100, 1) if total else 0.0,
        },
    }


def test_low_win_rate_dossier_becomes_stock_veto(monkeypatch):
    dossiers = [_dossier("WIPRO", wins=2, losses=8)]  # 20% WR, 10 trades
    monkeypatch.setattr(
        "src.memory.company_dossier.dossier_store.search", lambda **k: dossiers)
    vetoes, boosts = {}, {}
    AutoRuleExtractor._apply_dossier_rules(vetoes, boosts)
    assert "WIPRO" in vetoes
    assert "2W/10t" in vetoes["WIPRO"]["reason"]
    assert "WIPRO" not in boosts


def test_high_win_rate_dossier_becomes_stock_boost(monkeypatch):
    dossiers = [_dossier("AJANTPHARM", wins=7, losses=1)]  # 87.5% WR, 8 trades
    monkeypatch.setattr(
        "src.memory.company_dossier.dossier_store.search", lambda **k: dossiers)
    vetoes, boosts = {}, {}
    AutoRuleExtractor._apply_dossier_rules(vetoes, boosts)
    assert "AJANTPHARM" in boosts
    assert "AJANTPHARM" not in vetoes


def test_insufficient_trades_ignored(monkeypatch):
    dossiers = [_dossier("NEWSTOCK", wins=1, losses=1)]  # only 2 trades
    monkeypatch.setattr(
        "src.memory.company_dossier.dossier_store.search", lambda **k: dossiers)
    vetoes, boosts = {}, {}
    AutoRuleExtractor._apply_dossier_rules(vetoes, boosts)
    assert vetoes == {} and boosts == {}


def test_neutral_win_rate_neither_vetoed_nor_boosted(monkeypatch):
    dossiers = [_dossier("MIDPACK", wins=4, losses=4)]  # 50% WR, 8 trades
    monkeypatch.setattr(
        "src.memory.company_dossier.dossier_store.search", lambda **k: dossiers)
    vetoes, boosts = {}, {}
    AutoRuleExtractor._apply_dossier_rules(vetoes, boosts)
    assert vetoes == {} and boosts == {}
