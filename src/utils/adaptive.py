"""
Adaptive parameters — the bridge between the WorkflowEnhancer and the live agents.

The enhancer writes safe, bounded parameter changes to adaptive_params.json weekly;
every agent reads them here so the system tunes itself without code edits.
"""
from __future__ import annotations

import json
import os

PARAMS_FILE = os.path.join("data", "cache", "adaptive_params.json")

# Each: current default + safe auto-adjustable bounds.
ADJUSTABLE_PARAMS = {
    "scout_rsi_low": {"current": 50, "min": 45, "max": 65, "step": 1},
    "scout_rsi_high": {"current": 65, "min": 55, "max": 75, "step": 1},
    "scout_adx_threshold": {"current": 20, "min": 15, "max": 30, "step": 1},
    "judge_approval_threshold": {"current": 6.0, "min": 5.5, "max": 8.0, "step": 0.5},
    "atr_stop_multiplier": {"current": 2.0, "min": 1.5, "max": 3.0, "step": 0.25},
}

DEFAULT_PARAMS = {k: v["current"] for k, v in ADJUSTABLE_PARAMS.items()}


def get_adaptive_params() -> dict:
    """Load current adaptive parameters, falling back to defaults for any missing key."""
    params = dict(DEFAULT_PARAMS)
    try:
        if os.path.exists(PARAMS_FILE):
            with open(PARAMS_FILE, encoding="utf-8") as f:
                loaded = json.load(f)
            for k, v in loaded.items():
                if k in params and v is not None:
                    params[k] = v
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return params


def save_adaptive_params(params: dict) -> None:
    os.makedirs(os.path.dirname(PARAMS_FILE), exist_ok=True)
    with open(PARAMS_FILE, "w", encoding="utf-8") as f:
        json.dump(params, f, indent=2)
