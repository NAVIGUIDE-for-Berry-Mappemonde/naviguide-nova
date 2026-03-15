"""
NAVIGUIDE — LLM invoker for Hackathon Amazon Nova AI

Uses Nova 2 Lite (amazon.nova-2-lite-v1:0) as primary model.
Falls back to Claude 3.5 Sonnet if Nova fails.

Credentials: load from naviguide_workspace/.env
  - AWS_BEARER_TOKEN_BEDROCK (Bedrock API key)
  - or AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY (IAM)
"""

import asyncio
import logging
from typing import AsyncIterator, Optional

log = logging.getLogger("naviguide.llm")

NOVA_MODEL = "us.amazon.nova-2-lite-v1:0"  # US region format
CLAUDE_MODEL = "us.anthropic.claude-3-5-sonnet-20241022-v2:0"
REGION = "us-east-1"


def invoke_llm(
    prompt: str,
    system: str = "",
    fallback_msg: str = "LLM unavailable.",
) -> Optional[str]:
    """
    Invoke LLM with prompt. Tries Nova 2 Lite first, then Claude 3.5 Sonnet.
    Returns text or None on failure (caller should use fallback_msg).
    """
    full_prompt = f"{system}\n\n{prompt}" if system else prompt

    # 1. Try Nova 2 Lite (boto3 converse)
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
        log.warning(f"[llm] Nova failed: {exc} — trying Claude fallback")

    # 2. Fallback: Claude 3.5 Sonnet (langchain ChatBedrock)
    try:
        from langchain_aws import ChatBedrock
        from langchain_core.messages import HumanMessage

        llm = ChatBedrock(model_id=CLAUDE_MODEL, region_name=REGION)
        msg = llm.invoke([HumanMessage(content=full_prompt)])
        text = msg.content if hasattr(msg, "content") else str(msg)
        if text and str(text).strip():
            log.info(f"[llm] Claude fallback OK ({len(text)} chars)")
            return str(text).strip()
    except Exception as exc:
        log.warning(f"[llm] Claude fallback failed: {exc}")

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
