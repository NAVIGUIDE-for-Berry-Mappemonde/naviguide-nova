"""
NAVIGUIDE Simulation Agents — Anthropic Claude LLM Client

Shared client for LLM calls via the Anthropic API (claude-opus-4-5 by default).
Degrades gracefully when ANTHROPIC_API_KEY is not configured.

Provides two calling modes:
  - call_llm()   : synchronous, non-streaming (used by LangGraph agent nodes)
  - stream_llm() : async generator, token-by-token streaming (used by FastAPI
                   SSE endpoints to push data: {"token": "..."} events)
"""

import os
from typing import AsyncIterator, Tuple
from dotenv import load_dotenv

load_dotenv()

# Model to use — overridable via env var
_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-5")

# Lazy-initialised sync Anthropic client (used by agent LangGraph nodes)
_client = None

# Lazy-initialised async Anthropic client (used for SSE token streaming)
_async_client = None


def _get_client():
    """Return a cached sync Anthropic client, or None if SDK / key is unavailable."""
    global _client
    if _client is not None:
        return _client
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        from anthropic import Anthropic
        _client = Anthropic(api_key=api_key)
        return _client
    except Exception:
        return None


def _get_async_client():
    """Return a cached async Anthropic client, or None if SDK / key is unavailable."""
    global _async_client
    if _async_client is not None:
        return _async_client
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        from anthropic import AsyncAnthropic
        _async_client = AsyncAnthropic(api_key=api_key)
        return _async_client
    except Exception:
        return None


def call_llm(prompt: str, system: str = "") -> Tuple[str, str]:
    """
    Send a prompt to the Anthropic Claude API (non-streaming).
    Used internally by LangGraph agent nodes.

    Args:
        prompt  — user message content
        system  — optional system prompt (defaults to empty)

    Returns:
        (content, data_freshness) where data_freshness is 'training_only'.
    Falls back to ("", "training_only") when the service is unavailable.
    """
    client = _get_client()
    if client is None:
        return "", "training_only"

    try:
        kwargs = {
            "model":      _MODEL,
            "max_tokens": 1024,
            "messages":   [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system

        message = client.messages.create(**kwargs)
        content = message.content[0].text
        return content, "training_only"
    except Exception:
        return "", "training_only"


async def stream_llm(prompt: str, system: str = "") -> AsyncIterator[str]:
    """
    Stream tokens from Anthropic Claude via AsyncAnthropic.messages.stream().
    Async generator — yields individual text tokens as they arrive from the API.

    Used by FastAPI agent endpoints to push SSE data: {"token": "..."} events
    for progressive token-by-token display in the frontend AgentPanel.

    Args:
        prompt  — user message content
        system  — optional system prompt (defaults to empty)

    Yields:
        str — individual text tokens emitted by the model stream.

    Silently returns (yields nothing) if the async client is unavailable or
    if an unrecoverable error occurs during streaming.
    """
    client = _get_async_client()
    if client is None:
        return

    try:
        kwargs = {
            "model":      _MODEL,
            "max_tokens": 1024,
            "messages":   [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system

        async with client.messages.stream(**kwargs) as stream:
            async for token in stream.text_stream:
                yield token
    except Exception:
        return
