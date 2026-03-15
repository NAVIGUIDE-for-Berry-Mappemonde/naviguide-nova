"""
NAVIGUIDE Polar Agent — LangGraph
==================================
State machine:
  parse_question
       ▼
  choose_tool   ── (speed) ──► compute_speed ──► answer
       │         ── (vmg)  ──► compute_vmg   ──► answer
       │         ── (optim)──► compute_optim ──► answer
       └─────────── (chat) ──► llm_fallback  ──► answer
"""

import os
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, TypedDict

from langchain_core.messages import HumanMessage, AIMessage
from langgraph.graph import StateGraph, END

from polar_engine import PolarData

log = logging.getLogger("polar_agent")

# Add naviguide_workspace to path for llm_utils (Nova + Claude)
_WS = Path(__file__).resolve().parents[1] / "naviguide_workspace"
if str(_WS) not in sys.path:
    sys.path.insert(0, str(_WS))
from dotenv import load_dotenv
load_dotenv(_WS / ".env")


def _llm_call(prompt: str) -> str:
    """
    Single-shot LLM call via Nova + Claude (Bedrock).
    Falls back to empty string if credentials are missing or service unreachable.
    """
    try:
        from llm_utils import invoke_llm
        return invoke_llm(prompt, fallback_msg="") or ""
    except Exception as exc:
        log.warning(f"Nova/Claude unavailable: {exc}")
        return ""


# ══════════════════════════════════════════════════════════════════════════════
# Agent State
# ══════════════════════════════════════════════════════════════════════════════

class PolarAgentState(TypedDict):
    polar:    Optional[PolarData]
    question: str
    tool:     str           # "speed" | "vmg" | "optim" | "chat"
    params:   Dict[str, Any]
    result:   Dict[str, Any]
    answer:   str
    messages: List


# ══════════════════════════════════════════════════════════════════════════════
# Nodes
# ══════════════════════════════════════════════════════════════════════════════

def parse_question_node(state: PolarAgentState) -> PolarAgentState:
    """Extract TWA/TWS numbers and intent from the question."""
    q = state["question"].lower()

    # Extract numeric values
    numbers = [float(x) for x in re.findall(r"\d+\.?\d*", q)]

    twa = next((n for n in numbers if 0 <= n <= 180), None)
    tws = next((n for n in numbers if n != twa and 0 <= n <= 80), None)

    # Detect intent
    if any(w in q for w in ["optim", "optimal", "meilleur", "best", "vmc", "vmg", "gybe", "virement"]):
        tool = "optim"
    elif any(w in q for w in ["vmg", "vmc", "fond", "cap au vent"]):
        tool = "vmg"
    elif twa is not None and tws is not None:
        tool = "speed"
    else:
        tool = "chat"

    msg = HumanMessage(content=f"[parse] intent={tool}, twa={twa}, tws={tws}")
    return {**state, "tool": tool, "params": {"twa": twa, "tws": tws}, "messages": [msg]}


def compute_speed_node(state: PolarAgentState) -> PolarAgentState:
    """Bilinear interpolation — boat speed at TWA/TWS."""
    polar  = state["polar"]
    params = state["params"]
    twa, tws = params.get("twa"), params.get("tws")

    if polar is None or twa is None or tws is None:
        return {**state, "result": {"error": "Missing polar or parameters"}}

    bs = polar.speed(twa, tws)
    result = {
        "type":  "speed",
        "twa":   twa,
        "tws":   tws,
        "speed": round(bs, 2),
    }
    return {**state, "result": result}


def compute_vmg_node(state: PolarAgentState) -> PolarAgentState:
    """VMG at given TWA/TWS."""
    polar  = state["polar"]
    params = state["params"]
    twa, tws = params.get("twa"), params.get("tws")

    if polar is None or twa is None or tws is None:
        return {**state, "result": {"error": "Missing polar or parameters"}}

    bs  = polar.speed(twa, tws)
    vmg = polar.vmg(twa, tws)
    result = {
        "type":  "vmg",
        "twa":   twa,
        "tws":   tws,
        "speed": round(bs, 2),
        "vmg":   round(vmg, 2),
    }
    return {**state, "result": result}


def compute_optim_node(state: PolarAgentState) -> PolarAgentState:
    """Compute optimal upwind/downwind angles and VMG."""
    polar = state["polar"]
    tws   = state["params"].get("tws")

    if polar is None or tws is None:
        return {**state, "result": {"error": "Missing polar or TWS"}}

    uw_twa, uw_bs, uw_vmg = polar.optimal_upwind(tws)
    dw_twa, dw_bs, dw_vmg = polar.optimal_downwind(tws)
    gybe = polar.optimal_gybe_angle(tws)

    result = {
        "type": "optim",
        "tws":  tws,
        "upwind":   {"twa": uw_twa, "speed": uw_bs, "vmg": uw_vmg},
        "downwind": {"twa": dw_twa, "speed": dw_bs, "vmg": dw_vmg},
        "gybe_angle": gybe,
    }
    return {**state, "result": result}


def llm_fallback_node(state: PolarAgentState) -> PolarAgentState:
    """Ask the LLM to answer the question using polar context."""
    polar  = state["polar"]
    q      = state["question"]

    if polar:
        summary = polar.summary()
        ctx = json.dumps(summary, ensure_ascii=False)
        prompt = f"""Tu es un expert en navigation hauturière et polaires de voilier.

Bateau: {polar.boat_name}
Résumé des performances (TWS → VMG optimal):
{ctx}

Question du navigateur: {q}

Réponds de façon précise et concise en français, en citant des valeurs issues des polaires."""
    else:
        prompt = f"""Tu es un expert en navigation hauturière. Réponds à cette question sur les polaires de voilier: {q}"""

    answer = _llm_call(prompt)
    if not answer:
        answer = "LLM indisponible. Veuillez vérifier la configuration Deploy AI."

    return {**state, "result": {"type": "chat", "llm_answer": answer}}


def generate_answer_node(state: PolarAgentState) -> PolarAgentState:
    """Format the result into a human-readable answer."""
    r = state["result"]

    if "error" in r:
        answer = f"❌ Erreur : {r['error']}"

    elif r.get("type") == "speed":
        answer = (
            f"⛵ **Vitesse interpolée**\n"
            f"• TWA = {r['twa']}° / TWS = {r['tws']} kts\n"
            f"• **Vitesse bateau : {r['speed']} nœuds**"
        )

    elif r.get("type") == "vmg":
        direction = "au vent" if r["twa"] < 90 else "sous le vent"
        answer = (
            f"📐 **VMG ({direction})**\n"
            f"• TWA = {r['twa']}° / TWS = {r['tws']} kts\n"
            f"• Vitesse bateau : {r['speed']} nœuds\n"
            f"• **VMG : {r['vmg']} nœuds**"
        )

    elif r.get("type") == "optim":
        uw = r["upwind"]
        dw = r["downwind"]
        answer = (
            f"🏆 **Angles optimaux par TWS = {r['tws']} kts**\n\n"
            f"**Remontée au vent ↑**\n"
            f"• Angle optimal : {uw['twa']}° — Vitesse : {uw['speed']} kts — VMG : {uw['vmg']} kts\n\n"
            f"**Descente sous le vent ↓**\n"
            f"• Angle optimal : {dw['twa']}° — Vitesse : {dw['speed']} kts — VMG : {dw['vmg']} kts\n\n"
            f"**Angle de gybe : {r['gybe_angle']}°** (total bord à bord)"
        )

    elif r.get("type") == "chat":
        answer = r.get("llm_answer", "Pas de réponse disponible.")

    else:
        answer = "Résultat non reconnu."

    msg = AIMessage(content=answer)
    return {**state, "answer": answer, "messages": [msg]}


# ══════════════════════════════════════════════════════════════════════════════
# Router
# ══════════════════════════════════════════════════════════════════════════════

def route_tool(state: PolarAgentState) -> str:
    return state.get("tool", "chat")


# ══════════════════════════════════════════════════════════════════════════════
# Graph builder
# ══════════════════════════════════════════════════════════════════════════════

def build_polar_agent():
    g = StateGraph(PolarAgentState)

    g.add_node("parse_question",  parse_question_node)
    g.add_node("compute_speed",   compute_speed_node)
    g.add_node("compute_vmg",     compute_vmg_node)
    g.add_node("compute_optim",   compute_optim_node)
    g.add_node("llm_fallback",    llm_fallback_node)
    g.add_node("generate_answer", generate_answer_node)

    g.set_entry_point("parse_question")
    g.add_conditional_edges(
        "parse_question",
        route_tool,
        {
            "speed": "compute_speed",
            "vmg":   "compute_vmg",
            "optim": "compute_optim",
            "chat":  "llm_fallback",
        }
    )
    for node in ("compute_speed", "compute_vmg", "compute_optim", "llm_fallback"):
        g.add_edge(node, "generate_answer")
    g.add_edge("generate_answer", END)

    return g.compile()


_agent = build_polar_agent()


def ask(polar: Optional[PolarData], question: str) -> str:
    """Public entry point — ask anything about the polar."""
    state = {
        "polar":    polar,
        "question": question,
        "tool":     "",
        "params":   {},
        "result":   {},
        "answer":   "",
        "messages": [],
    }
    result = _agent.invoke(state)
    return result["answer"]
