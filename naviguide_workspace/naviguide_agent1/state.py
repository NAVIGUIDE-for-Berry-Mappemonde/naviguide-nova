"""
NAVIGUIDE Agent 1 — LangGraph State Definition
"""

from typing import Any, Dict, List, Optional, TypedDict


class RouteState(TypedDict, total=False):
    """State flowing through the Route Intelligence Agent graph."""

    # Input
    waypoints:            List[Dict[str, Any]]   # [{name, lat, lon, mandatory, skip_maritime}]
    vessel_specs:         Dict[str, Any]          # vessel performance profile
    constraints:          Dict[str, Any]          # routing constraints

    # Polar performance data (optional — from Polar API)
    expedition_id:        Optional[str]           # links to polar_data/polar_{id}.json
    polar_vmg:            Optional[Dict[str, Any]] # VMG summary {tws: {upwind, downwind, gybe_angle}}
    polar_avg_speed:      Optional[float]         # computed avg boat speed from polars (kts)

    # Intermediate — segment computation
    raw_segments:         List[Dict[str, Any]]   # enriched segments with geometry
    anti_shipping_scores: List[float]             # per-segment scores
    safety_validations:   List[Dict[str, Any]]   # per-segment safety flags

    # LLM advisory
    route_advisor_notes:  str

    # Output
    route_plan:           Dict[str, Any]          # final GeoJSON FeatureCollection

    # Execution control
    messages:             List[Any]               # LangChain message history
    errors:               List[str]
    status:               str                     # init / processing / validated / complete / error
    chat_id:              Optional[str]
    access_token:         Optional[str]
