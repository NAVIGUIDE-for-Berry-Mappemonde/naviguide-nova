"""
Orchestrator – Agent Coordinator + LLM Briefing (Port 8002)
Berry-Mappemonde Multi-Agent System

Workflow:
  1. Receive expedition planning request
  2. Call Agent 1 (Route Intelligence) for anti-shipping route
  3. Call Agent 3 (Risk Assessment) for risk matrix
  4. Generate a natural-language expedition briefing via Deploy AI (GPT_4O)
  5. Return a comprehensive expedition package (route + risks + briefing)

Deploy AI is optional: if credentials are not set the briefing falls back
to a structured static summary so all other features still work.
"""
import os
import logging
import httpx
from datetime import datetime
from typing import Optional, List
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

# Berry-Mappemonde waypoints (15 mandatory French-territory stopovers)
BERRY_MAPPEMONDE_WAYPOINTS = [
    {"id": "LEG_00", "name": "Saint-Maur-des-Fossés",  "lat":  48.794, "lon":  2.485,  "note": "Départ"},
    {"id": "LEG_01", "name": "Pointe-à-Pitre",          "lat":  16.241, "lon": -61.533, "note": "Guadeloupe – 1re escale"},
    {"id": "LEG_02", "name": "Fort-de-France",           "lat":  14.608, "lon": -61.074, "note": "Martinique"},
    {"id": "LEG_03", "name": "Cayenne",                  "lat":   4.932, "lon": -52.330, "note": "Guyane"},
    {"id": "LEG_04", "name": "Saint-Pierre",             "lat":  46.781, "lon": -56.182, "note": "Saint-Pierre-et-Miquelon (air-boat)"},
    {"id": "LEG_05", "name": "Marigot",                  "lat":  18.068, "lon": -63.080, "note": "Saint-Martin"},
    {"id": "LEG_06", "name": "Gustavia",                 "lat":  17.896, "lon": -62.850, "note": "Saint-Barthélemy"},
    {"id": "LEG_07", "name": "Panama City",              "lat":   8.994, "lon": -79.519, "note": "Transit Canal de Panama"},
    {"id": "LEG_08", "name": "Papeete",                  "lat": -17.535, "lon":-149.570, "note": "Polynésie française"},
    {"id": "LEG_09", "name": "Nouméa",                   "lat": -22.268, "lon": 166.453, "note": "Nouvelle-Calédonie"},
    {"id": "LEG_10", "name": "Mamoudzou",                "lat": -12.782, "lon":  45.228, "note": "Mayotte"},
    {"id": "LEG_11", "name": "Saint-Denis de La Réunion","lat": -20.882, "lon":  55.450, "note": "La Réunion"},
    {"id": "LEG_12", "name": "Port Louis",               "lat": -20.162, "lon":  57.499, "note": "Île Maurice (escale technique)"},
    {"id": "LEG_13", "name": "Cape of Good Hope",        "lat": -34.357, "lon":  18.474, "note": "Cap de Bonne-Espérance"},
    {"id": "LEG_14", "name": "Ajaccio",                  "lat":  41.919, "lon":   8.738, "note": "Corse – Arrivée"},
]

logging.basicConfig(level=logging.INFO, format="%(asctime)s [Orchestrator] %(message)s")
log = logging.getLogger(__name__)

AGENT1_URL = os.getenv("AGENT1_URL", "http://localhost:8001")
AGENT3_URL = os.getenv("AGENT3_URL", "http://localhost:8003")

# Deploy AI settings
AUTH_URL   = "https://api-auth.dev.deploy.ai/oauth2/token"
API_URL    = "https://core-api.dev.deploy.ai"
CLIENT_ID     = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
ORG_ID        = os.getenv("ORG_ID", "f3e01a12-b6aa-43ac-83bc-d0014e215eed")

app = FastAPI(
    title="Orchestrator – NAVIGUIDE Berry-Mappemonde",
    description="Coordinates Route Intelligence + Risk Assessment agents and generates LLM expedition briefings.",
    version="1.0.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Deploy AI helpers
# ---------------------------------------------------------------------------

def _get_access_token() -> Optional[str]:
    if not CLIENT_ID or not CLIENT_SECRET:
        return None
    try:
        resp = httpx.post(AUTH_URL, data={
            "grant_type": "client_credentials",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        }, timeout=10)
        if resp.status_code == 200:
            return resp.json()["access_token"]
    except Exception as exc:
        log.warning("Deploy AI auth failed: %s", exc)
    return None


def _create_chat(token: str) -> Optional[str]:
    try:
        resp = httpx.post(f"{API_URL}/chats",
            headers={"Authorization": f"Bearer {token}", "X-Org": ORG_ID,
                     "Content-Type": "application/json"},
            json={"agentId": "GPT_4O", "stream": False},
            timeout=15,
        )
        if resp.status_code == 200:
            return resp.json()["id"]
    except Exception as exc:
        log.warning("Deploy AI chat creation failed: %s", exc)
    return None


def _call_agent(token: str, chat_id: str, question: str) -> Optional[str]:
    try:
        resp = httpx.post(f"{API_URL}/messages",
            headers={"Authorization": f"Bearer {token}", "X-Org": ORG_ID,
                     "Content-Type": "application/json"},
            json={"chatId": chat_id, "stream": False,
                  "content": [{"type": "text", "value": question}]},
            timeout=30,
        )
        if resp.status_code == 200:
            return resp.json()["content"][0]["value"]
    except Exception as exc:
        log.warning("Deploy AI message failed: %s", exc)
    return None


def generate_llm_briefing(route_data: dict, risk_data: dict, waypoints: list) -> dict:
    """Generate a briefing via Deploy AI; fall back to a static summary."""
    token = _get_access_token()
    if not token:
        log.info("No Deploy AI credentials – using static briefing")
        return _static_briefing(route_data, risk_data)

    chat_id = _create_chat(token)
    if not chat_id:
        return _static_briefing(route_data, risk_data)

    risk_level = risk_data.get("overall_risk_level", "UNKNOWN")
    dist = route_data.get("properties", {}).get("distance_nm", "N/A")
    score = route_data.get("properties", {}).get("anti_shipping_score", "N/A")

    prompt = (
        "You are a professional maritime expedition planner. "
        "Generate a concise French-language expedition briefing for the Berry-Mappemonde voyage "
        f"covering {len(waypoints)} stopovers across French overseas territories.\n\n"
        f"Route stats: {dist} nm total, anti-shipping score {score}/1.0\n"
        f"Overall risk level: {risk_level}\n"
        f"Waypoints: {', '.join(w['name'] for w in waypoints)}\n\n"
        "Provide: (1) Executive summary (3 sentences), "
        "(2) 3 key recommendations, "
        "(3) Critical risk alerts, "
        "(4) Best departure window."
    )

    text = _call_agent(token, chat_id, prompt)
    if text:
        log.info("LLM briefing generated via Deploy AI (%d chars)", len(text))
        return {"source": "deploy_ai", "model": "GPT_4O", "content": text}

    return _static_briefing(route_data, risk_data)


def _static_briefing(route_data: dict, risk_data: dict) -> dict:
    dist  = route_data.get("properties", {}).get("distance_nm", "~25 000")
    score = route_data.get("properties", {}).get("anti_shipping_score", 0)
    risk  = risk_data.get("overall_risk_level", "MEDIUM")
    return {
        "source": "static",
        "content": (
            f"## Expédition Berry-Mappemonde — Briefing Synthétique\n\n"
            f"**Distance totale** : {dist} nm | **Score anti-shipping** : {score:.2f}/1.0 | "
            f"**Niveau de risque global** : {risk}\n\n"
            "### Recommandations clés\n"
            "1. Traversée Atlantique en novembre–décembre pour profiter des alizés de NE.\n"
            "2. Passage du Cap de Bonne-Espérance hors de la saison des dépressions (avril–juin).\n"
            "3. Séjour en Polynésie entre mai et octobre pour éviter la saison cyclonique.\n\n"
            "### Alertes de risque\n"
            "- Zones de piraterie : Golfe de Guinée, Corne de l'Afrique, Détroit de Malacca.\n"
            "- Cyclones : Atlantique Nord (juin–nov), Pacifique Sud (nov–avril).\n\n"
            "### Fenêtre de départ recommandée\n"
            "Octobre–novembre depuis Saint-Maur pour synchroniser toutes les fenêtres météo."
        ),
    }


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class ExpeditionRequest(BaseModel):
    waypoints: Optional[List[dict]] = None  # override default Berry-Mappemonde list
    month: Optional[int] = None             # departure month (1-12)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/")
async def root():
    return {
        "service": "Orchestrator",
        "version": "1.0.0",
        "port": 8002,
        "status": "coordinating",
        "agents": {
            "agent1_route_intelligence": AGENT1_URL,
            "agent3_risk_assessment": AGENT3_URL,
        },
        "llm": "deploy_ai" if (CLIENT_ID and CLIENT_SECRET) else "static_fallback",
    }


@app.get("/health")
async def health():
    results = {"orchestrator": "ok"}
    for name, url in [("agent1", AGENT1_URL), ("agent3", AGENT3_URL)]:
        try:
            r = httpx.get(f"{url}/", timeout=3)
            results[name] = "ok" if r.status_code == 200 else f"http_{r.status_code}"
        except Exception:
            results[name] = "unreachable"
    return results


@app.post("/berry-mappemonde-expedition")
async def plan_expedition(req: ExpeditionRequest = None):
    """
    Full Berry-Mappemonde expedition planning pipeline.
    Orchestrates Agent 1 + Agent 3 and returns a complete expedition package.
    """
    if req is None:
        req = ExpeditionRequest()

    waypoints = req.waypoints or BERRY_MAPPEMONDE_WAYPOINTS
    month = req.month or datetime.utcnow().month

    log.info("Planning expedition for %d waypoints, departure month %d", len(waypoints), month)

    # --- Step 1: Route Intelligence (Agent 1) ---
    route_segments = []
    total_nm = 0.0
    all_coords = []

    async with httpx.AsyncClient(timeout=30) as client:
        for i in range(len(waypoints) - 1):
            src = waypoints[i]
            dst = waypoints[i + 1]
            try:
                r = await client.get(
                    f"{AGENT1_URL}/anti-shipping-route",
                    params={
                        "start_lat": src["lat"], "start_lon": src["lon"],
                        "end_lat":   dst["lat"], "end_lon":   dst["lon"],
                    },
                )
                seg = r.json() if r.status_code == 200 else {"error": f"Agent1 HTTP {r.status_code}"}
                props = seg.get("properties", {})
                nm = props.get("distance_nm", 0)
                total_nm += nm
                coords = seg.get("geometry", {}).get("coordinates", [])
                all_coords.extend(coords)
                route_segments.append({
                    "leg": f"{src['name']} → {dst['name']}",
                    "distance_nm": nm,
                    "anti_shipping_score": props.get("anti_shipping_score"),
                    "alerts": len(props.get("shipping_lane_alerts", [])),
                })
            except Exception as exc:
                log.warning("Agent1 error on leg %d: %s", i, exc)
                route_segments.append({"leg": f"{src['name']} → {dst['name']}", "error": str(exc)})

    route_summary = {
        "total_distance_nm": round(total_nm, 1),
        "estimated_duration_days": round(total_nm / (6 * 24), 1),
        "segments": route_segments,
        "properties": {
            "distance_nm": round(total_nm, 1),
            "anti_shipping_score": round(
                sum(s.get("anti_shipping_score") or 0 for s in route_segments) /
                max(1, sum(1 for s in route_segments if s.get("anti_shipping_score"))), 3
            ),
        },
    }

    # --- Step 2: Risk Assessment (Agent 3) ---
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            risk_resp = await client.post(
                f"{AGENT3_URL}/assess-route",
                json={"coordinates": all_coords, "month": month},
            )
            risk_data = risk_resp.json() if risk_resp.status_code == 200 else {
                "error": f"Agent3 HTTP {risk_resp.status_code}",
                "overall_risk_level": "UNKNOWN",
            }
        except Exception as exc:
            log.warning("Agent3 error: %s", exc)
            risk_data = {"error": str(exc), "overall_risk_level": "UNKNOWN"}

    # --- Step 3: LLM Briefing (Deploy AI / static fallback) ---
    briefing = generate_llm_briefing(route_summary, risk_data, waypoints)

    return {
        "expedition": "Berry-Mappemonde",
        "planned_at": datetime.utcnow().isoformat() + "Z",
        "departure_month": month,
        "waypoints_count": len(waypoints),
        "waypoints": [{"id": w.get("id"), "name": w["name"], "note": w.get("note")} for w in waypoints],
        "route_intelligence": route_summary,
        "risk_assessment": risk_data,
        "expedition_briefing": briefing,
        "status": "complete",
    }


@app.get("/expedition/waypoints")
async def get_waypoints():
    """Return the standard Berry-Mappemonde waypoint list."""
    return {"expedition": "Berry-Mappemonde", "waypoints": BERRY_MAPPEMONDE_WAYPOINTS}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8002)
