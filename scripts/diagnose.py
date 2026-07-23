"""Run this to understand exactly why no real trades pass.

    uv run python scripts/diagnose.py
"""
from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from config.settings import settings  # noqa: E402
from src.agents.fundamental.agent import FundamentalAgent  # noqa: E402
from src.agents.scout.agent import ScoutAgent  # noqa: E402
from src.agents.technical.agent import TechnicalAgent  # noqa: E402
from src.data.regime_detector import RegimeDetector  # noqa: E402


def main() -> int:
    regime = RegimeDetector().detect()
    print(f"Regime: {regime['regime']}, RSI: {regime.get('rsi', 0):.1f}")
    print(f"Demo mode: {settings.effective_demo_mode}")
    print()

    candidates = ScoutAgent().scan()[:3]
    if not candidates:
        print("Scout returned 0 candidates — nothing passed the liquidity/scoring gate.")
        return 0

    for c in candidates:
        print(f"=== {c['symbol']} (score {c['score']}) ===")

        state = {"symbol": c["symbol"], "candidates": [c], "market_context": regime, "errors": []}

        fa = FundamentalAgent()
        fund = fa.analyze(state)
        print(f"  Fundamental: {fund.get('score', 0):.2f}, "
              f"hard_rejected={fund.get('hard_rejected', False)}")
        if fund.get("hard_rejected"):
            print(f"  Rejection reason: {fund.get('reasoning', '')[:80]}")

        if not fund.get("hard_rejected"):
            state["fundamental_verdict"] = fund
            ta = TechnicalAgent()
            tech = ta.analyze(state)
            print(f"  Technical: {tech.get('score', 0):.2f}")
            print(f"  Signal: {tech.get('signal', '?')}")

        print()

    print("If everything is being hard-rejected by fundamental:")
    print("Check if screener.in data is loading (network issue?)")
    print('Run: python -c "from src.data.screener import ScreenerScraper; '
          'print(ScreenerScraper().get_company_data(\'HDFCBANK\'))"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
