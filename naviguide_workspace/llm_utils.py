"""
NAVIGUIDE — LLM invoker for Hackathon Amazon Nova AI

Chain:
  1. Nova 2 Lite (Bedrock)
  2. Claude via Anthropic API direct (ANTHROPIC_API_KEY) — prioritaire si Nova bloqué
  3. Claude Bedrock (dernier recours)
  4. None → fallback statique côté caller

Credentials: naviguide_workspace/.env
  - AWS_* ou AWS_BEARER_TOKEN_BEDROCK pour Bedrock
  - ANTHROPIC_API_KEY pour fallback direct (clé API Anthropic)
"""

import asyncio
import logging
import os
from typing import AsyncIterator, Optional

log = logging.getLogger("naviguide.llm")

NOVA_MODEL = "us.amazon.nova-2-lite-v1:0"  # US region format
CLAUDE_BEDROCK_MODEL = "us.anthropic.claude-3-5-sonnet-20241022-v2:0"
CLAUDE_ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
REGION = "us-east-1"


def invoke_llm(
    prompt: str,
    system: str = "",
    fallback_msg: str = "LLM unavailable.",
) -> Optional[str]:
    """
    Invoke LLM with prompt. Tries Nova → Claude (Bedrock) → Claude (API Anthropic).
    Returns text or None on failure (caller should use fallback_msg).
    """
    full_prompt = f"{system}\n\n{prompt}" if system else prompt

    # 1. Nova 2 Lite (Bedrock)
    try:
        import boto3
        client = boto3.client("bedrock-runtime", region_name=REGION)
        response = client.converse(
            modelId=NOVA_MODEL,
            messages=[{"role": "user", "content": [{"text": full_prompt}]}],
        )
        text = response["output"]["message"]["content"][0]["text"]
        if text and text.strip():
            log.info(f"[llm] Nova 2 Lite OK ({len(text)} chars)")
            return text.strip()
    except Exception as exc:
        log.warning(f"[llm] Nova failed: {exc} — trying Anthropic API")

    # 2. Claude via API Anthropic directe (clé API) — prioritaire car Bedrock peut crasher
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if api_key:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            kwargs = {"model": CLAUDE_ANTHROPIC_MODEL, "max_tokens": 2048, "messages": [{"role": "user", "content": prompt}]}
            if system:
                kwargs["system"] = system
            message = client.messages.create(**kwargs)
            text = message.content[0].text if message.content else ""
            if text and text.strip():
                log.info(f"[llm] Claude Anthropic API OK ({len(text)} chars)")
                return text.strip()
        except Exception as exc:
            log.warning(f"[llm] Claude Anthropic API failed: {exc}")
    else:
        log.debug("[llm] ANTHROPIC_API_KEY not set — skipping direct API fallback")

    # 3. Claude (Bedrock) — dernier recours (peut crasher sur certains environnements)
    try:
        from langchain_aws import ChatBedrock
        from langchain_core.messages import HumanMessage

        llm = ChatBedrock(model_id=CLAUDE_BEDROCK_MODEL, region_name=REGION)
        msg = llm.invoke([HumanMessage(content=full_prompt)])
        text = msg.content if hasattr(msg, "content") else str(msg)
        if text and str(text).strip():
            log.info(f"[llm] Claude Bedrock OK ({len(text)} chars)")
            return str(text).strip()
    except Exception as exc:
        log.warning(f"[llm] Claude Bedrock failed: {exc}")

    return None


async def stream_llm(
    prompt: str,
    system: str = "",
) -> AsyncIterator[str]:
    """
    Async generator — yields tokens for SSE streaming.
    Uses invoke_llm (Nova + Claude fallback) then yields word-by-word.
    """
    full_prompt = f"{system}\n\n{prompt}" if system else prompt
    text = await asyncio.to_thread(invoke_llm, full_prompt, system="", fallback_msg="")
    if text:
        for word in text.split():
            yield word + " "
