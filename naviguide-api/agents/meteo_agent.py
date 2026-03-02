"""
NAVIGUIDE Simulation Agent — Meteo (Weather & Routing Windows)

LangGraph StateGraph — Pipeline:
  prepare_context → fetch_stormglass → llm_generate → END

Domain: Departure windows, wind regimes (trades/ITCZ/monsoon),
        cyclone seasons, sea state forecasts, optimal routing timing.
Sources: StormGlass API (optional, degrades gracefully), LLM training data.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import List, Optional

import httpx
from langgraph.graph import StateGraph, END
from langchain_core.messages import HumanMessage, AIMessage
from typing_extensions import TypedDict

from .deploy_ai import call_llm

_STORMGLASS_BASE = "https://api.stormglass.io/v2"
_STORMGLASS_KEY  = os.getenv("STORMGLASS_API_KEY", "")
_SG_TIMEOUT      = 10.0


# ── State ──────────────────────────────────────────────────────────────────────

class MeteoAgentState(TypedDict):
    from_stop:    str
    to_stop:      str
    lat:          float
    lon:          float
    nm_remaining: float
    language:     str
    # Internal
    weather_obs:  Optional[dict]
    prompt:       str
    messages:     List
    # Outputs
    content:      str
    data_sources: List[str]
    data_freshness: str
    error:        Optional[str]


# ── Node 1: prepare_context ────────────────────────────────────────────────────

def prepare_context_node(state: MeteoAgentState) -> MeteoAgentState:
    msg = HumanMessage(
        content=f"[meteo_agent] Preparing weather brief for {state['from_stop']} → {state['to_stop']}"
    )
    return {**state, "weather_obs": None, "messages": [msg], "error": None}


# ── Node 2: fetch_stormglass ───────────────────────────────────────────────────

def fetch_stormglass_node(state: MeteoAgentState) -> MeteoAgentState:
    """
    Fetch current weather point data from StormGlass API.
    Requires STORMGLASS_API_KEY in environment — degrades gracefully without it.
    Fetches: windSpeed, windDirection, waveHeight, wavePeriod, swellHeight.
    """
    if not _STORMGLASS_KEY:
        msg = AIMessage(content="[meteo_agent] StormGlass key not configured — LLM only mode")
        return {**state, "weather_obs": None, "data_freshness": "training_only", "messages": [msg]}

    params_needed = "windSpeed,windDirection,waveHeight,wavePeriod,swellHeight,airTemperature"
    try:
        with httpx.Client(timeout=_SG_TIMEOUT) as client:
            resp = client.get(
                f"{_STORMGLASS_BASE}/weather/point",
                params={
                    "lat":    state["lat"],
                    "lng":    state["lon"],
                    "params": params_needed,
                    "source": "noaa,icon,sg",
                },
                headers={"Authorization": _STORMGLASS_KEY},
            )
            resp.raise_for_status()
            data = resp.json()
            # Extract first hour's aggregated values
            hours = data.get("hours", [])
            if hours:
                h0 = hours[0]

                def _sg_val(field: str) -> Optional[float]:
                    entry = h0.get(field, {})
                    if isinstance(entry, dict):
                        # StormGlass returns {"noaa": v, "sg": v, ...} — take first available
                        for val in entry.values():
                            if val is not None:
                                return round(float(val), 2)
                    return None

                obs = {
                    "wind_speed_ms":    _sg_val("windSpeed"),
                    "wind_dir_deg":     _sg_val("windDirection"),
                    "wave_height_m":    _sg_val("waveHeight"),
                    "wave_period_s":    _sg_val("wavePeriod"),
                    "swell_height_m":   _sg_val("swellHeight"),
                    "air_temp_c":       _sg_val("airTemperature"),
                    "timestamp":        h0.get("time", ""),
                }
                msg = AIMessage(
                    content=f"[meteo_agent] ✅ StormGlass: wind {obs['wind_speed_ms']} m/s, "
                            f"wave {obs['wave_height_m']} m"
                )
                return {**state, "weather_obs": obs, "data_freshness": "live", "messages": [msg]}
    except Exception as exc:
        msg = AIMessage(content=f"[meteo_agent] StormGlass fetch failed ({exc}) — LLM fallback")
        return {**state, "weather_obs": None, "data_freshness": "training_only", "messages": [msg]}

    return {**state, "weather_obs": None, "data_freshness": "training_only", "messages": []}


# ── Prompt builder (shared by llm_generate_node and get_streaming_prompt) ─────────

def _build_meteo_prompt(state: MeteoAgentState) -> str:
    """
    Build the LLM prompt from meteo agent state.
    Requires prepare_context_node + fetch_stormglass_node to have run first.
    """
    lang_full = "French" if state["language"] == "fr" else "English"
    obs       = state.get("weather_obs")
    now_month = datetime.now().strftime("%B")

    if obs:
        ws_kts = round(obs["wind_speed_ms"] * 1.944, 1) if obs.get("wind_speed_ms") else "N/A"
        obs_block = (
            f"LIVE STORMGLASS DATA (at position):\n"
            f"• Wind  : {ws_kts} kts from {obs.get('wind_dir_deg', 'N/A')}°\n"
            f"• Wave  : {obs.get('wave_height_m', 'N/A')} m / {obs.get('wave_period_s', 'N/A')} s period\n"
            f"• Swell : {obs.get('swell_height_m', 'N/A')} m\n"
            f"• Air T : {obs.get('air_temp_c', 'N/A')} °C\n\n"
        )
    else:
        obs_block = f"Live weather data: not available — using climatological knowledge for {now_month}.\n\n"

    return (
        f"You are NAVIGUIDE's meteorological routing advisor for the Berry-Mappemonde "
        f"circumnavigation expedition (French offshore catamaran, beam reach performance).\n\n"
        f"NAVIGATION CONTEXT:\n"
        f"• Active leg     : {state['from_stop']} → {state['to_stop']}\n"
        f"• Position       : {state['lat']:.4f}° lat / {state['lon']:.4f}° lon\n"
        f"• NM to next stop: {state['nm_remaining']:.0f} nm\n"
        f"• Current month  : {now_month}\n"
        f"• Response lang  : {lang_full}\n\n"
        f"{obs_block}"
        f"Provide a weather routing briefing covering:\n"
        f"1. **Current conditions** — wind regime, sea state, visibility at position\n"
        f"2. **Departure window** — optimal timing to depart for {state['to_stop']} "
        f"considering {now_month} climatology\n"
        f"3. **Wind regime** — dominant wind system for this leg "
        f"(trade winds, ITCZ, monsoon, westerlies — with typical direction & speed)\n"
        f"4. **Cyclone/hazard season** — is this leg in a tropical cyclone season? "
        f"Safe window advice\n"
        f"5. **Routing tips** — optimal waypoint strategy to maximise VMG "
        f"(go north/south of rhumb line? avoid calms?)\n\n"
        f"Format in **Markdown**, practical for offshore crew. Max 350 words. "
        f"Mark live data with [Live] and forecast data with [Fcst]."
    )


# ── Node 3: llm_generate ──────────────────────────────────────────────────

def llm_generate_node(state: MeteoAgentState) -> MeteoAgentState:
    obs       = state.get("weather_obs")
    freshness = state.get("data_freshness", "training_only")
    prompt    = _build_meteo_prompt(state)

    content, llm_freshness = call_llm(prompt)

    if not content:
        content = (
            f"## Météo — {state['from_stop']} → {state['to_stop']}\n\n"
            f"⚠️ **Service météo temporairement indisponible.**\n\n"
            f"**Ressources de secours :**\n"
            f"- 🌐 [Passage Weather](https://passageweather.com)\n"
            f"- 🌐 [Windy.com](https://www.windy.com/?{state['lat']},{state['lon']},7)\n"
            f"- 💻 Bulletins GRIB via Saildocs (gribs@saildocs.com)\n"
            f"- 📡 NAVTEX pour zones côtières\n\n"
            f"Distance restante : **{state['nm_remaining']:.0f} nm**."
        )
        freshness = "training_only"

    final_freshness = "live" if obs else llm_freshness or "training_only"
    sources = ["deploy_ai_llm", "noaa_climatology_training"]
    if obs:
        sources.insert(0, "stormglass_live")

    msg = AIMessage(
        content=f"[meteo_agent] ✅ Weather brief generated (freshness={final_freshness})"
    )
    return {
        **state,
        "content":        content,
        "data_sources":   sources,
        "data_freshness": final_freshness,
        "messages":       [msg],
    }


# ── Graph factory ──────────────────────────────────────────────────────────

def build_meteo_agent():
    """Compile and return the Meteo (Weather) LangGraph."""
    graph = StateGraph(MeteoAgentState)
    graph.add_node("prepare_context",  prepare_context_node)
    graph.add_node("fetch_stormglass", fetch_stormglass_node)
    graph.add_node("llm_generate",     llm_generate_node)
    graph.set_entry_point("prepare_context")
    graph.add_edge("prepare_context",  "fetch_stormglass")
    graph.add_edge("fetch_stormglass", "llm_generate")
    graph.add_edge("llm_generate",     END)
    return graph.compile()


# ── Convenience runner ──────────────────────────────────────────────────────────

def run_meteo_agent(
    from_stop:    str,
    to_stop:      str,
    lat:          float,
    lon:          float,
    nm_remaining: float,
    language:     str = "fr",
) -> dict:
    """Invoke the Meteo agent and return a serialisable AgentResponse dict."""
    agent = build_meteo_agent()
    state = agent.invoke({
        "from_stop":    from_stop,
        "to_stop":      to_stop,
        "lat":          lat,
        "lon":          lon,
        "nm_remaining": nm_remaining,
        "language":     language,
        "weather_obs":  None,
        "prompt":       "",
        "messages":     [],
        "content":      "",
        "data_sources": [],
        "data_freshness": "training_only",
        "error":        None,
    })
    return {
        "agent":          "meteo",
        "content":        state["content"],
        "data_sources":   state["data_sources"],
        "generated_at":   datetime.now(timezone.utc).isoformat(),
        "data_freshness": state["data_freshness"],
    }


# ── Streaming helper ──────────────────────────────────────────────────────────

def get_streaming_prompt(
    from_stop:    str,
    to_stop:      str,
    lat:          float,
    lon:          float,
    nm_remaining: float,
    language:     str = "fr",
) -> str:
    """
    Run the data-fetch pipeline and return the built LLM prompt without calling the LLM.
    Used by the /agents/meteo SSE endpoint: StormGlass fetch runs synchronously in a
    threadpool, then the prompt is streamed token-by-token via deploy_ai.stream_llm().
    """
    initial = {
        "from_stop":    from_stop,
        "to_stop":      to_stop,
        "lat":          lat,
        "lon":          lon,
        "nm_remaining": nm_remaining,
        "language":     language,
        "weather_obs":  None,
        "prompt":       "",
        "messages":     [],
        "content":      "",
        "data_sources": [],
        "data_freshness": "training_only",
        "error":        None,
    }
    state = prepare_context_node(initial)
    state = fetch_stormglass_node(state)
    return _build_meteo_prompt(state)
