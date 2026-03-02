"""
NAVIGUIDE Orchestrator — LangGraph Node Functions

Orchestration flow:
  validate_expedition_request
           │ error ──────────────────────────────────────► END
           ▼
  run_route_intelligence          ← invokes Agent 1 graph directly
           │ agent1_failed ───────────────────────────────► END
           ▼
  run_risk_assessment             ← invokes Agent 3 graph (with Agent 1 route)
           ▼
  llm_expedition_briefing         ← Claude/ChatBedrock unified skipper executive summary
           ▼
  generate_expedition_plan        ← merge Agent 1 + Agent 3 → digital twin
           ▼
          END
"""

import sys
import os
import logging
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, AIMessage

# Load .env from workspace root (contains ANTHROPIC_API_KEY)
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

try:
    import anthropic as _anthropic
    _ANTHROPIC_CLIENT   = _anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    _ANTHROPIC_AVAILABLE = bool(os.getenv("ANTHROPIC_API_KEY"))
except Exception:
    _ANTHROPIC_CLIENT    = None
    _ANTHROPIC_AVAILABLE = False

from .state import OrchestratorState

# ── Agent imports (direct graph invocation — single process) ──────────────────
# Add workspace root to path so both agent packages resolve correctly
_WS_ROOT = str(Path(__file__).resolve().parents[1])
if _WS_ROOT not in sys.path:
    sys.path.insert(0, _WS_ROOT)

from naviguide_agent1.graph     import build_route_intelligence_agent
from naviguide_agent1.router    import BerryMappemondeRouter
from naviguide_agent3.graph     import build_risk_assessment_agent

log = logging.getLogger("orchestrator.nodes")

# Build agent graphs once at module import (cached for all requests)
_agent1_graph = None
_agent3_graph = None


def _get_agent1():
    global _agent1_graph
    if _agent1_graph is None:
        _agent1_graph = build_route_intelligence_agent()
    return _agent1_graph


def _get_agent3():
    global _agent3_graph
    if _agent3_graph is None:
        _agent3_graph = build_risk_assessment_agent()
    return _agent3_graph


# ──────────────────────────────────────────────────────────────────────────────
# NODE 1 — validate_expedition_request
# ──────────────────────────────────────────────────────────────────────────────

def validate_expedition_request_node(state: OrchestratorState) -> OrchestratorState:
    """Validate input waypoints and initialise orchestrator state."""
    waypoints = state.get("waypoints", [])
    errors    = []

    if len(waypoints) < 2:
        errors.append("At least 2 waypoints are required for expedition planning.")
        msg = HumanMessage(content=f"[validate] ❌ Validation failed: {errors}")
        return {**state, "status": "error", "errors": errors, "messages": [msg]}

    for i, wp in enumerate(waypoints):
        lat = wp.get("lat", 0)
        lon = wp.get("lon", 0)
        if not (-90  <= lat <= 90):
            errors.append(f"Waypoint {i} '{wp.get('name')}': latitude {lat} out of range")
        if not (-180 <= lon <= 180):
            errors.append(f"Waypoint {i} '{wp.get('name')}': longitude {lon} out of range")

    if errors:
        msg = HumanMessage(content=f"[validate] ❌ Validation failed: {errors}")
        return {**state, "status": "error", "errors": errors, "messages": [msg]}

    msg = HumanMessage(
        content=(
            f"[validate] ✅ {len(waypoints)} waypoints validated. "
            f"Expedition: {waypoints[0].get('name')} → {waypoints[-1].get('name')}"
        )
    )
    return {
        **state,
        "status":          "running_a1",
        "errors":          [],
        "agent1_status":   "pending",
        "agent3_status":   "pending",
        "messages":        [msg],
    }


# ──────────────────────────────────────────────────────────────────────────────
# NODE 2 — run_route_intelligence
# ──────────────────────────────────────────────────────────────────────────────

def run_route_intelligence_node(state: OrchestratorState) -> OrchestratorState:
    """
    Invoke Agent 1 (Route Intelligence) as a direct subgraph call.
    Translates orchestrator state → agent1 state → back to orchestrator state.
    """
    log.info("[orchestrator] Running Agent 1 — Route Intelligence")

    initial_a1 = {
        "waypoints":            state["waypoints"],
        "vessel_specs":         state.get("vessel_specs") or BerryMappemondeRouter.VESSEL_PROFILE,
        "constraints":          state.get("constraints", {}),
        "expedition_id":        state.get("expedition_id"),   # forward to fetch_vmg_node
        "polar_vmg":            None,
        "polar_avg_speed":      None,
        "raw_segments":         [],
        "anti_shipping_scores": [],
        "safety_validations":   [],
        "route_plan":           {},
        "messages":             [],
        "errors":               [],
        "status":               "init",
        "chat_id":              None,
        "access_token":         None,
        "route_advisor_notes":  "",
    }

    try:
        result  = _get_agent1().invoke(initial_a1)
        scores  = result.get("anti_shipping_scores", [])
        avg     = round(sum(scores) / len(scores), 4) if scores else 0.0
        meta    = result.get("route_plan", {}).get("metadata", {})
        eta_d   = meta.get("total_eta_days")

        polar_info = ""
        if result.get("polar_avg_speed"):
            polar_info = (
                f" | polar_speed={result['polar_avg_speed']} kts"
                f" | ETA={eta_d} days"
            )

        msg = AIMessage(
            content=(
                f"[agent1] ✅ Route computed: "
                f"{len(result.get('raw_segments', []))} segments | "
                f"anti-shipping avg={avg} | "
                f"status={result.get('status')}"
                f"{polar_info}"
            )
        )
        log.info(f"[orchestrator] Agent 1 complete: status={result.get('status')}")
        return {
            **state,
            "agent1_status":    result.get("status", "complete"),
            "agent1_errors":    result.get("errors", []),
            "route_plan":       result.get("route_plan", {}),
            "anti_shipping_avg": avg,
            "polar_vmg":        result.get("polar_vmg"),
            "polar_avg_speed":  result.get("polar_avg_speed"),
            "total_eta_days":   eta_d,
            "status":           "running_a3",
            "messages":         [msg],
        }

    except Exception as exc:
        log.error(f"[orchestrator] Agent 1 failed: {exc}")
        msg = AIMessage(content=f"[agent1] ❌ Failed: {exc}")
        return {
            **state,
            "agent1_status": "failed",
            "agent1_errors": [str(exc)],
            "status":        "agent1_failed",
            "messages":      [msg],
        }


# ──────────────────────────────────────────────────────────────────────────────
# NODE 3 — run_risk_assessment
# ──────────────────────────────────────────────────────────────────────────────

def run_risk_assessment_node(state: OrchestratorState) -> OrchestratorState:
    """
    Invoke Agent 3 (Risk Assessment) with waypoints + optional Agent 1 segments.
    """
    log.info("[orchestrator] Running Agent 3 — Risk Assessment")

    # Pass Agent 1 route segments as context to Agent 3
    route_plan     = state.get("route_plan", {})
    route_segments = route_plan.get("features", []) if route_plan else []

    initial_a3 = {
        "waypoints":            state["waypoints"],
        "route_segments":       route_segments,
        "weather_assessments":  [],
        "piracy_assessments":   [],
        "medical_assessments":  [],
        "cyclone_assessments":  [],
        "risk_scores":          [],
        "risk_report":          {},
        "messages":             [],
        "errors":               [],
        "status":               "init",
        "chat_id":              None,
        "access_token":         None,
        "llm_risk_summary":     "",
        "constraints":          state.get("constraints", {}),
    }

    try:
        result = _get_agent3().invoke(initial_a3)
        report = result.get("risk_report", {})
        level  = report.get("metadata", {}).get("expedition_risk_level", "UNKNOWN")

        msg = AIMessage(
            content=(
                f"[agent3] ✅ Risk assessed: "
                f"{len(result.get('risk_scores', []))} waypoints | "
                f"expedition risk={level} | "
                f"status={result.get('status')}"
            )
        )
        log.info(f"[orchestrator] Agent 3 complete: risk={level}")
        return {
            **state,
            "agent3_status":         result.get("status", "complete"),
            "agent3_errors":         result.get("errors", []),
            "risk_report":           report,
            "expedition_risk_level": level,
            "status":                "briefing",
            "messages":              [msg],
        }

    except Exception as exc:
        log.error(f"[orchestrator] Agent 3 failed: {exc}")
        msg = AIMessage(content=f"[agent3] ❌ Failed: {exc}")
        return {
            **state,
            "agent3_status":         "failed",
            "agent3_errors":         [str(exc)],
            "expedition_risk_level": "UNKNOWN",
            "status":                "briefing",   # continue to generate plan even if A3 fails
            "messages":              [msg],
        }


# ──────────────────────────────────────────────────────────────────────────────
# NODE 4 — llm_expedition_briefing
# ──────────────────────────────────────────────────────────────────────────────

def llm_expedition_briefing_node(state: OrchestratorState) -> OrchestratorState:
    """
    Generate unified executive skipper briefing combining Agent 1 + Agent 3 outputs.
    Uses Anthropic Claude API; falls back to structured static text.
    """
    route_plan   = state.get("route_plan", {})
    risk_report  = state.get("risk_report", {})
    risk_level   = state.get("expedition_risk_level", "UNKNOWN")
    waypoints    = state.get("waypoints", [])
    anti_avg     = state.get("anti_shipping_avg", 0.0)
    language     = state.get("language", "en").lower()
    if language not in ("en", "fr"):
        language = "en"

    # Gather key data for the prompt
    risk_metadata   = risk_report.get("metadata", {})
    critical_alerts = risk_report.get("critical_alerts", [])
    route_metadata  = route_plan.get("metadata", {}) if isinstance(route_plan, dict) else {}
    total_nm        = route_metadata.get("total_distance_nm", 0)

    # Polar / VMG performance data
    polar_vmg       = state.get("polar_vmg")         # {tws: {upwind, downwind, gybe_angle}}
    polar_avg_speed = state.get("polar_avg_speed")
    total_eta_days  = state.get("total_eta_days") or route_metadata.get("total_eta_days")
    polar_data_used = route_metadata.get("polar_data_used", False)

    # Build VMG context block for the prompt
    def _vmg_block_en():
        if not polar_vmg:
            return "  Polar data not available — ETAs based on vessel default speed (10 kts)."
        lines = [
            f"  Polar-based average speed: {polar_avg_speed:.1f} kts",
            f"  Expedition ETA (polar): {total_eta_days:.0f} days",
        ]
        for tws_key in ["10", "12", "16"]:
            entry = polar_vmg.get(tws_key, {})
            uw = entry.get("upwind",   {})
            dw = entry.get("downwind", {})
            if uw and dw:
                lines.append(
                    f"  TWS {tws_key} kts → upwind VMG {uw.get('vmg', 0):.1f} kts "
                    f"@ {uw.get('twa', 0)}° | downwind VMG {dw.get('vmg', 0):.1f} kts "
                    f"@ {dw.get('twa', 0)}°"
                )
        return "\n".join(lines)

    def _vmg_block_fr():
        if not polar_vmg:
            return "  Données polaires indisponibles — ETAs basés sur vitesse par défaut (10 nœuds)."
        lines = [
            f"  Vitesse moyenne polaire : {polar_avg_speed:.1f} nœuds",
            f"  ETA expédition (polaires) : {total_eta_days:.0f} jours",
        ]
        for tws_key in ["10", "12", "16"]:
            entry = polar_vmg.get(tws_key, {})
            uw = entry.get("upwind",   {})
            dw = entry.get("downwind", {})
            if uw and dw:
                lines.append(
                    f"  TWS {tws_key} nœuds → VMG au près {uw.get('vmg', 0):.1f} nœuds "
                    f"@ {uw.get('twa', 0)}° | VMG portant {dw.get('vmg', 0):.1f} nœuds "
                    f"@ {dw.get('twa', 0)}°"
                )
        return "\n".join(lines)

    critical_list = "\n".join(
        f"  • {a['waypoint']} [{a['risk_level']}] — dominant: {a.get('dominant_risk', 'N/A')}"
        for a in critical_alerts[:5]
    ) or ("  No critical alerts detected." if language == "en" else "  Aucune alerte critique détectée.")

    if language == "en":
        prompt = f"""You are the NAVIGUIDE chief maritime safety officer, specialist in offshore circumnavigations.

BERRY-MAPPEMONDE EXPEDITION SUMMARY
═════════════════════════════════════
Stopovers: {len(waypoints)}
Total distance: {total_nm:,.0f} nautical miles
Expedition risk level: {risk_level}
Average anti-shipping score: {anti_avg:.3f}  (1.0 = ideal)
CRITICAL/HIGH alerts: {len(critical_alerts)}

BOAT PERFORMANCE (polar data):
{_vmg_block_en()}

PRIORITY ALERTS:
{critical_list}

AGENT 3 STATISTICS:
• Waypoints assessed: {risk_metadata.get('waypoints_assessed', len(waypoints))}
• Average risk score: {risk_metadata.get('overall_expedition_risk', 0):.3f}
• CRITICAL alerts: {risk_metadata.get('critical_stops_count', 0)}
• HIGH alerts: {risk_metadata.get('high_risk_stops_count', 0)}

Write a structured executive skipper briefing with exactly these sections:
1. EXECUTIVE SUMMARY (2-3 sentences — include ETA estimate and average VMG if polar data available)
2. CRITICAL ALERTS (bullet list, max 4 points, with mitigation)
3. RECOMMENDED WEATHER WINDOWS (per ocean region, 1 sentence each)
4. NON-NEGOTIABLE SAFETY REQUIREMENTS (3 points)

Tone: professional, concise, confirmed offshore expertise. Max 280 words. Reply in English."""
    else:
        prompt = f"""Tu es le chef officier de sécurité maritime de NAVIGUIDE, spécialiste en circumnavigations hauturières.

RÉSUMÉ EXPÉDITION BERRY-MAPPEMONDE
═══════════════════════════════════
Escales : {len(waypoints)}
Distance totale : {total_nm:,.0f} milles nautiques
Niveau de risque expédition : {risk_level}
Score anti-trafic maritime moyen : {anti_avg:.3f}  (1.0 = idéal)
Alertes CRITICAL/HIGH : {len(critical_alerts)}

PERFORMANCES DU BATEAU (données polaires) :
{_vmg_block_fr()}

ALERTES PRIORITAIRES :
{critical_list}

STATISTIQUES AGENT 3 :
• Waypoints évalués : {risk_metadata.get('waypoints_assessed', len(waypoints))}
• Score moyen : {risk_metadata.get('overall_expedition_risk', 0):.3f}
• Alertes CRITICAL : {risk_metadata.get('critical_stops_count', 0)}
• Alertes HIGH : {risk_metadata.get('high_risk_stops_count', 0)}

Rédige un briefing skipper exécutif structuré avec exactement ces sections :
1. RÉSUMÉ EXÉCUTIF (2-3 phrases — inclure l'ETA estimé et la VMG moyenne si données polaires disponibles)
2. ALERTES CRITIQUES (liste à puces, max 4 points, avec mitigation)
3. FENÊTRES MÉTÉO RECOMMANDÉES (par région océanique, 1 phrase chacune)
4. EXIGENCES DE SÉCURITÉ NON NÉGOCIABLES (3 points)

Ton : professionnel, concis, expertise hauturière confirmée. Max 280 mots. Répondre en français."""

    briefing = ""

    if _ANTHROPIC_AVAILABLE and _ANTHROPIC_CLIENT:
        try:
            response = _ANTHROPIC_CLIENT.messages.create(
                model      = "claude-opus-4-5",
                max_tokens = 600,
                messages   = [{"role": "user", "content": prompt}],
            )
            briefing = response.content[0].text
            log.info(f"[orchestrator] LLM briefing generated via Anthropic Claude (lang={language})")
        except Exception as exc:
            log.warning(f"[orchestrator] Anthropic unavailable ({exc}) — using fallback briefing")

    if not briefing:
        # Structured fallback briefing
        briefing = _build_fallback_briefing(
            risk_level, critical_alerts, total_nm, len(waypoints), language
        )

    msg = AIMessage(content=f"[llm_briefing] ✅ Executive briefing generated ({len(briefing)} chars)")
    return {
        **state,
        "executive_briefing": briefing,
        "status":             "generating_plan",
        "messages":           [msg],
    }


def _build_fallback_briefing(
    risk_level: str,
    critical_alerts: list,
    total_nm: float,
    waypoint_count: int,
    language: str = "en",
) -> str:
    """Structured fallback when LLM is unavailable. Respects language parameter."""
    if language == "fr":
        alerts_text = "\n".join(
            f"• {a['waypoint']} [{a['risk_level']}] — {a.get('dominant_risk', 'risque composite')}"
            for a in critical_alerts[:4]
        ) or "• Aucune alerte critique sur le tracé."
        return (
            f"BRIEFING EXPÉDITION BERRY-MAPPEMONDE — TOUR DU MONDE DES TERRITOIRES FRANÇAIS\n\n"
            f"1. RÉSUMÉ EXÉCUTIF\n"
            f"L'expédition Berry-Mappemonde couvre {total_nm:,.0f} milles nautiques à travers "
            f"{waypoint_count} escales dans les territoires français d'outre-mer. "
            f"Le niveau de risque global évalué est {risk_level}. "
            f"Une préparation approfondie et un calendrier respectant les fenêtres météo "
            f"saisonnières sont impératifs.\n\n"
            f"2. ALERTES CRITIQUES\n"
            f"{alerts_text}\n\n"
            f"3. FENÊTRES MÉTÉO RECOMMANDÉES\n"
            f"• Atlantique N (La Rochelle → Canaries) : mai–juin (alizés établis)\n"
            f"• Atlantique tropical (Canaries → Caraïbes) : novembre–janvier\n"
            f"• Pacifique S (Cayenne → Papeete) : avril–juin (hors cyclone)\n"
            f"• Océan Indien S (Nouméa → Réunion) : mai–septembre\n\n"
            f"4. EXIGENCES DE SÉCURITÉ NON NÉGOCIABLES\n"
            f"• Balise EPIRB 406 MHz homologuée + AIS classe B actif permanent\n"
            f"• Trousse médicale hauturière complète + formation premiers secours en mer\n"
            f"• Éviter les zones cycloniques en saison active (voir alertes ci-dessus)\n\n"
            f"Bonne route, Commandant. NAVIGUIDE surveille votre expédition."
        )
    else:
        alerts_text = "\n".join(
            f"• {a['waypoint']} [{a['risk_level']}] — {a.get('dominant_risk', 'composite risk')}"
            for a in critical_alerts[:4]
        ) or "• No critical alerts detected on route."
        return (
            f"BERRY-MAPPEMONDE EXPEDITION BRIEFING — WORLD TOUR OF FRENCH TERRITORIES\n\n"
            f"1. EXECUTIVE SUMMARY\n"
            f"The Berry-Mappemonde expedition covers {total_nm:,.0f} nautical miles across "
            f"{waypoint_count} stopovers in French overseas territories. "
            f"The overall assessed risk level is {risk_level}. "
            f"Thorough preparation and a schedule respecting seasonal weather windows are imperative.\n\n"
            f"2. CRITICAL ALERTS\n"
            f"{alerts_text}\n\n"
            f"3. RECOMMENDED WEATHER WINDOWS\n"
            f"• N Atlantic (La Rochelle → Canaries): May–June (established trade winds)\n"
            f"• Tropical Atlantic (Canaries → Caribbean): November–January\n"
            f"• S Pacific (Cayenne → Papeete): April–June (outside cyclone season)\n"
            f"• S Indian Ocean (Nouméa → Réunion): May–September\n\n"
            f"4. NON-NEGOTIABLE SAFETY REQUIREMENTS\n"
            f"• Certified 406 MHz EPIRB + permanent Class B AIS transponder\n"
            f"• Full offshore medical kit + offshore first-aid certification\n"
            f"• Avoid active cyclone zones during cyclone season (see alerts above)\n\n"
            f"Fair winds, Captain. NAVIGUIDE is monitoring your expedition."
        )


# ──────────────────────────────────────────────────────────────────────────────
# NODE 5 — generate_expedition_plan
# ──────────────────────────────────────────────────────────────────────────────

def generate_expedition_plan_node(state: OrchestratorState) -> OrchestratorState:
    """
    Merge Agent 1 + Agent 3 outputs into the unified expedition digital twin.
    """
    route_plan      = state.get("route_plan", {})
    risk_report     = state.get("risk_report", {})
    risk_metadata   = risk_report.get("metadata", {})
    critical_alerts = risk_report.get("critical_alerts", [])
    waypoints       = state.get("waypoints", [])

    # Compute voyage statistics
    route_meta   = route_plan.get("metadata", {}) if isinstance(route_plan, dict) else {}
    total_nm     = route_meta.get("total_distance_nm", 0)
    total_segs   = route_meta.get("total_segments", max(0, len(waypoints) - 1))
    anti_avg     = state.get("anti_shipping_avg", route_meta.get("anti_shipping_avg_score", 0))
    risk_level   = state.get("expedition_risk_level", "UNKNOWN")
    overall_risk = risk_metadata.get("overall_expedition_risk", 0.0)
    high_count   = risk_metadata.get("high_risk_stops_count", 0)
    crit_count   = risk_metadata.get("critical_stops_count", 0)

    # ── Build unified GeoJSON — Route features + Risk overlays ────────────────
    route_features = []
    if isinstance(route_plan, dict) and "features" in route_plan:
        route_features = route_plan["features"]

    # Add risk marker points for CRITICAL and HIGH waypoints
    risk_features = []
    for alert in critical_alerts:
        # Find the waypoint coordinates
        wp_coords = next(
            ({"lat": wp["lat"], "lon": wp["lon"]}
             for wp in waypoints
             if alert["waypoint"].lower() in wp.get("name", "").lower()
             or wp.get("name", "").lower() in alert["waypoint"].lower()),
            None
        )
        if wp_coords:
            risk_features.append({
                "type": "Feature",
                "geometry": {
                    "type":        "Point",
                    "coordinates": [wp_coords["lon"], wp_coords["lat"]],
                },
                "properties": {
                    "type":           "risk_alert",
                    "waypoint":       alert["waypoint"],
                    "risk_level":     alert["risk_level"],
                    "dominant_risk":  alert.get("dominant_risk", ""),
                    "score":          alert.get("score", 0.0),
                    "agent":          "Agent3-RiskAssessment",
                },
            })

    unified_geojson = {
        "type": "FeatureCollection",
        "metadata": {
            "expedition_name":         "Berry-Mappemonde",
            "source":                  "NAVIGUIDE Multi-Agent Orchestrator",
            "framework":               "LangGraph",
            "generated_at":            datetime.utcnow().isoformat() + "Z",
            "total_distance_nm":       total_nm,
            "expedition_risk_level":   risk_level,
            "overall_expedition_risk": overall_risk,
        },
        "features": route_features + risk_features,
    }

    # ── Format critical_alerts for the Sidebar ────────────────────────────────
    sidebar_alerts = []
    for alert in critical_alerts:
        components = {}
        # Try to get component scores from risk_matrix
        for scored_wp in risk_report.get("risk_matrix", []):
            if (scored_wp.get("name", "").lower() in alert["waypoint"].lower() or
                    alert["waypoint"].lower() in scored_wp.get("name", "").lower()):
                components = scored_wp.get("components", {})
                break
        sidebar_alerts.append({
            "waypoint":      alert["waypoint"],
            "risk_level":    alert["risk_level"],
            "dominant_risk": alert.get("dominant_risk", ""),
            "scores": {
                "weather_score": components.get("weather_score", 0.0),
                "cyclone_score": components.get("cyclone_score", 0.0),
                "piracy_score":  components.get("piracy_score",  0.0),
                "medical_score": components.get("medical_score", 0.0),
            },
        })

    expedition_plan = {
        "executive_briefing": state.get("executive_briefing", ""),
        "voyage_statistics": {
            "total_distance_nm":       total_nm,
            "total_segments":          total_segs,
            "expedition_risk_level":   risk_level,
            "overall_expedition_risk": overall_risk,
            "anti_shipping_avg":       anti_avg,
            "high_risk_count":         high_count,
            "critical_count":          crit_count,
            # Polar / ETA data
            "total_eta_days":          state.get("total_eta_days"),
            "polar_avg_speed_knots":   state.get("polar_avg_speed"),
            "polar_data_used":         state.get("polar_avg_speed") is not None,
            "expedition_id":           state.get("expedition_id"),
        },
        "critical_alerts": sidebar_alerts,
        "unified_geojson":  unified_geojson,
        "full_route_intelligence": {
            "status":   state.get("agent1_status", "unknown"),
            "metadata": route_meta,
        },
        "full_risk_assessment": {
            "status":   state.get("agent3_status", "unknown"),
            "metadata": risk_metadata,
        },
    }

    msg = AIMessage(
        content=(
            f"[generate_plan] ✅ Expedition plan complete — "
            f"{total_nm:,.0f} nm | risk={risk_level} | "
            f"{len(sidebar_alerts)} alerts | "
            f"{len(unified_geojson['features'])} GeoJSON features"
        )
    )
    log.info(f"[orchestrator] Expedition plan generated: {total_nm} nm, risk={risk_level}")

    return {
        **state,
        "expedition_plan": expedition_plan,
        "status":          "complete",
        "messages":        [msg],
    }
