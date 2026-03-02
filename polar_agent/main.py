"""
NAVIGUIDE — Agent 1: Route Intelligence Agent
FastAPI entry point

Endpoints
─────────
GET  /                              Health check
POST /api/v1/agent/route            Custom waypoint route computation
POST /api/v1/agent/route/berry-mappemonde  Pre-configured expedition route
GET  /api/v1/agent/route/graph      LangGraph diagram (ASCII)
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .graph       import build_route_intelligence_agent
from .router      import BerryMappemondeRouter
from .geojson_data import BERRY_MAPPEMONDE_WAYPOINTS
from .state       import RouteState

# ── Logging ───────────────────────────────────────────────────────────────────

LOG_DIR = Path(
    "/mnt/efs/spaces/ef014a98-8a1c-4b16-8e06-5d2c5b364d08"
    "/62965cb5-fd5b-4d1c-b1c4-54766d3e1e9e/logs"
)
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    handlers=[
        logging.FileHandler(LOG_DIR / "agent1.log"),
        logging.StreamHandler(),
    ],
    format='{"time":"%(asctime)s","level":"%(levelname)s","msg":"%(message)s"}',
)
log = logging.getLogger("agent1")

# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="NAVIGUIDE — Route Intelligence Agent",
    description=(
        "LangGraph-powered maritime routing agent with anti-shipping logic, "
        "safety validation, and Deploy AI advisory."
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

# Compile the agent graph once at startup
agent_graph = build_route_intelligence_agent()
log.info("Route Intelligence Agent graph compiled successfully.")

# ── Pydantic models ───────────────────────────────────────────────────────────

class WaypointIn(BaseModel):
    name:           str
    lat:            float
    lon:            float
    mandatory:      bool = True
    skip_maritime:  bool = False    # True = decoupled / air-travel leg


class RouteRequestIn(BaseModel):
    waypoints:    List[WaypointIn]
    vessel_specs: Dict[str, Any]   = {}
    constraints:  Dict[str, Any]   = {}


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/")
def health():
    return {
        "agent":        "Route Intelligence Agent",
        "version":      "1.0.0",
        "status":       "operational",
        "framework":    "LangGraph",
        "capabilities": [
            "maritime_routing",
            "anti_shipping_cost_function",
            "coastal_buffer_validation",
            "llm_route_advisory",
            "geojson_export",
        ],
    }


@app.post("/api/v1/agent/route")
async def compute_custom_route(request: RouteRequestIn):
    """
    Run the Route Intelligence Agent for a custom multi-waypoint expedition.
    Returns an enriched GeoJSON FeatureCollection.
    """
    log.info(f"Route request: {len(request.waypoints)} waypoints")

    initial_state: RouteState = {
        "waypoints":            [wp.dict() for wp in request.waypoints],
        "vessel_specs":         request.vessel_specs or BerryMappemondeRouter.VESSEL_PROFILE,
        "constraints":          request.constraints,
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
        result = agent_graph.invoke(initial_state)
        log.info(f"Route computed: status={result['status']}")
        return {
            "status":    result["status"],
            "route_plan": result["route_plan"],
            "errors":    result.get("errors", []),
            "summary": {
                "segments":            len(result.get("raw_segments", [])),
                "total_distance_nm":   result["route_plan"].get("metadata", {}).get("total_distance_nm", 0),
                "anti_shipping_avg":   result["route_plan"].get("metadata", {}).get("anti_shipping_avg_score", 0),
                "safety_validations":  result.get("safety_validations", []),
                "route_advisor_notes": result.get("route_advisor_notes", ""),
            },
        }
    except Exception as exc:
        log.error(f"Agent execution error: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/v1/agent/route/berry-mappemonde")
async def compute_berry_mappemonde():
    """
    Pre-configured Berry-Mappemonde expedition route.
    Runs the full Route Intelligence Agent against all 19 official waypoints.
    """
    log.info("Berry-Mappemonde pre-configured route requested.")

    initial_state: RouteState = {
        "waypoints":            BERRY_MAPPEMONDE_WAYPOINTS,
        "vessel_specs":         BerryMappemondeRouter.VESSEL_PROFILE,
        "constraints": {
            "mandatory_cape_of_good_hope": True,
            "no_suez_canal":               True,
            "east_to_west_atlantic":       True,
            "panama_canal_to_pacific":     True,
            "spm_decoupled_leg":           True,
        },
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
        result = agent_graph.invoke(initial_state)
        log.info(f"Berry-Mappemonde route computed: status={result['status']}")
        return {
            "status":     result["status"],
            "route_plan": result["route_plan"],
            "errors":     result.get("errors", []),
        }
    except Exception as exc:
        log.error(f"Berry-Mappemonde agent error: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/v1/agent/route/graph")
def get_graph_diagram():
    """Return an ASCII representation of the LangGraph workflow."""
    diagram = """
    NAVIGUIDE — Route Intelligence Agent (LangGraph)
    ═══════════════════════════════════════════════════

    [START]
       │
       ▼
    ┌─────────────────────────────┐
    │  parse_route                │  Validate waypoint coordinates
    └──────────────┬──────────────┘
                   │ error ──────────────────────► [END]
                   ▼
    ┌─────────────────────────────┐
    │  compute_segments           │  searoute engine + geodesic extension
    └──────────────┬──────────────┘
                   ▼
    ┌─────────────────────────────┐
    │  apply_anti_shipping        │  BerryMappemondeRouter cost function
    │  (log-normalised scoring)   │  1.0 = ideal · 0.0 = shipping lane
    └──────────────┬──────────────┘
                   ▼
    ┌─────────────────────────────┐
    │  validate_safety            │  2 nm coastal buffer · depth check
    └──────────────┬──────────────┘
                   ▼
    ┌─────────────────────────────┐
    │  llm_route_advisor          │  Deploy AI (GPT-4o) route commentary
    └──────────────┬──────────────┘
                   ▼
    ┌─────────────────────────────┐
    │  generate_route_plan        │  Enriched GeoJSON FeatureCollection
    └──────────────┬──────────────┘
                   ▼
                [END]
    """
    return {"diagram": diagram}


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8001))
    uvicorn.run("naviguide_agent1.main:app", host="0.0.0.0", port=port, reload=False)
