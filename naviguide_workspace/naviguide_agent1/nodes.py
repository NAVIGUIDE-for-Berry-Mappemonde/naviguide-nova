"""
NAVIGUIDE Agent 1 — LangGraph Node Functions

Graph flow:
  parse_route
      │ (error) ──► END
      ▼
  compute_segments
      ▼
  apply_anti_shipping
      ▼
  validate_safety
      ▼
  llm_route_advisor
      ▼
  generate_route_plan
      ▼
     END
"""

import math
import os
import urllib.request
import urllib.error
import json
from datetime import datetime
from langchain_core.messages import HumanMessage, AIMessage

try:
    from langchain_aws import ChatBedrock
    _BEDROCK_AVAILABLE = True
except (ImportError, Exception):
    _BEDROCK_AVAILABLE = False

from .state  import RouteState
from .router import BerryMappemondeRouter

# Singleton router — shared across all node invocations
_router = BerryMappemondeRouter()


# ──────────────────────────────────────────────────────────────────────────────
# NODE 1 — parse_route
# ──────────────────────────────────────────────────────────────────────────────

def parse_route_node(state: RouteState) -> RouteState:
    """Validate and normalise incoming waypoints."""
    waypoints = state.get("waypoints", [])
    errors    = []

    if len(waypoints) < 2:
        errors.append("At least 2 waypoints are required to compute a route.")
        return {**state, "status": "error", "errors": errors}

    for i, wp in enumerate(waypoints):
        lat = wp.get("lat", 0)
        lon = wp.get("lon", 0)
        if not (-90  <= lat <= 90):
            errors.append(f"Waypoint {i} '{wp.get('name')}': latitude {lat} out of range")
        if not (-180 <= lon <= 180):
            errors.append(f"Waypoint {i} '{wp.get('name')}': longitude {lon} out of range")

    if errors:
        return {**state, "status": "error", "errors": errors}

    msg = HumanMessage(
        content=(
            f"[parse_route] ✅ {len(waypoints)} waypoints validated. "
            f"Route: {waypoints[0].get('name')} → {waypoints[-1].get('name')}"
        )
    )
    return {**state, "status": "processing", "messages": [msg], "errors": []}


# ──────────────────────────────────────────────────────────────────────────────
# NODE 2 — compute_segments
# ──────────────────────────────────────────────────────────────────────────────

def compute_segments_node(state: RouteState) -> RouteState:
    """Compute maritime route segments using the searoute engine."""
    waypoints = state["waypoints"]
    segments  = []
    errors    = list(state.get("errors", []))

    for i in range(len(waypoints) - 1):
        wp_from = waypoints[i]
        wp_to   = waypoints[i + 1]

        # Skip any waypoints flagged as non-maritime (e.g. SPM air-travel leg)
        if wp_from.get("skip_maritime", False):
            continue

        start   = (wp_from["lon"], wp_from["lat"])
        end     = (wp_to["lon"],   wp_to["lat"])
        segment = _router.compute_segment(start, end)

        if segment:
            raw_len_km = segment.get("properties", {}).get("length", 0) or 0
            segments.append({
                "segment_id":  f"LEG_{i + 1:03d}",
                "from":        wp_from.get("name", f"WP_{i}"),
                "to":          wp_to.get("name",   f"WP_{i+1}"),
                "geometry":    segment["geometry"],
                "distance_nm": round(raw_len_km * 0.539957, 1),   # km → nm
            })
        else:
            errors.append(
                f"searoute failed: {wp_from.get('name')} → {wp_to.get('name')}"
            )

    msg = AIMessage(
        content=f"[compute_segments] {len(segments)} segments computed, {len(errors)} failures."
    )
    return {**state, "raw_segments": segments, "errors": errors, "messages": [msg]}


# ──────────────────────────────────────────────────────────────────────────────
# NODE 3 — apply_anti_shipping
# ──────────────────────────────────────────────────────────────────────────────

def apply_anti_shipping_node(state: RouteState) -> RouteState:
    """
    Score each segment with the anti-shipping cost function.
    Scores: 1.0 = ideal (no commercial traffic), 0.0 = runs through shipping lane.
    """
    segments = state.get("raw_segments", [])
    scores   = []

    for seg in segments:
        coords = seg.get("geometry", {}).get("coordinates", [])
        score  = _router.calculate_anti_shipping_score(coords)
        scores.append(score)

        seg["anti_shipping_score"] = score
        seg["traffic_avoidance"]   = (
            "optimal"      if score >= 0.80 else
            "moderate"     if score >= 0.55 else
            "review_needed"
        )

    avg = sum(scores) / len(scores) if scores else 0.0
    flagged = sum(1 for s in scores if s < 0.70)

    msg = AIMessage(
        content=(
            f"[apply_anti_shipping] avg score={avg:.3f} | "
            f"{flagged}/{len(segments)} segments below threshold (0.70)"
        )
    )
    return {**state, "raw_segments": segments, "anti_shipping_scores": scores, "messages": [msg]}


# ──────────────────────────────────────────────────────────────────────────────
# NODE 4 — validate_safety
# ──────────────────────────────────────────────────────────────────────────────

def validate_safety_node(state: RouteState) -> RouteState:
    """
    Validate 2 nm coastal buffer and depth constraints for every segment.
    Sets status to 'validated' or 'safety_review'.
    """
    segments    = state.get("raw_segments", [])
    validations = []
    all_valid   = True

    for seg in segments:
        coords     = seg.get("geometry", {}).get("coordinates", [])
        validation = _router.apply_coastal_buffer(coords)
        validations.append({"segment_id": seg["segment_id"], **validation})

        if not validation["validated"]:
            all_valid = False
            seg["safety_flag"] = "COASTAL_BUFFER_REVIEW"
        else:
            seg["safety_flag"] = "CLEAR"

    status  = "validated" if all_valid else "safety_review"
    verdict = "✅ PASSED" if all_valid else "⚠️  REVIEW REQUIRED"

    msg = AIMessage(
        content=(
            f"[validate_safety] {verdict} — "
            f"{len(validations)} segments checked | buffer={_router.VESSEL_PROFILE['coastal_buffer_nm']} nm"
        )
    )
    return {
        **state,
        "raw_segments":       segments,
        "safety_validations": validations,
        "status":             status,
        "messages":           [msg],
    }


# ──────────────────────────────────────────────────────────────────────────────
# NODE 5 — llm_route_advisor
# ──────────────────────────────────────────────────────────────────────────────

def llm_route_advisor_node(state: RouteState) -> RouteState:
    """
    Call Deploy AI (GPT-4o) to generate a professional maritime route assessment
    and actionable optimisation recommendations.
    """
    segments = state.get("raw_segments", [])
    scores   = state.get("anti_shipping_scores", [])
    avg      = sum(scores) / len(scores) if scores else 0.0

    low_score_lines = [
        f"  • {s['segment_id']}: {s['from']} → {s['to']} "
        f"(score={s.get('anti_shipping_score', 0):.2f}, flag={s.get('traffic_avoidance')})"
        for s in segments if s.get("anti_shipping_score", 1.0) < 0.70
    ] or ["  All segments meet the anti-shipping threshold (≥ 0.70)"]

    prompt = f"""You are NAVIGUIDE's maritime route intelligence advisor for luxury sailing expeditions.

ROUTE ANALYSIS SUMMARY
──────────────────────
• Total segments   : {len(segments)}
• Total distance   : {sum(s.get('distance_nm', 0) for s in segments):,.0f} nm
• Avg anti-shipping score : {avg:.3f}  (1.0 = ideal, avoids all commercial traffic)
• Safety status    : {state.get('status', 'unknown').upper()}
• Segments needing attention (score < 0.70):
{chr(10).join(low_score_lines)}

Your task:
1. Provide a 2-sentence overall route quality assessment.
2. Identify the single most critical area for the skipper to monitor.
3. Give one specific, actionable optimisation for the lowest-scoring segment.

Tone: professional, concise, offshore-sailing expertise. Max 120 words."""

    advice = ""

    if _BEDROCK_AVAILABLE:
        try:
            llm    = ChatBedrock(model_id="us.anthropic.claude-3-5-sonnet-20241022-v2:0", region_name="us-east-1")
            advice = llm.invoke([HumanMessage(content=prompt)]).content
        except Exception as exc:
            advice = (
                f"LLM advisor unavailable ({exc}). "
                "Manual review recommended for segments scoring below 0.70."
            )

    if not advice:
        advice = (
            f"Route analysis complete. {len(segments)} segments computed, "
            f"avg anti-shipping score {avg:.3f}. "
            "Manual review recommended for segments scoring below 0.70."
        )

    msg = AIMessage(content=f"[llm_route_advisor] {advice}")
    return {
        **state,
        "route_advisor_notes": advice,
        "messages":            [msg],
    }


# ──────────────────────────────────────────────────────────────────────────────
# NODE 6 — fetch_vmg  (optional — skipped gracefully if no polar data available)
# ──────────────────────────────────────────────────────────────────────────────

# Polar API base URL — configurable via POLAR_API_URL env var
_POLAR_API_URL = os.getenv("POLAR_API_URL", "http://localhost:8004")

# TWS values (kts) used to estimate average offshore boat speed from polars
_POLAR_SPEED_TWS = ["10", "12", "16"]


def fetch_vmg_node(state: RouteState) -> RouteState:
    """
    Fetch VMG summary from the Polar API for the given expedition_id.

    - Calls GET {POLAR_API_URL}/api/v1/polar/{expedition_id}/summary
    - Computes polar_avg_speed = mean of (upwind.speed + downwind.speed) / 2
      across TWS 10, 12, 16 kts — representative offshore conditions.
    - Falls back silently if no polar data exists (expedition_id absent or API down).

    The polar_avg_speed replaces the hardcoded avg_speed_knots (10.0) in ETA
    calculations done by generate_route_plan_node.
    """
    expedition_id = state.get("expedition_id")
    messages      = list(state.get("messages", []))

    if not expedition_id:
        msg = AIMessage(content="[fetch_vmg] No expedition_id — using default vessel speed for ETAs.")
        return {**state, "polar_vmg": None, "polar_avg_speed": None, "messages": [msg]}

    url = f"{_POLAR_API_URL}/api/v1/polar/{expedition_id}/summary"
    try:
        req  = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data    = json.loads(resp.read().decode())
            vmg_sum = data.get("vmg_summary", {})

        # Compute a representative average offshore boat speed from the VMG table
        speeds = []
        for tws_key in _POLAR_SPEED_TWS:
            entry = vmg_sum.get(tws_key, {})
            up_sp = entry.get("upwind",   {}).get("speed", 0)
            dn_sp = entry.get("downwind", {}).get("speed", 0)
            if up_sp > 0 and dn_sp > 0:
                speeds.append((up_sp + dn_sp) / 2.0)

        polar_avg = round(sum(speeds) / len(speeds), 2) if speeds else None

        msg = AIMessage(
            content=(
                f"[fetch_vmg] ✅ Polar data loaded for '{expedition_id}' "
                f"(boat: {data.get('boat_name', '?')}) — "
                f"polar_avg_speed={polar_avg} kts "
                f"(based on TWS {', '.join(_POLAR_SPEED_TWS)} kts)"
            )
        )
        return {**state, "polar_vmg": vmg_sum, "polar_avg_speed": polar_avg, "messages": [msg]}

    except (urllib.error.URLError, Exception) as exc:
        msg = AIMessage(
            content=(
                f"[fetch_vmg] Polar API unavailable for '{expedition_id}' ({exc}). "
                "Falling back to vessel default speed."
            )
        )
        return {**state, "polar_vmg": None, "polar_avg_speed": None, "messages": [msg]}


# ──────────────────────────────────────────────────────────────────────────────
# NODE 7 — generate_route_plan
# ──────────────────────────────────────────────────────────────────────────────

def generate_route_plan_node(state: RouteState) -> RouteState:
    """
    Assemble the final enriched GeoJSON FeatureCollection — the 'digital twin'
    of the expedition as specified in the NAVIGUIDE V1 functional spec.

    ETA computation:
      - Uses polar_avg_speed (kts) if polar data was loaded by fetch_vmg_node.
      - Falls back to vessel_specs.avg_speed_knots (default 10.0 kts) otherwise.
    """
    segments  = state.get("raw_segments", [])
    waypoints = state.get("waypoints", [])
    scores    = state.get("anti_shipping_scores", [])

    total_nm  = sum(s.get("distance_nm", 0) for s in segments)
    avg_score = round(sum(scores) / len(scores), 4) if scores else 0.0

    # ── Speed source for ETA ──────────────────────────────────────────────────
    polar_avg_speed  = state.get("polar_avg_speed")              # from polar API
    vessel_avg_speed = (
        state.get("vessel_specs", {}).get("avg_speed_knots")
        or BerryMappemondeRouter.VESSEL_PROFILE["avg_speed_knots"]
    )
    eta_speed    = polar_avg_speed if polar_avg_speed else vessel_avg_speed
    polar_active = polar_avg_speed is not None

    total_eta_hours = 0.0
    features        = []

    # ── LineString features (route legs) ──────────────────────────────────────
    for seg in segments:
        dist_nm   = seg.get("distance_nm", 0)
        eta_hours = round(dist_nm / eta_speed, 2) if eta_speed > 0 else 0.0
        eta_days  = round(eta_hours / 24.0, 2)
        total_eta_hours += eta_hours

        features.append({
            "type":     "Feature",
            "geometry": seg["geometry"],
            "properties": {
                "segment_id":           seg["segment_id"],
                "from_port":            seg["from"],
                "to_port":              seg["to"],
                "distance_nm":          dist_nm,
                "eta_hours":            eta_hours,
                "eta_days":             eta_days,
                "anti_shipping_score":  seg.get("anti_shipping_score", 0),
                "traffic_avoidance":    seg.get("traffic_avoidance", "unknown"),
                "safety_flag":          seg.get("safety_flag", "CLEAR"),
                "routing_constraints": [
                    "avoid:shipping_lanes",
                    "avoid:tss_zones",
                    "min_depth:3m",
                    "coastal_buffer:2nm",
                ],
                "waypoints_count":      len(seg.get("geometry", {}).get("coordinates", [])),
                "validation_status":    "route_intelligence_complete",
                "agent":                "Agent1-RouteIntelligence",
            },
        })

    # ── Point features (stopover markers) ─────────────────────────────────────
    for wp in waypoints:
        features.append({
            "type":     "Feature",
            "geometry": {"type": "Point", "coordinates": [wp["lon"], wp["lat"]]},
            "properties": {
                "name":      wp.get("name", "Waypoint"),
                "type":      "stopover",
                "mandatory": wp.get("mandatory", True),
            },
        })

    route_plan = {
        "type": "FeatureCollection",
        "metadata": {
            "expedition_name":         "Berry-Mappemonde",
            "agent":                   "NAVIGUIDE Route Intelligence Agent v1.0",
            "framework":               "LangGraph",
            "calculation_timestamp":   datetime.utcnow().isoformat() + "Z",
            "algorithm_version":       "1.0.0",
            "total_distance_nm":       round(total_nm, 1),
            "total_segments":          len(segments),
            "anti_shipping_avg_score": avg_score,
            "safety_status":           state.get("status", "unknown"),
            "route_advisor_notes":     state.get("route_advisor_notes", ""),
            # ETA metadata
            "total_eta_days":          round(total_eta_hours / 24.0, 1),
            "total_eta_hours":         round(total_eta_hours, 1),
            "avg_speed_knots_used":    eta_speed,
            "polar_data_used":         polar_active,
            "expedition_id":           state.get("expedition_id"),
        },
        "features": features,
    }

    msg = AIMessage(
        content=(
            f"[generate_route_plan] ✅ Route plan complete — "
            f"{len(segments)} segments | {round(total_nm):,} nm | "
            f"ETA {round(total_eta_hours/24.0, 1)} days "
            f"@ {eta_speed} kts "
            f"({'polar VMG' if polar_active else 'vessel default'})"
        )
    )
    return {**state, "route_plan": route_plan, "status": "complete", "messages": [msg]}

