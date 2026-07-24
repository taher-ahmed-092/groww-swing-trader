"""Utilities for keeping LangGraph state msgpack-serializable."""
from __future__ import annotations

import numpy as np


def sanitize_for_state(obj):
    """Recursively converts numpy types to native Python types.

    LangGraph's MemorySaver checkpointer serializes state via msgpack, which
    has no encoder for numpy scalars/arrays — any numpy.float64/int64/bool_/
    ndarray reaching the state dict crashes the pipeline mid-execution.
    Call this on any dict/list before it's returned into state.
    """
    if isinstance(obj, dict):
        return {k: sanitize_for_state(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [sanitize_for_state(v) for v in obj]
    elif isinstance(obj, np.integer):
        return int(obj)
    elif isinstance(obj, np.floating):
        return float(obj)
    elif isinstance(obj, np.bool_):
        return bool(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    return obj
