"""
Shared LLM helpers.

Model strings live ONLY in config.settings (llm_model_default / llm_model_judge) —
never hardcode them here or anywhere else. LLMs interpret and reason; they never
compute numbers (CLAUDE.md rule 4).
"""
from __future__ import annotations

import json

from config.settings import settings


def _build_llm(model: str, temperature: float):
    # Import is local so paper mode works even if langchain_anthropic has issues;
    # callers guard on the API key anyway.
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(
        model=model,
        temperature=temperature,
        api_key=settings.anthropic_api_key,
    )


def get_llm(temperature: float = 0):
    """Default (cheap/fast) model for the research agents."""
    return _build_llm(settings.llm_model_default, temperature)


def get_judge_llm(temperature: float = 0):
    """Stronger model reserved for the capital-protecting judge."""
    return _build_llm(settings.llm_model_judge, temperature)


def make_cached_messages(system_prompt: str, user_content: str) -> list:
    """Cache the (long, repeated) system prompt — ~90% discount on cache reads."""
    return [{
        "role": "user",
        "content": [
            {"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": user_content},
        ],
    }]


def cached_llm_call(system: str, user: str, model: str | None = None,
                    max_tokens: int = 1000) -> str:
    """LLM call with the system prompt prompt-cached. Returns text ('' if no key).

    Logs token usage to the journal for monthly cost tracking. Never raises.
    """
    if not settings.has_anthropic_key:
        return ""
    used_model = model or settings.llm_model_default
    try:
        from anthropic import Anthropic

        client = Anthropic(api_key=settings.anthropic_api_key)
        response = client.messages.create(
            model=used_model, max_tokens=max_tokens,
            messages=make_cached_messages(system, user),
        )
        text = response.content[0].text
        try:
            from src.memory.journal import TradingJournal

            usage = getattr(response, "usage", None)
            TradingJournal().log_api_usage(
                model=used_model,
                input_tokens=getattr(usage, "input_tokens", 0) or 0,
                output_tokens=getattr(usage, "output_tokens", 0) or 0,
                call_type="CACHED", agent_name="",
            )
        except Exception:
            pass
        return text
    except Exception:
        return ""


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
