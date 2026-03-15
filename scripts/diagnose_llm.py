#!/usr/bin/env python3
"""
NAVIGUIDE — Diagnostic LLM (Nova → Claude Bedrock → Anthropic API)

Identifie quel maillon de la chaîne fonctionne ou échoue.
Usage: cd naviguide_workspace && python3 ../scripts/diagnose_llm.py
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WS = ROOT / "naviguide_workspace"
if str(WS) not in sys.path:
    sys.path.insert(0, str(WS))

from dotenv import load_dotenv
load_dotenv(WS / ".env")

def check_env():
    """Vérifie les variables d'environnement (sans afficher les valeurs)."""
    has_iam = bool(os.getenv("AWS_ACCESS_KEY_ID") and os.getenv("AWS_SECRET_ACCESS_KEY"))
    has_bedrock = bool(os.getenv("AWS_BEARER_TOKEN_BEDROCK"))
    has_anthropic = bool(os.getenv("ANTHROPIC_API_KEY", "").strip())
    return {
        "AWS IAM": has_iam,
        "AWS_BEARER_TOKEN_BEDROCK": has_bedrock,
        "ANTHROPIC_API_KEY": "***" if has_anthropic else None,
    }

def test_nova():
    """Test Nova 2 Lite (Bedrock)."""
    try:
        import boto3
        client = boto3.client("bedrock-runtime", region_name="us-east-1")
        r = client.converse(
            modelId="us.amazon.nova-2-lite-v1:0",
            messages=[{"role": "user", "content": [{"text": "Say hi"}]}],
        )
        text = r["output"]["message"]["content"][0]["text"]
        return ("OK", text[:50] if text else "")
    except Exception as e:
        return ("FAIL", str(e))

def test_claude_bedrock():
    """Test Claude via Bedrock."""
    try:
        from langchain_aws import ChatBedrock
        from langchain_core.messages import HumanMessage
        llm = ChatBedrock(model_id="us.anthropic.claude-3-5-sonnet-20241022-v2:0", region_name="us-east-1")
        msg = llm.invoke([HumanMessage(content="Say hi")])
        text = msg.content if hasattr(msg, "content") else str(msg)
        return ("OK", text[:50] if text else "")
    except Exception as e:
        return ("FAIL", str(e))

def test_anthropic_api():
    """Test Claude via API Anthropic directe."""
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return ("SKIP", "ANTHROPIC_API_KEY non défini")
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=64,
            messages=[{"role": "user", "content": "Say hi in 3 words"}],
        )
        text = msg.content[0].text if msg.content else ""
        return ("OK", text[:50] if text else "")
    except Exception as e:
        return ("FAIL", str(e))

def main():
    print("=" * 60)
    print("NAVIGUIDE — Diagnostic LLM")
    print("=" * 60)

    env = check_env()
    print("\n1. Variables d'environnement:")
    for k, v in env.items():
        print(f"   {k}: {'✓' if v else '✗'}")

    results = []
    print("\n2. Nova 2 Lite (Bedrock):")
    r = test_nova()
    results.append(r)
    print(f"   {r[0]}: {r[1][:80]}")

    print("\n3. Claude Bedrock:")
    r = test_claude_bedrock()
    results.append(r)
    print(f"   {r[0]}: {r[1][:80]}")

    print("\n4. Claude API Anthropic (fallback):")
    r = test_anthropic_api()
    results.append(r)
    print(f"   {r[0]}: {r[1][:80]}")

    print("\n" + "=" * 60)
    any_ok = any(r[0] == "OK" for r in results)
    if any_ok:
        print("→ Au moins un provider fonctionne. Le chat devrait répondre.")
    else:
        print("→ Aucun provider OK. Vérifier SETUP_NOVA_CREDITS.md et .env")
    print("=" * 60)
    sys.exit(0 if any_ok else 1)
    if any_ok:
        print("→ Au moins un provider fonctionne. Le chat devrait répondre.")
    else:
        print("→ Aucun provider OK. Vérifier SETUP_NOVA_CREDITS.md et .env")
    print("=" * 60)

if __name__ == "__main__":
    main()
