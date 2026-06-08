"""
Shared LLM helpers.

The model string lives ONLY in config.settings.llm_model — never hardcode it here
or anywhere else. LLMs interpret and reason; they never compute numbers (CLAUDE.md rule 4).
"""
from __future__ import annotations

import json

from config.settings import settings


def get_llm(temperature: float = 0):
    """Return a configured ChatAnthropic client. Import is local so paper mode
    works even if langchain_anthropic has issues, as callers guard on the key."""
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(
        model=settings.llm_model,
        temperature=temperature,
        api_key=settings.anthropic_api_key,
    )


def parse_json_response(content: str) -> dict:
    """Parse an LLM response into a dict, stripping markdown code fences first.
    Returns {} on failure (callers supply their own fallback)."""
    if not content:
        return {}
    text = content.strip()
    if text.startswith("```"):
        # Drop the opening fence (``` or ```json) and the trailing fence.
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {}
