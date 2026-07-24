# Cosmic Punk Trading System — Lessons Learned
*Auto-generated: 24 Jul 2026 23:31 IST*
*Share this file with Claude for analysis and improvement.*

---

## System Performance Summary
- Real pipeline trades: 0
- Win rate: 0.0%
- Total P&L: ₹+0.00
- Forced learning trades: 233

## Market Regime Observations

### ANY (1 wins, 4 losses)
- ❌ [78%] FORCED OUTCOME: mid signal (0.50) → LOSS (-3.3%)
- ✅ [68%] FORCED OUTCOME: high signal (0.80) → WIN (+4.8%)
- ❌ [20%] FORCED OUTCOME: high signal (0.60) → LOSS (-0.6%)
- ❌ [4%] FORCED OUTCOME: mid signal (0.55) → LOSS (-2.1%)
- ❌ [0%] FORCED OUTCOME: low signal (0.15) → LOSS (-2.9%)

### DOWNTREND (5 wins, 1 losses)
- ✅ [58%] REPLAY ✅ WON: ONGC (large) | RSI 51 | DOWNTREND | ADX TRENDING | Score 0.50
- ✅ [42%] REPLAY ✅ WON: HCLTECH (large) | RSI 51 | DOWNTREND | ADX NEUTRAL | Score 0.35
- ✅ [12%] CONT-SIM WIN: large | RSI mid | DOWNTREND | ADX TRENDING | score 0.55
- ❌ [10%] CONT-SIM LOSS: large | RSI mid | DOWNTREND | ADX NEUTRAL | score 0.35
- ✅ [8%] REPLAY ✅ WON: PIIND (mid) | RSI 50 | DOWNTREND | ADX TRENDING | Score 0.40

### RECOVERY (0 wins, 0 losses)
- ◆ [8%] TITAN BUY signal worked in RECOVERY (RSI 39.9, SIDEWAYS)
- ◆ [6%] BAJAJ-AUTO BUY failed in RECOVERY (RSI 23.7, SIDEWAYS) — avoid similar setups

### UPTREND (10 wins, 19 losses)
- ❌ [100%] REPLAY ❌ LOST: WIPRO (large) | RSI 61 | UPTREND | ADX NEUTRAL | Score 0.40
- ❌ [100%] REPLAY ❌ LOST: ABCAPITAL (mid) | RSI 51 | UPTREND | ADX CHOPPY | Score 0.70
- ❌ [95%] REPLAY ❌ LOST: BHARTIARTL (large) | RSI 49 | UPTREND | ADX CHOPPY | Score 0.45
- ❌ [95%] REPLAY ❌ LOST: WIPRO (large) | RSI 55 | UPTREND | ADX NEUTRAL | Score 0.40
- ✅ [95%] REPLAY ✅ WON: ASTERDM (small) | RSI 63 | UPTREND | ADX NEUTRAL | Score 0.80

## What Works (High Confidence Patterns)
- [95%] REPLAY ✅ WON: ASTERDM (small) | RSI 63 | UPTREND | ADX NEUTRAL | Score 0.80
- [92%] REPLAY ✅ WON: ABSLAMC (small) | RSI 51 | UPTREND | ADX NEUTRAL | Score 0.70
- [90%] REPLAY ✅ WON: AJANTPHARM (small) | RSI 55 | UPTREND | ADX CHOPPY | Score 0.70
- [85%] REPLAY ✅ WON: BANDHANBNK (mid) | RSI 56 | UPTREND | ADX NEUTRAL | Score 0.80
- [80%] REPLAY ✅ WON: ABSLAMC (small) | RSI 53 | UPTREND | ADX TRENDING | Score 0.95

## What Doesn't Work (Loss Patterns to Avoid)
- [100%] REPLAY ❌ LOST: WIPRO (large) | RSI 61 | UPTREND | ADX NEUTRAL | Score 0.40
- [100%] REPLAY ❌ LOST: ABCAPITAL (mid) | RSI 51 | UPTREND | ADX CHOPPY | Score 0.70
- [95%] REPLAY ❌ LOST: BHARTIARTL (large) | RSI 49 | UPTREND | ADX CHOPPY | Score 0.45
- [95%] REPLAY ❌ LOST: WIPRO (large) | RSI 55 | UPTREND | ADX NEUTRAL | Score 0.40
- [90%] REPLAY ❌ LOST: AARTIIND (small) | RSI 61 | UPTREND | ADX TRENDING | Score 1.05
- [85%] REPLAY ❌ LOST: LT (large) | RSI 61 | UPTREND | ADX CHOPPY | Score 0.80
- [78%] FORCED OUTCOME: mid signal (0.50) → LOSS (-3.3%)
- [75%] REPLAY ❌ LOST: GRASIM (large) | RSI 65 | UPTREND | ADX TRENDING | Score 0.55
- [75%] REPLAY ❌ LOST: BIOCON (mid) | RSI 56 | UPTREND | ADX TRENDING | Score 0.80

## Stocks Analyzed
- Replay coverage: 244/244 stocks
- Most replayed: RELIANCE, TCS, HDFCBANK, ICICIBANK, INFY, HINDUNILVR, ITC, SBIN, BHARTIARTL, BAJFINANCE

## Forced Trade Outcomes by Type
- HISTORICAL_SIM: 35W/63L (36% win rate)
- LIVE_FORCED: 72W/63L (53% win rate)

## Recommendations for Improvement
*(Based on current data — share this section with Claude)*

1. No real pipeline trades yet. Run the system daily during market hours (9:15-15:30 IST) and use /scan to find opportunities.
2. Consistent loss pattern detected: REPLAY ❌ LOST: WIPRO (large) | RSI 61 | UPTREND | ADX NEUTRA. Consider adding this as an auto-veto rule.
3. Strong win pattern: REPLAY ✅ WON: ASTERDM (small) | RSI 63 | UPTREND | ADX NEUTR. Consider boosting scout score for this setup.

---
*This file is auto-updated after every learning cycle.*
*Run `/report` in Telegram for a live summary.*