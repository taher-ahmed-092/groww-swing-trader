# Cosmic Punk Trading System — Lessons Learned
*Auto-generated: 28 Jul 2026 23:31 IST*
*Share this file with Claude for analysis and improvement.*

---

## System Performance Summary
- Real pipeline trades: 0
- Win rate: 0.0%
- Total P&L: ₹+0.00
- Forced learning trades: 542

## Market Regime Observations

### ANY (1 wins, 4 losses)
- ❌ [12%] FORCED OUTCOME: mid signal (0.50) → LOSS (-3.3%)
- ❌ [7%] FORCED OUTCOME: high signal (0.60) → LOSS (-0.6%)
- ✅ [6%] FORCED OUTCOME: high signal (0.80) → WIN (+4.8%)
- ❌ [4%] FORCED OUTCOME: mid signal (0.55) → LOSS (-2.1%)
- ❌ [2%] FORCED OUTCOME: low signal (0.15) → LOSS (-2.9%)

### DOWNTREND (8 wins, 3 losses)
- ✅ [90%] CONT-SIM WIN: small | RSI mid | DOWNTREND | ADX TRENDING | score 0.55
- ✅ [85%] CONT-SIM WIN: mid | RSI mid | DOWNTREND | ADX TRENDING | score 0.40
- ✅ [70%] REPLAY ✅ WON: ONGC (large) | RSI 51 | DOWNTREND | ADX TRENDING | Score 0.50
- ✅ [57%] REPLAY ✅ WON: HCLTECH (large) | RSI 51 | DOWNTREND | ADX NEUTRAL | Score 0.35
- ❌ [53%] CONT-SIM LOSS: large | RSI mid | DOWNTREND | ADX NEUTRAL | score 0.35

### RECOVERY (0 wins, 0 losses)
- ◆ [8%] TITAN BUY signal worked in RECOVERY (RSI 39.9, SIDEWAYS)
- ◆ [6%] BAJAJ-AUTO BUY failed in RECOVERY (RSI 23.7, SIDEWAYS) — avoid similar setups

### SIDEWAYS (1 wins, 0 losses)
- ✅ [8%] REPLAY ✅ WON: UFLEX (small) | RSI 57 | SIDEWAYS | ADX TRENDING | Score 0.45

### UPTREND (27 wins, 27 losses)
- ❌ [90%] CONT-SIM LOSS: large | RSI mid | UPTREND | ADX CHOPPY | score 0.70
- ❌ [90%] CONT-SIM LOSS: mid | RSI mid | UPTREND | ADX TRENDING | score 0.85
- ❌ [90%] CONT-SIM LOSS: mid | RSI high | UPTREND | ADX NEUTRAL | score 0.50
- ✅ [90%] CONT-SIM WIN: mid | RSI high | UPTREND | ADX TRENDING | score 1.00
- ✅ [90%] CONT-SIM WIN: small | RSI mid | UPTREND | ADX CHOPPY | score 0.70

## What Works (High Confidence Patterns)
- [90%] CONT-SIM WIN: mid | RSI high | UPTREND | ADX TRENDING | score 1.00
- [90%] CONT-SIM WIN: small | RSI mid | UPTREND | ADX CHOPPY | score 0.70
- [90%] CONT-SIM WIN: small | RSI high | UPTREND | ADX CHOPPY | score 0.70
- [90%] CONT-SIM WIN: small | RSI mid | DOWNTREND | ADX TRENDING | score 0.55
- [85%] CONT-SIM WIN: mid | RSI mid | UPTREND | ADX CHOPPY | score 0.70
- [85%] CONT-SIM WIN: mid | RSI mid | DOWNTREND | ADX TRENDING | score 0.40
- [85%] CONT-SIM WIN: small | RSI mid | UPTREND | ADX NEUTRAL | score 0.65
- [85%] CONT-SIM WIN: small | RSI high | UPTREND | ADX NEUTRAL | score 0.80
- [80%] CONT-SIM WIN: mid | RSI high | UPTREND | ADX CHOPPY | score 0.70
- [80%] CONT-SIM WIN: small | RSI mid | UPTREND | ADX TRENDING | score 0.85

## What Doesn't Work (Loss Patterns to Avoid)
- [90%] CONT-SIM LOSS: large | RSI mid | UPTREND | ADX CHOPPY | score 0.70
- [90%] CONT-SIM LOSS: mid | RSI mid | UPTREND | ADX TRENDING | score 0.85
- [90%] CONT-SIM LOSS: mid | RSI high | UPTREND | ADX NEUTRAL | score 0.50
- [90%] CONT-SIM LOSS: small | RSI high | UPTREND | ADX TRENDING | score 0.85
- [82%] REPLAY ❌ LOST: BIOCON (mid) | RSI 56 | UPTREND | ADX TRENDING | Score 0.80
- [73%] REPLAY ❌ LOST: WIPRO (large) | RSI 55 | UPTREND | ADX NEUTRAL | Score 0.40
- [71%] REPLAY ❌ LOST: WIPRO (large) | RSI 61 | UPTREND | ADX NEUTRAL | Score 0.40
- [70%] REPLAY ❌ LOST: ABCAPITAL (mid) | RSI 51 | UPTREND | ADX CHOPPY | Score 0.70
- [69%] REPLAY ❌ LOST: AARTIIND (small) | RSI 61 | UPTREND | ADX TRENDING | Score 1.05

## Stocks Analyzed
- Replay coverage: 241/244 stocks
- Most replayed: RELIANCE, TCS, HDFCBANK, ICICIBANK, INFY, HINDUNILVR, ITC, SBIN, BHARTIARTL, BAJFINANCE

## Forced Trade Outcomes by Type
- HISTORICAL_SIM: 141W/262L (35% win rate)
- LIVE_FORCED: 73W/66L (53% win rate)

## Recommendations for Improvement
*(Based on current data — share this section with Claude)*

1. No real pipeline trades yet. Run the system daily during market hours (9:15-15:30 IST) and use /scan to find opportunities.
2. Forced trade win rate is 39%. Market conditions are unfavorable. Consider switching to ROGUE mode to capture more diverse signals.
3. Consistent loss pattern detected: CONT-SIM LOSS: large | RSI mid | UPTREND | ADX CHOPPY | scor. Consider adding this as an auto-veto rule.
4. Strong win pattern: CONT-SIM WIN: mid | RSI high | UPTREND | ADX TRENDING | scor. Consider boosting scout score for this setup.

## Recently Drifted Patterns (Do Not Trust)
- No recently drifted patterns

---
*This file is auto-updated after every learning cycle.*
*Run `/report` in Telegram for a live summary.*