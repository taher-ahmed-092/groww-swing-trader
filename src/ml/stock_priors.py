"""
Bayesian win probability per stock.

Beta distribution: prior = Beta(alpha=2, beta=2) (neutral 50%). Each observed
WIN -> alpha += 1. Each observed LOSS -> beta += 1. Result: stocks with more
wins in history get a higher posterior probability. Conservative: requires
5+ observations to meaningfully diverge from neutral.
"""
from __future__ import annotations

import json
from pathlib import Path

PRIORS_FILE = Path("data/cache/stock_priors.json")


class StockPriors:
    PRIOR_ALPHA = 2.0
    PRIOR_BETA = 2.0

    def compute_and_save(self) -> dict:
        """Reads all trade history, computes per-stock Beta params."""
        all_data: dict = {}

        for fname in (
            "data/cache/forced_trades_history.json",
            "data/cache/intraday_sim_history.json",
            "data/cache/short_trades_history.json",
        ):
            p = Path(fname)
            if not p.exists():
                continue
            try:
                for t in json.loads(p.read_text()):
                    sym = t.get("symbol", "")
                    if not sym:
                        continue
                    if sym not in all_data:
                        all_data[sym] = {
                            "alpha": self.PRIOR_ALPHA, "beta": self.PRIOR_BETA,
                            "wins": 0, "total": 0,
                        }
                    if t.get("outcome") == "WIN":
                        all_data[sym]["alpha"] += 1
                        all_data[sym]["wins"] += 1
                    elif t.get("outcome") == "LOSS":
                        all_data[sym]["beta"] += 1
                    all_data[sym]["total"] += 1
            except Exception:
                pass

        for sym, d in all_data.items():
            d["posterior_win_prob"] = round(d["alpha"] / (d["alpha"] + d["beta"]), 3)

        PRIORS_FILE.parent.mkdir(parents=True, exist_ok=True)
        PRIORS_FILE.write_text(json.dumps(all_data, indent=2))
        return all_data

    def get_win_prior(self, symbol: str) -> float:
        """Returns posterior win probability for a stock (0-1)."""
        if not PRIORS_FILE.exists():
            return 0.5
        try:
            data = json.loads(PRIORS_FILE.read_text())
            return data.get(symbol, {}).get("posterior_win_prob", 0.5)
        except Exception:
            return 0.5

    def get_stats(self, symbol: str) -> dict:
        if not PRIORS_FILE.exists():
            return {}
        try:
            data = json.loads(PRIORS_FILE.read_text())
            return data.get(symbol, {})
        except Exception:
            return {}
