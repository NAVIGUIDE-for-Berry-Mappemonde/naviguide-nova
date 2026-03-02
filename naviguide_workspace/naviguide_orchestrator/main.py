"""
NAVIGUIDE — Multi-Agent Orchestrator
FastAPI entry point — port 3008

Endpoints
─────────
GET  /                                   Health + agent status
POST /api/v1/expedition/plan             Custom waypoint expedition plan
POST /api/v1/expedition/plan/berry-mappemonde   Pre-configured expedition
GET  /api/v1/expedition/graph            LangGraph orchestration diagram
GET  /api/v1/expedition/status           Live agent availability check
"""

import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ── Workspace path setup (so agent packages resolve) ─────────────────────────
_WS_ROOT = str(Path(__file__).resolve().parents[1])
if _WS_ROOT not in sys.path:
    sys.path.insert(0, _WS_ROOT)

from .graph  import build_orchestrator
from .state  import OrchestratorState
from naviguide_agent1.router    import BerryMappemondeRouter
from naviguide_agent3.geojson_data import BERRY_MAPPEMONDE_WAYPOINTS as _BM_WP_RISK

# ── Berry-Mappemonde orchestrator waypoints (with skip_maritime flags) ────────
BERRY_MAPPEMONDE_WAYPOINTS = [
    {"name": "La Rochelle",                             "lat":  46.1591, "lon":  -1.1520, "mandatory": True},
    {"name": "Ajaccio (Corse)",                         "lat":  41.9192, "lon":   8.7386, "mandatory": True},
    {"name": "Îles Canaries",                           "lat":  28.5521, "lon": -16.1529, "mandatory": True},
    {"name": "Fort-de-France (Martinique)",             "lat":  14.6037, "lon": -61.0731, "mandatory": True},
    {"name": "Pointe-à-Pitre (Guadeloupe)",             "lat":  16.2415, "lon": -61.5331, "mandatory": True},
    {"name": "Gustavia (Saint-Barthélemy)",             "lat":  17.8962, "lon": -62.8498, "mandatory": True},
    {"name": "Marigot (Saint-Martin)",                  "lat":  18.0679, "lon": -63.0822, "mandatory": True},
    {"name": "Halifax (Nouvelle-Écosse)",               "lat":  44.6488, "lon": -63.5752, "mandatory": True, "skip_maritime": True},
    {"name": "Saint-Pierre (Saint-Pierre-et-Miquelon)", "lat":  46.7811, "lon": -56.1778, "mandatory": True},
    {"name": "Cayenne (Guyane française)",              "lat":   4.9333, "lon": -52.3333, "mandatory": True},
    {"name": "Papeete (Polynésie française)",           "lat": -17.5516, "lon":-149.5585, "mandatory": True},
    {"name": "Mata-Utu (Wallis-et-Futuna)",             "lat": -13.2825, "lon":-176.1736, "mandatory": True},
    {"name": "Nouméa (Nouvelle-Calédonie)",             "lat": -22.2758, "lon": 166.4572, "mandatory": True},
    {"name": "Dzaoudzi (Mayotte)",                      "lat": -12.7871, "lon":  45.2750, "mandatory": True},
    {"name": "Tromelin (TAAF)",                         "lat": -15.8900, "lon":  54.5200, "mandatory": True},
    {"name": "Saint-Gilles (La Réunion)",               "lat": -21.0594, "lon":  55.2242, "mandatory": True},
    {"name": "Europa (TAAF)",                           "lat": -22.3635, "lon":  40.3476, "mandatory": True},
    {"name": "La Rochelle (retour)",                    "lat":  46.1591, "lon":  -1.1520, "mandatory": True},
]

# ── Logging ───────────────────────────────────────────────────────────────────
# Resolve logs/ relative to project root (works on any machine)
LOG_DIR = Path(os.environ.get(
    "NAVIGUIDE_LOG_DIR",
    str(Path(__file__).resolve().parents[2] / "logs")
))
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    handlers=[
        logging.FileHandler(LOG_DIR / "orchestrator.log"),
        logging.StreamHandler(),
    ],
    format='{"time":"%(asctime)s","level":"%(levelname)s","msg":"%(message)s"}',
)
log = logging.getLogger("orchestrator")

# ── FastAPI app ───────────────────────────────────────────────────────────────
app = FastAPI(
    title="NAVIGUIDE — Multi-Agent Orchestrator",
    description=(
        "LangGraph orchestrator coordinating Agent 1 (Route Intelligence) "
        "→ Agent 3 (Risk Assessment) into a unified expedition digital twin."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Compile orchestrator graph once at startup
orchestrator = build_orchestrator()
log.info("Multi-Agent Orchestrator compiled and ready.")


# ── Pydantic models ───────────────────────────────────────────────────────────

class WaypointIn(BaseModel):
    name:          str
    lat:           float
    lon:           float
    mandatory:     bool = True
    skip_maritime: bool = False


class ExpeditionRequestIn(BaseModel):
    waypoints:     List[WaypointIn]
    vessel_specs:  Dict[str, Any] = {}
    constraints:   Dict[str, Any] = {}
    expedition_id: Optional[str]  = None  # links to polar data for real VMG-based ETAs


# ── Helper: build initial OrchestratorState ───────────────────────────────────

def _initial_state(
    waypoints, vessel_specs, constraints, language="en", expedition_id=None
) -> OrchestratorState:
    return {
        "waypoints":             waypoints,
        "vessel_specs":          vessel_specs or BerryMappemondeRouter.VESSEL_PROFILE,
        "constraints":           constraints,
        "expedition_id":         expedition_id,
        "agent1_status":         "pending",
        "agent1_errors":         [],
        "route_plan":            {},
        "anti_shipping_avg":     0.0,
        "polar_vmg":             None,
        "polar_avg_speed":       None,
        "total_eta_days":        None,
        "agent3_status":         "pending",
        "agent3_errors":         [],
        "risk_report":           {},
        "expedition_risk_level": "UNKNOWN",
        "expedition_plan":       {},
        "executive_briefing":    "",
        "messages":              [],
        "errors":                [],
        "status":                "init",
        "language":              language if language in ("en", "fr") else "en",
        "chat_id":               None,
        "access_token":          None,
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/")
def health():
    return {
        "service":      "NAVIGUIDE Multi-Agent Orchestrator",
        "version":      "1.0.0",
        "status":       "operational",
        "framework":    "LangGraph",
        "agents": {
            "agent1": "Route Intelligence (searoute + anti-shipping + safety)",
            "agent3": "Risk Assessment (weather + piracy + medical + cyclone)",
        },
        "pipeline":     "Agent1 → Agent3 → LLM Briefing → Expedition Digital Twin",
    }


@app.post("/api/v1/expedition/plan")
async def plan_expedition(request: ExpeditionRequestIn):
    """
    Full multi-agent expedition planning pipeline for a custom waypoint set.

    Runs:
      1. Agent 1 — maritime route with anti-shipping scoring
      2. Agent 3 — risk assessment across weather/piracy/medical/cyclone
      3. LLM — unified executive skipper briefing
      4. Merger — combined GeoJSON digital twin

    Returns the complete expedition_plan with all sub-agent outputs.
    """
    log.info(f"Expedition plan request: {len(request.waypoints)} waypoints")

    state = _initial_state(
        waypoints     = [wp.dict() for wp in request.waypoints],
        vessel_specs  = request.vessel_specs,
        constraints   = request.constraints,
        expedition_id = request.expedition_id,
    )

    try:
        result  = orchestrator.invoke(state)
        vs      = result.get("expedition_plan", {}).get("voyage_statistics", {})
        log.info(f"Expedition plan complete: status={result['status']}, risk={result['expedition_risk_level']}")
        return {
            "status":           result["status"],
            "expedition_plan":  result["expedition_plan"],
            "errors":           result.get("errors", []),
            "summary": {
                "agent1_status":         result.get("agent1_status"),
                "agent3_status":         result.get("agent3_status"),
                "total_distance_nm":     vs.get("total_distance_nm"),
                "total_eta_days":        vs.get("total_eta_days"),
                "polar_avg_speed_knots": vs.get("polar_avg_speed_knots"),
                "polar_data_used":       vs.get("polar_data_used", False),
                "anti_shipping_avg":     result.get("anti_shipping_avg"),
                "expedition_risk_level": result.get("expedition_risk_level"),
                "critical_alerts_count": len(result.get("expedition_plan", {}).get("critical_alerts", [])),
            },
        }
    except Exception as exc:
        log.error(f"Orchestrator error: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


class BerryPlanRequest(BaseModel):
    language:        Optional[str] = "en"
    departure_month: Optional[int] = None
    expedition_id:   Optional[str] = "berry-mappemonde-2026"  # default polar dataset


@app.post("/api/v1/expedition/plan/berry-mappemonde")
async def plan_berry_mappemonde(body: BerryPlanRequest = None):
    """
    Pre-configured Berry-Mappemonde circumnavigation expedition plan.
    Accepts JSON body with optional `language` ("en"|"fr") and `departure_month` (1-12).
    """
    language       = (body.language       if body and body.language       else "en")
    departure_month = (body.departure_month if body and body.departure_month else None)
    log.info(f"Berry-Mappemonde plan requested. language={language} departure_month={departure_month}")

    constraints = {
        "mandatory_cape_of_good_hope": True,
        "no_suez_canal":               True,
        "east_to_west_atlantic":       True,
        "spm_decoupled_leg":           True,
    }
    if departure_month:
        constraints["departure_month"] = departure_month

    state = _initial_state(
        waypoints     = BERRY_MAPPEMONDE_WAYPOINTS,
        vessel_specs  = BerryMappemondeRouter.VESSEL_PROFILE,
        constraints   = constraints,
        language      = language,
        expedition_id = body.expedition_id if body else "berry-mappemonde-2026",
    )

    try:
        result = orchestrator.invoke(state)
        log.info(f"Berry-Mappemonde complete: status={result['status']}, risk={result['expedition_risk_level']}")
        return {
            "status":          result["status"],
            "expedition_plan": result["expedition_plan"],
            "errors":          result.get("errors", []),
        }
    except Exception as exc:
        log.error(f"Berry-Mappemonde orchestrator error: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/v1/expedition/graph")
def get_orchestration_diagram():
    """ASCII representation of the full multi-agent orchestration workflow."""
    diagram = """
    NAVIGUIDE — Multi-Agent Orchestrator (LangGraph)
    ══════════════════════════════════════════════════════════════

    [START]
       │
       ▼
    ┌──────────────────────────────────────┐
    │  validate_expedition_request         │  Coordinate / bounds check
    └────────────────┬─────────────────────┘
                     │ error ────────────────────────────────► [END]
                     ▼
    ┌──────────────────────────────────────┐
    │  run_route_intelligence              │  ← AGENT 1 subgraph
    │  ┌────────────────────────────────┐  │    parse_route
    │  │  searoute + anti-shipping      │  │  → compute_segments
    │  │  coastal buffer validation     │  │  → apply_anti_shipping
    │  │  LLM route advisory            │  │  → validate_safety
    │  └────────────────────────────────┘  │  → llm_route_advisor
    └────────────────┬─────────────────────┘  → generate_route_plan
                     │ agent1_failed ─────────────────────────► [END]
                     ▼
    ┌──────────────────────────────────────┐
    │  run_risk_assessment                 │  ← AGENT 3 subgraph
    │  ┌────────────────────────────────┐  │    parse_risk_request
    │  │  weather windows               │  │  → assess_weather_risks
    │  │  piracy zones (IMB/MDAT)       │  │  → assess_piracy_zones
    │  │  medical access                │  │  → assess_medical_safety
    │  │  cyclone basins (NHC/RSMC)     │  │  → assess_cyclone_risks
    │  └────────────────────────────────┘  │  → compute_risk_scores
    └────────────────┬─────────────────────┘  → llm_risk_analyst
                     ▼                         → generate_risk_report
    ┌──────────────────────────────────────┐
    │  llm_expedition_briefing             │  Deploy AI (GPT-4o) combined briefing
    └────────────────┬─────────────────────┘
                     ▼
    ┌──────────────────────────────────────┐
    │  generate_expedition_plan            │  Merged GeoJSON Digital Twin
    │  • Route features (Agent 1)          │  + risk overlays (Agent 3)
    │  • Risk point overlays (Agent 3)     │  + executive briefing
    │  • Voyage statistics                 │  + critical alerts
    └────────────────┬─────────────────────┘
                     ▼
                   [END]

    Output: unified expedition_plan JSON with:
      ├── executive_briefing    (LLM-generated skipper briefing)
      ├── voyage_statistics     (distance, risk level, scores)
      ├── critical_alerts       (HIGH/CRITICAL stops)
      ├── unified_geojson       (route + risk overlays merged)
      ├── full_route_intelligence  (Agent 1 complete output)
      └── full_risk_assessment     (Agent 3 complete output)
    """
    return {"diagram": diagram}


@app.get("/api/v1/expedition/status")
def get_agent_status():
    """Check availability of all sub-agents."""
    return {
        "orchestrator": "operational",
        "agent1_route_intelligence": {
            "module":       "naviguide_agent1",
            "capabilities": ["maritime_routing", "anti_shipping", "safety_validation"],
        },
        "agent3_risk_assessment": {
            "module":       "naviguide_agent3",
            "capabilities": ["weather_windows", "piracy_zones", "medical_access", "cyclone_exposure"],
        },
        "integration_mode": "direct_subgraph_invocation",
    }


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.getenv("PORT", 3008))
    uvicorn.run("naviguide_orchestrator.main:app", host="0.0.0.0", port=port, reload=False)
