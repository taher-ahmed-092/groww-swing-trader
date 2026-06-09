"""Punchy trading tips — surfaced in CLI banners and Telegram trade cards."""
from __future__ import annotations

import random

TECHNICAL_TIPS = [
    "📈 The trend is your friend — until the bend at the end.",
    "🔧 RSI 50-65 is the momentum sweet spot; above 70 you're chasing.",
    "📊 Volume confirms price. A breakout without volume is a fakeout.",
    "🪜 ADX > 25 means a real trend; below 20 is chop — sit on your hands.",
    "🕯️ One candle is noise. Wait for the close, not the wick.",
    "📉 Price below MA200 in a downtrend? Don't catch the falling knife.",
    "🎯 Let the chart pick your stop (ATR), not your hope.",
    "🔁 OBV rising while price stalls = accumulation. Pay attention.",
    "⏳ Best entries come to the patient — wait for the pullback to MA50.",
    "🚫 Never average down a losing trade. Add to winners, not losers.",
]

FUNDAMENTAL_TIPS = [
    "🏰 A wide moat beats a cheap price. Quality compounds.",
    "💰 ROCE > 15% consistently = a business that prints cash.",
    "⚠️ Promoter pledging > 30% is a red flag, not a footnote.",
    "📚 Read the cash flow statement; earnings can be massaged, cash can't.",
    "📈 Sales AND profit growing for 3 years beats a one-quarter pop.",
    "🧾 Low debt-to-equity survives the storms that sink leveraged peers.",
    "🤝 High promoter holding = skin in the game. Alignment matters.",
    "🔍 A low P/E can be a value trap. Ask WHY it's cheap.",
    "🧠 Would Buffett hold this for 3 years? If not, why hold it for 3 days?",
    "💵 Free cash flow funds dividends, buybacks, and survival. Watch it.",
]

RISK_TIPS = [
    "🛑 Every trade needs a stop. No stop = no trade. No exceptions.",
    "📉 Risk 1-3% per trade. Survive the losses to enjoy the wins.",
    "⚖️ Position size IS your risk control. Size down when unsure.",
    "🎯 Aim for 1:2 reward:risk minimum. Bad R:R is a slow bleed.",
    "🔒 Move your stop to breakeven once you're up — protect the principal.",
    "🪤 Don't fight the index. A downtrending Nifty drowns most longs.",
    "🧊 Cool down after 3 losses in a row. The market isn't going anywhere.",
    "💸 Never risk money you can't afford to lose. Paper trade first.",
    "📐 Half-Kelly sizing keeps you in the game through variance.",
    "🚪 Plan your exit before your entry. Know where you're wrong.",
]

SELF_LEARNING_TIPS = [
    "📔 Journal every trade. The market's best teacher is your own history.",
    "🔬 Run a post-mortem on every loss — the lesson is the real profit.",
    "🧠 Patterns confirmed across many trades graduate from luck to edge.",
    "📊 Track if your confidence matches your win rate. Calibration is king.",
    "🔁 The same mistake twice is a choice, not bad luck.",
    "🌱 After 20 trades you start to see what works for YOUR watchlist.",
    "⚙️ Let the data tune your parameters — don't marry a number.",
    "🧭 A losing strategy followed perfectly still loses. Re-evaluate.",
    "💡 Write down WHY you entered. Review it WHEN you exit.",
    "📈 Edge comes from process, not predictions. Refine the process.",
]

TRADING_TIPS = TECHNICAL_TIPS + FUNDAMENTAL_TIPS + RISK_TIPS + SELF_LEARNING_TIPS


def get_random_tip() -> str:
    return random.choice(TRADING_TIPS)


def get_contextual_tip(
    signal: str | None = None,
    nifty_trend: str | None = None,
    flags: list[str] | None = None,
) -> str:
    """Pick a tip relevant to the current setup."""
    flags = flags or []
    flag_str = " ".join(flags).upper()

    if nifty_trend == "DOWNTREND":
        return "🪤 Don't fight the index. A downtrending Nifty drowns most longs."
    if "CHOPPY_MARKET" in flag_str:
        return "🪜 ADX < 20 means a real trend is absent; below 20 is chop — sit on your hands."
    if "POOR_RISK_REWARD" in flag_str:
        return "🎯 Aim for 1:2 reward:risk minimum. Bad R:R is a slow bleed."
    if "MANUALLY_REQUESTED" in flag_str:
        return "🧠 Would Buffett hold this for 3 years? If not, why hold it for 3 days?"
    if "WEEKLY_TREND_CONFLICT" in flag_str:
        return "📈 The trend is your friend — until the bend at the end."
    if signal == "BUY":
        return random.choice(TECHNICAL_TIPS)
    if signal == "SKIP":
        return random.choice(RISK_TIPS)
    return get_random_tip()
