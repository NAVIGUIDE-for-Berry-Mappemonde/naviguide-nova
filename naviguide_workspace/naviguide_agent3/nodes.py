"""
NAVIGUIDE Agent 3 — LangGraph Node Functions

Graph flow:
  parse_risk_request
       │ (error) ──► END
       ▼
  assess_weather_risks
       ▼
  assess_piracy_zones        ◄─┐
       ▼                       │ (parallel in intent; sequential in graph)
  assess_medical_safety         │
       ▼                       │
  assess_cyclone_risks  ───────┘
       ▼
  compute_risk_scores
       ▼
  llm_risk_analyst
       ▼
  generate_risk_report
       ▼
      END
"""

from datetime import datetime
from langchain_core.messages import AIMessage, HumanMessage

from .state       import RiskState
from .risk_engine import RiskAssessmentEngine

_engine = RiskAssessmentEngine()


# ─────────────────────────────────────────────────────────────────────────────
# NODE 1 — parse_risk_request
# ─────────────────────────────────────────────────────────────────────────────

def parse_risk_request_node(state: RiskState) -> RiskState:
    """Validate input waypoints and extract departure month."""
    waypoints = state.get("waypoints", [])
    errors    = []

    if not waypoints:
        errors.append("No waypoints provided for risk assessment.")
        return {**state, "status": "error", "errors": errors}

    for i, wp in enumerate(waypoints):
        if "lat" not in wp or "lon" not in wp:
            errors.append(f"Waypoint {i} missing lat/lon coordinates.")
        if not (-90  <= wp.get("lat", 0) <= 90):
            errors.append(f"Waypoint {i}: latitude out of range.")
        if not (-180 <= wp.get("lon", 0) <= 180):
            errors.append(f"Waypoint {i}: longitude out of range.")

    if errors:
        return {**state, "status": "error", "errors": errors}

    msg = HumanMessage(
        content=(
            f"[parse_risk_request] ✅ {len(waypoints)} waypoints validated for risk assessment. "
            f"Route: {waypoints[0].get('name')} → {waypoints[-1].get('name')}"
        )
    )
    return {**state, "status": "processing", "messages": [msg], "errors": []}


# ─────────────────────────────────────────────────────────────────────────────
# NODE 2 — assess_weather_risks
# ─────────────────────────────────────────────────────────────────────────────

def assess_weather_risks_node(state: RiskState) -> RiskState:
    """Assess seasonal weather window quality for every waypoint."""
    waypoints       = state["waypoints"]
    departure_month = state.get("constraints", {}).get("departure_month")
    assessments     = _engine.assess_weather_windows(waypoints, departure_month)

    high_risk = [a for a in assessments if a["score"] >= 0.50]
    msg = AIMessage(
        content=(
            f"[assess_weather] {len(assessments)} waypoints assessed. "
            f"{len(high_risk)} HIGH/CRITICAL weather windows detected."
        )
    )
    return {**state, "weather_assessments": assessments, "messages": [msg]}


# ─────────────────────────────────────────────────────────────────────────────
# NODE 3 — assess_piracy_zones
# ─────────────────────────────────────────────────────────────────────────────

def assess_piracy_zones_node(state: RiskState) -> RiskState:
    """Evaluate piracy risk for every waypoint against IMB/MDAT hotspot database."""
    waypoints   = state["waypoints"]
    assessments = _engine.assess_piracy(waypoints)

    critical = [a for a in assessments if a["risk_level"] in ("HIGH", "CRITICAL")]
    msg = AIMessage(
        content=(
            f"[assess_piracy] {len(assessments)} waypoints checked. "
            f"{len(critical)} HIGH/CRITICAL piracy risk areas identified."
        )
    )
    return {**state, "piracy_assessments": assessments, "messages": [msg]}


# ─────────────────────────────────────────────────────────────────────────────
# NODE 4 — assess_medical_safety
# ─────────────────────────────────────────────────────────────────────────────

def assess_medical_safety_node(state: RiskState) -> RiskState:
    """Rate medical access and medevac feasibility per stopover."""
    waypoints   = state["waypoints"]
    assessments = _engine.assess_medical(waypoints)

    isolated = [a for a in assessments if a["medevac_hours"] >= 48]
    msg = AIMessage(
        content=(
            f"[assess_medical] {len(assessments)} stopovers assessed. "
            f"{len(isolated)} locations have medevac lead time ≥ 48h."
        )
    )
    return {**state, "medical_assessments": assessments, "messages": [msg]}


# ─────────────────────────────────────────────────────────────────────────────
# NODE 5 — assess_cyclone_risks
# ─────────────────────────────────────────────────────────────────────────────

def assess_cyclone_risks_node(state: RiskState) -> RiskState:
    """Check tropical storm / cyclone / hurricane exposure for each waypoint."""
    waypoints       = state["waypoints"]
    departure_month = state.get("constraints", {}).get("departure_month")
    assessments     = _engine.assess_cyclones(waypoints, departure_month)

    active = [a for a in assessments if a.get("season_active")]
    peak   = [a for a in assessments if a.get("in_peak")]
    msg = AIMessage(
        content=(
            f"[assess_cyclone] {len(active)} waypoints inside active cyclone season. "
            f"{len(peak)} in peak season window."
        )
    )
    return {**state, "cyclone_assessments": assessments, "messages": [msg]}


# ─────────────────────────────────────────────────────────────────────────────
# NODE 6 — compute_risk_scores
# ─────────────────────────────────────────────────────────────────────────────

def compute_risk_scores_node(state: RiskState) -> RiskState:
    """
    Combine all four risk dimensions into a single composite score per waypoint.
    Weights: weather 25% | piracy 30% | medical 20% | cyclone 25%
    """
    scores = _engine.compute_overall_scores(
        weather_list  = state.get("weather_assessments",  []),
        piracy_list   = state.get("piracy_assessments",   []),
        medical_list  = state.get("medical_assessments",  []),
        cyclone_list  = state.get("cyclone_assessments",  []),
    )

    critical = [s for s in scores if s["level"] == "CRITICAL"]
    high     = [s for s in scores if s["level"] == "HIGH"]
    avg      = sum(s["overall"] for s in scores) / len(scores) if scores else 0.0

    msg = AIMessage(
        content=(
            f"[compute_scores] avg={avg:.3f} | "
            f"CRITICAL={len(critical)} | HIGH={len(high)} | "
            f"total={len(scores)} waypoints scored."
        )
    )
    return {**state, "risk_scores": scores, "status": "analysed", "messages": [msg]}


# ─────────────────────────────────────────────────────────────────────────────
# NODE 7 — llm_risk_analyst
# ─────────────────────────────────────────────────────────────────────────────

def llm_risk_analyst_node(state: RiskState) -> RiskState:
    """
    Call Deploy AI (GPT-4o) to generate an executive risk briefing
    with prioritised recommendations for the skipper.
    """
    scores   = state.get("risk_scores", [])
    avg      = sum(s["overall"] for s in scores) / len(scores) if scores else 0.0
    critical = [s for s in scores if s["level"] == "CRITICAL"]
    high     = [s for s in scores if s["level"] == "HIGH"]

    # Build a structured summary for the LLM
    top_risks = sorted(scores, key=lambda x: x["overall"], reverse=True)[:5]
    risk_table = "\n".join(
        f"  {i+1}. {s['name']} — overall={s['overall']:.2f} [{s['level']}] "
        f"(weather={s['components']['weather_score']:.2f}, "
        f"piracy={s['components']['piracy_score']:.2f}, "
        f"medical={s['components']['medical_score']:.2f}, "
        f"cyclone={s['components']['cyclone_score']:.2f})"
        for i, s in enumerate(top_risks)
    )

    piracy_zones   = list({a["zone"]  for a in state.get("piracy_assessments", [])  if a["score"] > 0.3})
    cyclone_zones  = list({a["basin"] for a in state.get("cyclone_assessments", []) if a.get("season_active")})
    medevac_remote = [a["name"] for a in state.get("medical_assessments", []) if a["medevac_hours"] >= 72]

    prompt = f"""You are NAVIGUIDE's chief maritime safety officer briefing the skipper before a world circumnavigation.

EXPEDITION RISK PROFILE — Berry-Mappemonde
───────────────────────────────────────────
Total waypoints assessed : {len(scores)}
Average composite risk   : {avg:.3f}  (0=safe, 1=critical)
CRITICAL risk stops      : {len(critical)} — {', '.join(s['name'] for s in critical) or 'None'}
HIGH risk stops          : {len(high)} — {', '.join(s['name'] for s in high) or 'None'}

TOP 5 RISKIEST WAYPOINTS:
{risk_table}

KEY THREAT CLUSTERS:
• Active piracy zones en route : {', '.join(piracy_zones) or 'None'}
• Active cyclone basins        : {', '.join(cyclone_zones) or 'None'}
• Remote medevac (≥72h)        : {', '.join(medevac_remote) or 'None'}

Produce a structured skipper briefing with EXACTLY these sections:
1. EXECUTIVE SUMMARY (2 sentences)
2. TOP 3 CRITICAL RISKS (bullet list, each with one mitigation)
3. SEASONAL TIMING RECOMMENDATION (1 sentence per ocean basin)
4. NON-NEGOTIABLE SAFETY REQUIREMENTS (3 bullet points)

Professional maritime tone. Max 200 words total."""

    summary = ""
    try:
        from llm_utils import invoke_llm
        summary = invoke_llm(prompt, fallback_msg="Manual review of CRITICAL/HIGH waypoints recommended.")
    except Exception as exc:
        summary = (
            f"LLM risk analyst unavailable ({exc}). "
            "Manual review of CRITICAL/HIGH waypoints recommended."
        )

    if not summary:
        summary = (
            f"Risk analysis complete. {len(scores)} waypoints assessed, "
            f"avg composite risk {avg:.3f}. "
            f"CRITICAL: {len(critical)}, HIGH: {len(high)}. "
            "Manual review of CRITICAL/HIGH waypoints recommended."
        )

    msg = AIMessage(content=f"[llm_risk_analyst] {summary}")
    return {
        **state,
        "llm_risk_summary": summary,
        "messages":         [msg],
    }


# ─────────────────────────────────────────────────────────────────────────────
# NODE 8 — generate_risk_report
# ─────────────────────────────────────────────────────────────────────────────

def generate_risk_report_node(state: RiskState) -> RiskState:
    """
    Assemble the final structured risk report — the safety 'digital twin'
    companion to Agent 1's route plan.
    """
    scores   = state.get("risk_scores", [])
    avg      = sum(s["overall"] for s in scores) / len(scores) if scores else 0.0
    critical = [s for s in scores if s["level"] == "CRITICAL"]
    high     = [s for s in scores if s["level"] == "HIGH"]

    risk_report = {
        "report_type": "Maritime Risk Assessment",
        "metadata": {
            "expedition_name":        "Berry-Mappemonde",
            "agent":                  "NAVIGUIDE Risk Assessment Agent v1.0",
            "framework":              "LangGraph",
            "generated_at":           datetime.utcnow().isoformat() + "Z",
            "algorithm_version":      "1.0.0",
            "waypoints_assessed":     len(scores),
            "overall_expedition_risk": round(avg, 3),
            "expedition_risk_level":  (
                "CRITICAL" if avg >= 0.75 else
                "HIGH"     if avg >= 0.50 else
                "MODERATE" if avg >= 0.25 else "LOW"
            ),
            "critical_stops_count":   len(critical),
            "high_risk_stops_count":  len(high),
            "executive_briefing":     state.get("llm_risk_summary", ""),
        },
        "risk_matrix": scores,
        "detail": {
            "weather_windows":    state.get("weather_assessments",  []),
            "piracy_zones":       state.get("piracy_assessments",   []),
            "medical_access":     state.get("medical_assessments",  []),
            "cyclone_exposure":   state.get("cyclone_assessments",  []),
        },
        "critical_alerts": [
            {
                "waypoint":   s["name"],
                "risk_level": s["level"],
                "score":      s["overall"],
                "dominant_risk": max(s["components"], key=s["components"].get),
            }
            for s in scores if s["level"] in ("CRITICAL", "HIGH")
        ],
    }

    msg = AIMessage(
        content=(
            f"[generate_risk_report] ✅ Risk report complete — "
            f"{len(scores)} waypoints | expedition risk={round(avg, 3)} "
            f"[{risk_report['metadata']['expedition_risk_level']}] | "
            f"{len(critical)} CRITICAL alerts"
        )
    )
    return {**state, "risk_report": risk_report, "status": "complete", "messages": [msg]}

