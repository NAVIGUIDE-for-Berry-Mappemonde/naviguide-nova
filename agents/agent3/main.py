"""
Agent 3 – Risk Assessment (Port 8003)
Berry-Mappemonde Multi-Agent System

Evaluates four risk categories for any maritime route:
  • Weather risk   – seasonal storm / gale probability
  • Piracy risk    – proximity to known high-risk zones (IMB data)
  • Cyclone risk   – basin + season analysis
  • Medical risk   – distance from emergency medical facilities

Returns a per-segment and overall risk matrix in JSON.
"""
import math
import logging
from datetime import date, datetime
from typing import List, Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

logging.basicConfig(level=logging.INFO, format="%(asctime)s [Agent3] %(message)s")
log = logging.getLogger(__name__)

app = FastAPI(
    title="Agent 3 – Risk Assessment",
    description="Weather, piracy, cyclone and medical risk for Berry-Mappemonde.",
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
# Risk Zone Databases
# ---------------------------------------------------------------------------

PIRACY_ZONES = [
    {"name": "Gulf of Guinea",            "lat": (-2.0, 10.0),  "lon": ( 2.0,  9.0), "level": "HIGH"},
    {"name": "Horn of Africa / Gulf Aden","lat": (10.0, 16.0),  "lon": (43.0, 55.0), "level": "HIGH"},
    {"name": "Strait of Malacca",         "lat": ( 1.0,  6.0),  "lon": (99.0,104.0), "level": "MEDIUM"},
    {"name": "Mozambique Channel",        "lat": (-25.0,-10.0), "lon": (34.0, 43.0), "level": "MEDIUM"},
    {"name": "W African Coast",           "lat": ( 4.0, 14.0),  "lon": (-18.0,-10.0),"level": "LOW"},
    {"name": "Sulu-Celebes Seas",         "lat": ( 3.0,  9.0),  "lon": (117.0,126.0),"level": "MEDIUM"},
    {"name": "Venezuela–Guyana Coast",    "lat": ( 6.0, 12.0),  "lon": (-65.0,-52.0),"level": "LOW"},
]

# Cyclone basins with peak months (1-based)
CYCLONE_BASINS = [
    {"name": "North Atlantic",            "lat": ( 8.0, 35.0), "lon": (-100.0,-15.0), "peak_months": [8, 9, 10], "season_months": list(range(6, 12))},
    {"name": "Eastern Pacific",           "lat": ( 8.0, 25.0), "lon": (-140.0,-85.0), "peak_months": [8, 9],     "season_months": list(range(5, 12))},
    {"name": "Western Pacific",           "lat": ( 5.0, 35.0), "lon": (100.0, 180.0), "peak_months": [8, 9, 10], "season_months": list(range(1, 13))},
    {"name": "North Indian Ocean",        "lat": ( 5.0, 25.0), "lon": (45.0,  100.0), "peak_months": [10, 11],   "season_months": [4, 5, 10, 11, 12]},
    {"name": "South Indian Ocean",        "lat": (-30.0,-5.0), "lon": (25.0,  100.0), "peak_months": [1, 2],     "season_months": list(range(11, 13)) + list(range(1, 5))},
    {"name": "Australian Region",         "lat": (-30.0,-8.0), "lon": (100.0, 160.0), "peak_months": [1, 2, 3],  "season_months": list(range(11, 13)) + list(range(1, 5))},
    {"name": "South Pacific",             "lat": (-30.0,-8.0), "lon": (155.0, 220.0), "peak_months": [2, 3],     "season_months": list(range(11, 13)) + list(range(1, 5))},
]

# Major medical evacuation hubs (port / city + coords)
MEDICAL_HUBS = [
    {"name": "Le Havre (FR)",         "lat": 49.49,  "lon":  0.11},
    {"name": "Brest (FR)",            "lat": 48.39,  "lon": -4.49},
    {"name": "Pointe-à-Pitre (GP)",   "lat": 16.24,  "lon":-61.53},
    {"name": "Fort-de-France (MQ)",   "lat": 14.61,  "lon":-61.07},
    {"name": "Saint-Denis (RE)",      "lat": -20.88, "lon": 55.45},
    {"name": "Mamoudzou (YT)",        "lat": -12.78, "lon": 45.23},
    {"name": "Nouméa (NC)",           "lat": -22.27, "lon":166.45},
    {"name": "Papeete (PF)",          "lat": -17.54, "lon":-149.56},
    {"name": "Cayenne (GF)",          "lat":  4.93,  "lon":-52.33},
    {"name": "Cape Town (ZA)",        "lat": -33.93, "lon":  18.42},
    {"name": "Singapore (SG)",        "lat":  1.29,  "lon": 103.85},
    {"name": "Sydney (AU)",           "lat": -33.87, "lon": 151.21},
    {"name": "Ajaccio (FR-Corse)",    "lat": 41.92,  "lon":   8.74},
    {"name": "Saint-Pierre (SPM)",    "lat": 46.78,  "lon":-56.18},
    {"name": "Las Palmas (ES)",       "lat": 28.10,  "lon":-15.42},
]

# Seasonal gale probability by latitude band and month (rough model)
def _gale_probability(lat: float, month: int) -> float:
    """Return estimated gale probability 0–1 for a given lat/month."""
    # Roaring Forties / Southern Ocean
    if lat < -40:
        return 0.55 if month in [6, 7, 8] else 0.40
    if -40 <= lat < -30:
        return 0.25
    # North Atlantic / North Pacific winter
    if 40 <= lat <= 60:
        return 0.35 if month in [11, 12, 1, 2, 3] else 0.10
    # Trade wind belt
    if 5 <= lat <= 25:
        return 0.05
    return 0.12


def _haversine(lat1, lon1, lat2, lon2) -> float:
    """Distance in nautical miles between two lat/lon points."""
    R = 3440.065  # nm
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _in_box(lat, lon, lat_range, lon_range) -> bool:
    return lat_range[0] <= lat <= lat_range[1] and lon_range[0] <= lon <= lon_range[1]


def _level_to_score(level: str) -> float:
    return {"LOW": 0.25, "MEDIUM": 0.55, "HIGH": 0.85, "CRITICAL": 1.0}.get(level, 0.0)


def _score_to_level(score: float) -> str:
    if score >= 0.75:
        return "HIGH"
    if score >= 0.45:
        return "MEDIUM"
    if score >= 0.20:
        return "LOW"
    return "MINIMAL"


# ---------------------------------------------------------------------------
# Risk computation helpers
# ---------------------------------------------------------------------------

def assess_piracy(lat: float, lon: float) -> dict:
    for zone in PIRACY_ZONES:
        if _in_box(lat, lon, zone["lat"], zone["lon"]):
            return {"level": zone["level"], "score": _level_to_score(zone["level"]), "zone": zone["name"]}
    return {"level": "MINIMAL", "score": 0.0, "zone": None}


def assess_cyclone(lat: float, lon: float, month: int) -> dict:
    best = {"level": "MINIMAL", "score": 0.0, "basin": None, "peak": False}
    for basin in CYCLONE_BASINS:
        if _in_box(lat, lon, basin["lat"], basin["lon"]):
            if month in basin["season_months"]:
                peak = month in basin["peak_months"]
                score = 0.80 if peak else 0.45
                if score > best["score"]:
                    best = {
                        "level": _score_to_level(score),
                        "score": score,
                        "basin": basin["name"],
                        "peak": peak,
                    }
    return best


def assess_weather(lat: float, lon: float, month: int) -> dict:
    prob = _gale_probability(lat, month)
    return {"level": _score_to_level(prob), "score": round(prob, 2), "gale_probability": prob}


def assess_medical(lat: float, lon: float) -> dict:
    distances = [
        {"hub": h["name"], "distance_nm": round(_haversine(lat, lon, h["lat"], h["lon"]), 0)}
        for h in MEDICAL_HUBS
    ]
    distances.sort(key=lambda x: x["distance_nm"])
    nearest = distances[0]
    # Risk increases beyond 500 nm from nearest hub
    d = nearest["distance_nm"]
    score = min(1.0, d / 2000)
    return {
        "level": _score_to_level(score),
        "score": round(score, 2),
        "nearest_hub": nearest["hub"],
        "distance_nm": nearest["distance_nm"],
        "top3_hubs": distances[:3],
    }


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class PointAssessRequest(BaseModel):
    latitude: float
    longitude: float
    month: Optional[int] = None  # 1-12; defaults to current month


class RouteAssessRequest(BaseModel):
    coordinates: List[List[float]]  # [[lon, lat], ...]
    month: Optional[int] = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/")
async def root():
    return {"agent": "Risk Assessment", "version": "1.0.0", "port": 8003, "status": "active"}


@app.post("/assess-risks")
async def assess_risks(req: dict):
    """
    Assess risks for a single point or a full route.
    Accepts: { latitude, longitude, month? }
    or       { route_data: {...GeoJSON...}, month? }
    """
    month = req.get("month") or datetime.utcnow().month
    lat = req.get("latitude") or req.get("lat")
    lon = req.get("longitude") or req.get("lon")

    if lat is None or lon is None:
        # Try to extract centroid from GeoJSON coordinates
        coords = req.get("coordinates") or []
        if coords:
            lat = sum(c[1] for c in coords) / len(coords)
            lon = sum(c[0] for c in coords) / len(coords)
        else:
            raise HTTPException(status_code=422, detail="Provide latitude/longitude or coordinates")

    piracy  = assess_piracy(lat, lon)
    cyclone = assess_cyclone(lat, lon, month)
    weather = assess_weather(lat, lon, month)
    medical = assess_medical(lat, lon)

    overall_score = round(
        piracy["score"]  * 0.30 +
        cyclone["score"] * 0.30 +
        weather["score"] * 0.20 +
        medical["score"] * 0.20,
        3,
    )

    return {
        "agent": "Risk Assessment",
        "assessed_at": datetime.utcnow().isoformat() + "Z",
        "month": month,
        "position": {"latitude": lat, "longitude": lon},
        "risk_matrix": {
            "weather": weather,
            "piracy":  piracy,
            "cyclone": cyclone,
            "medical": medical,
        },
        "overall_risk_score": overall_score,
        "overall_risk_level": _score_to_level(overall_score),
        "recommendations": _build_recommendations(piracy, cyclone, weather, medical),
    }


@app.post("/assess-route")
async def assess_route(req: RouteAssessRequest):
    """Full route risk assessment – returns per-segment and aggregate risk."""
    month = req.month or datetime.utcnow().month
    coords = req.coordinates
    if not coords:
        raise HTTPException(status_code=422, detail="coordinates list is empty")

    segments = []
    for lon, lat in coords[::max(1, len(coords) // 20)]:  # sample up to 20 points
        piracy  = assess_piracy(lat, lon)
        cyclone = assess_cyclone(lat, lon, month)
        weather = assess_weather(lat, lon, month)
        medical = assess_medical(lat, lon)
        overall = round(
            piracy["score"] * 0.30 + cyclone["score"] * 0.30 +
            weather["score"] * 0.20 + medical["score"] * 0.20, 3,
        )
        segments.append({
            "position": [lon, lat],
            "overall_score": overall,
            "overall_level": _score_to_level(overall),
            "piracy": piracy["level"],
            "cyclone": cyclone["level"],
            "weather": weather["level"],
            "medical": medical["level"],
        })

    max_score  = max(s["overall_score"] for s in segments)
    mean_score = round(sum(s["overall_score"] for s in segments) / len(segments), 3)
    high_risk  = [s for s in segments if s["overall_score"] >= 0.45]

    return {
        "agent": "Risk Assessment",
        "assessed_at": datetime.utcnow().isoformat() + "Z",
        "month": month,
        "segments_assessed": len(segments),
        "overall_risk_score": mean_score,
        "peak_risk_score": max_score,
        "overall_risk_level": _score_to_level(mean_score),
        "high_risk_segments": high_risk,
        "segment_detail": segments,
    }


def _build_recommendations(piracy, cyclone, weather, medical) -> list:
    recs = []
    if piracy["score"] >= 0.55:
        recs.append(f"⚠️  Piracy alert – {piracy['zone']}. Register with MSCHOA; keep watch schedule.")
    if cyclone["score"] >= 0.45:
        prefix = "🌀  Peak cyclone season" if cyclone.get("peak") else "🌀  Cyclone season active"
        recs.append(f"{prefix} – {cyclone['basin']}. Depart before or after season window.")
    if weather["score"] >= 0.35:
        recs.append("🌬️  Elevated gale probability. Monitor GRIB forecasts; reef early.")
    if medical["distance_nm"] and medical["distance_nm"] > 800:
        recs.append(f"🏥  Nearest medical hub {medical['nearest_hub']} is {medical['distance_nm']:.0f} nm away. Carry comprehensive medical kit; satellite comms mandatory.")
    if not recs:
        recs.append("✅  All risk categories within acceptable limits for offshore passage.")
    return recs


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8003)
