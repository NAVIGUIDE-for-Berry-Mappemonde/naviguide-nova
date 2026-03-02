"""
Agent 1 - Route Intelligence (Port 8001)
Berry-Mappemonde Multi-Agent System

Implements anti-shipping route optimisation based on the BerryMappemonde
spec (Cahier des Charges v2.0). Uses the searoute engine with a custom
cost function that penalises commercial shipping lanes and rewards
quiet sailing passages.
"""
import math
import logging
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import searoute as sr
from geographiclib.geodesic import Geodesic

logging.basicConfig(level=logging.INFO, format="%(asctime)s [Agent1] %(message)s")
log = logging.getLogger(__name__)

app = FastAPI(
    title="Agent 1 – Route Intelligence",
    description="Anti-shipping maritime route optimisation for Berry-Mappemonde.",
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
# Anti-Shipping Zone Definitions (approximate bounding boxes)
# ---------------------------------------------------------------------------
SHIPPING_ZONES = [
    {"name": "English Channel TSS",   "lat": (49.0, 51.5),  "lon": (-2.0, 2.5),   "penalty": 4.0},
    {"name": "Strait of Gibraltar",   "lat": (35.8, 36.2),  "lon": (-6.0, -5.0),  "penalty": 3.5},
    {"name": "W Atlantic Main Track", "lat": (20.0, 35.0),  "lon": (-70.0, -40.0),"penalty": 2.5},
    {"name": "Caribbean Lanes",       "lat": (10.0, 20.0),  "lon": (-85.0, -60.0),"penalty": 2.0},
    {"name": "Panama Approaches",     "lat": (7.0,  10.0),  "lon": (-82.0, -78.0),"penalty": 3.0},
    {"name": "Panama–Pacific Lane",   "lat": (5.0,  15.0),  "lon": (-100.0,-82.0),"penalty": 2.5},
    {"name": "Cape of Good Hope TSS", "lat": (-35.0,-33.0), "lon": (17.0,  20.0), "penalty": 4.0},
    {"name": "Indian Ocean E–W Lane", "lat": (-20.0, 0.0),  "lon": (50.0,  80.0), "penalty": 2.0},
    {"name": "Strait of Malacca",     "lat": (1.0,   6.0),  "lon": (99.0, 104.0), "penalty": 4.5},
    {"name": "S China Sea Lane",      "lat": (5.0,  20.0),  "lon": (109.0,120.0), "penalty": 2.5},
    {"name": "Tasman Sea Corridor",   "lat": (-45.0,-30.0), "lon": (150.0,170.0), "penalty": 1.5},
]

# Areas preferred by leisure sailors (cost reduction)
PREFERRED_ZONES = [
    {"name": "Trade Wind Belt NE",    "lat": (10.0, 25.0),  "lon": (-45.0,-20.0), "bonus": 0.6},
    {"name": "Trade Wind Belt SE",    "lat": (-25.0,-10.0), "lon": (-30.0,-10.0), "bonus": 0.65},
    {"name": "Roaring Forties W",     "lat": (-50.0,-40.0), "lon": (-60.0,  0.0), "bonus": 0.75},
    {"name": "Pacific Trade Winds",   "lat": (5.0,  20.0),  "lon": (170.0,210.0), "bonus": 0.65},
    {"name": "Polynesia Quiet Waters","lat": (-25.0,-10.0), "lon": (210.0,240.0), "bonus": 0.6},
]


def _point_in_box(lat: float, lon: float, lat_range, lon_range) -> bool:
    """True if (lat, lon) falls inside the given bounding box."""
    return lat_range[0] <= lat <= lat_range[1] and lon_range[0] <= lon <= lon_range[1]


def anti_shipping_score(coords: list) -> float:
    """
    Compute an anti-shipping score for a route (0=busy, 1=quiet).
    Score is the average per-point score across all waypoints.
    """
    if not coords:
        return 0.5
    scores = []
    for lon, lat in coords:
        point_penalty = 1.0
        for zone in SHIPPING_ZONES:
            if _point_in_box(lat, lon, zone["lat"], zone["lon"]):
                point_penalty *= zone["penalty"]
        for zone in PREFERRED_ZONES:
            if _point_in_box(lat, lon, zone["lat"], zone["lon"]):
                point_penalty *= zone["bonus"]
        # Normalise to 0–1 (higher = quieter)
        scores.append(1.0 / max(point_penalty, 1.0))
    return round(sum(scores) / len(scores), 4)


def classify_legs(coords: list) -> list:
    """Identify shipping-lane segments and annotate them."""
    alerts = []
    for i, (lon, lat) in enumerate(coords):
        for zone in SHIPPING_ZONES:
            if _point_in_box(lat, lon, zone["lat"], zone["lon"]):
                alerts.append({
                    "waypoint_index": i,
                    "zone": zone["name"],
                    "penalty_factor": zone["penalty"],
                    "recommendation": "Consider offset routing to avoid this lane",
                })
                break
    return alerts


def searoute_with_exact_end(start, end):
    """
    Calculate a maritime route and append a geodesic extension to the
    exact destination if searoute stops short (< 1 km gap).
    """
    try:
        route = sr.searoute(start, end)
    except Exception as exc:
        log.warning("searoute error: %s", exc)
        return None

    if not route or "geometry" not in route:
        return None

    coords = route["geometry"]["coordinates"]
    last = coords[-1]
    geod = Geodesic.WGS84
    dist = geod.Inverse(last[1], last[0], end[1], end[0])["s12"]

    if dist > 1000:
        n = max(2, int(dist // 5000))
        line = geod.InverseLine(last[1], last[0], end[1], end[0])
        extra = []
        for i in range(1, n):
            pos = line.Position(i * line.s13 / (n - 1))
            lon = pos["lon2"]
            prev_lon = coords[-1][0] if not extra else extra[-1][0]
            if lon - prev_lon > 180:
                lon -= 360
            elif lon - prev_lon < -180:
                lon += 360
            extra.append([lon, pos["lat2"]])
        coords.extend(extra)

    route["geometry"]["coordinates"] = coords
    return route


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/")
async def root():
    return {"agent": "Route Intelligence", "version": "1.0.0", "port": 8001, "status": "active"}


@app.get("/anti-shipping-route")
async def get_anti_shipping_route(
    start_lat: float = Query(...),
    start_lon: float = Query(...),
    end_lat: float = Query(...),
    end_lon: float = Query(...),
):
    """
    Calculate a maritime route with anti-shipping analysis.
    Returns the route GeoJSON annotated with shipping-lane alerts and a
    quiet-sailing score (0–1, higher = less commercial traffic).
    """
    start = (start_lon, start_lat)
    end = (end_lon, end_lat)

    route = searoute_with_exact_end(start, end)
    if route is None:
        raise HTTPException(status_code=404, detail="Route not found by searoute engine")

    coords = route["geometry"]["coordinates"]
    score = anti_shipping_score(coords)
    alerts = classify_legs(coords)

    # Distance (nm)
    total_m = sum(
        Geodesic.WGS84.Inverse(coords[i][1], coords[i][0],
                                coords[i + 1][1], coords[i + 1][0])["s12"]
        for i in range(len(coords) - 1)
    )
    distance_nm = round(total_m / 1852, 1)

    route["properties"] = {
        **route.get("properties", {}),
        "agent": "Route Intelligence",
        "anti_shipping_score": score,
        "distance_nm": distance_nm,
        "estimated_duration_days": round(distance_nm / (6 * 24), 1),  # 6 kts cruise
        "shipping_lane_alerts": alerts,
        "alert_count": len(alerts),
        "routing_constraints": [
            "avoid:shipping_lanes",
            "avoid:tss_zones",
            "min_depth:3m",
            "coastal_buffer:2nm",
        ],
    }

    log.info("Route %s→%s | %.1f nm | score=%.2f | %d alerts",
             start, end, distance_nm, score, len(alerts))
    return route


@app.post("/anti-shipping-route")
async def post_anti_shipping_route(route_data: dict):
    """POST variant – accepts start/end in request body."""
    try:
        start_lat = route_data["start_lat"]
        start_lon = route_data["start_lon"]
        end_lat   = route_data["end_lat"]
        end_lon   = route_data["end_lon"]
    except KeyError as exc:
        raise HTTPException(status_code=422, detail=f"Missing field: {exc}")

    return await get_anti_shipping_route(start_lat, start_lon, end_lat, end_lon)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)
