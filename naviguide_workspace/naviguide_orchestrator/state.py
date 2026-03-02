"""
NAVIGUIDE Orchestrator — LangGraph State Definition
"""

from typing import Any, Dict, List, Optional, TypedDict


class OrchestratorState(TypedDict, total=False):
    """State flowing through the multi-agent Orchestrator graph."""

    # Input
    waypoints:             List[Dict[str, Any]]
    vessel_specs:          Dict[str, Any]
    constraints:           Dict[str, Any]
    expedition_id:         Optional[str]          # links to polar data (polar_api)

    # Agent 1 outputs
    agent1_status:         str
    agent1_errors:         List[str]
    route_plan:            Dict[str, Any]         # GeoJSON FeatureCollection
    anti_shipping_avg:     float
    # Polar performance (populated by Agent 1 → fetch_vmg_node)
    polar_vmg:             Optional[Dict[str, Any]]  # VMG summary {tws: {upwind, downwind}}
    polar_avg_speed:       Optional[float]            # computed avg boat speed (kts)
    total_eta_days:        Optional[float]            # expedition ETA in days (polar-based or default)

    # Agent 3 outputs
    agent3_status:         str
    agent3_errors:         List[str]
    risk_report:           Dict[str, Any]
    expedition_risk_level: str

    # Final output
    expedition_plan:       Dict[str, Any]         # merged digital twin
    executive_briefing:    str

    # Execution control
    messages:              List[Any]
    errors:                List[str]
    status:                str                    # init / running_a1 / running_a3 / briefing / complete / error
    language:              str                    # "en" | "fr" — briefing output language
    chat_id:               Optional[str]
    access_token:          Optional[str]
