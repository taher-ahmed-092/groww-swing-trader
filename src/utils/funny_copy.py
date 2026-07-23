"""
Generates playful, funny copy for the dashboard based on real market conditions.
Humor grounded in real data, not random — the joke picks are randomized within
a regime, but the regime bucket itself always comes from real RegimeDetector output.
"""
from __future__ import annotations

import random

REGIME_JOKES = {
    "BULL_TRENDING": [
        "Nifty's doing cartwheels today 🤸",
        "The market woke up and chose violence (in a good way) 📈",
        "Bulls are throwing a party and everyone's invited 🎉",
    ],
    "BEAR_TRENDING": [
        "The market's having a Monday, and it's not even Monday 😩",
        "Bears are doing their thing. We're just watching from the treehouse 🐻",
        "Nifty took the stairs down. All of them. At once. 🪜",
    ],
    "VOLATILE": [
        "The market had too much coffee today ☕⚡",
        "Nifty is vibrating like a phone on silent 📳",
        "Things are jumpy. We're wearing our seatbelts 🎢",
    ],
    "RANGE_BOUND": [
        "Nifty is just... pacing. Like it's waiting for a bus 🚌",
        "The market is doing donuts in a parking lot 🍩",
    ],
    "RECOVERY": [
        "Nifty is stretching after a nap 🧘",
        "Slow and steady, like a turtle with somewhere to be 🐢",
    ],
    "TRANSITIONAL": [
        "The market can't decide what it wants for lunch 🍽️",
        "Nifty is standing at a crossroads, checking its phone 📱",
    ],
}


def get_regime_joke(regime: str) -> str:
    try:
        jokes = REGIME_JOKES.get(regime, ["The market is being mysterious today 🔮"])
    except TypeError:  # unhashable regime value — never let a bad input crash the dashboard
        jokes = ["The market is being mysterious today 🔮"]
    return random.choice(jokes)
