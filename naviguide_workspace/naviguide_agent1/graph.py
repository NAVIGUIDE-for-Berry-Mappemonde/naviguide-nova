"""
NAVIGUIDE Agent 1 — LangGraph StateGraph Definition
build_route_intelligence_agent() compiles the full route pipeline graph.
"""

from langgraph.graph import StateGraph, END

from .state import RouteState
from .nodes import (
    parse_route_node,
    compute_segments_node,
    apply_anti_shipping_node,
    validate_safety_node,
    llm_route_advisor_node,
    fetch_vmg_node,
    generate_route_plan_node,
)


def _route_after_parse(state: RouteState) -> str:
    """Conditional edge after parse_route: skip to END on error."""
    return "error" if state.get("status") == "error" else "ok"


def build_route_intelligence_agent():
    """
    Compile and return the Route Intelligence LangGraph.

    Flow:
      parse_route → compute_segments → apply_anti_shipping
                  → validate_safety → llm_route_advisor
                  → fetch_vmg       → generate_route_plan → END

    fetch_vmg queries the Polar API for real VMG data when an expedition_id
    is provided, enabling accurate ETA calculations per segment.
    """
    graph = StateGraph(RouteState)

    # Register nodes
    graph.add_node("parse_route",          parse_route_node)
    graph.add_node("compute_segments",     compute_segments_node)
    graph.add_node("apply_anti_shipping",  apply_anti_shipping_node)
    graph.add_node("validate_safety",      validate_safety_node)
    graph.add_node("llm_route_advisor",    llm_route_advisor_node)
    graph.add_node("fetch_vmg",            fetch_vmg_node)
    graph.add_node("generate_route_plan",  generate_route_plan_node)

    # Entry point
    graph.set_entry_point("parse_route")

    # Conditional: abort on parse error
    graph.add_conditional_edges(
        "parse_route",
        _route_after_parse,
        {"error": END, "ok": "compute_segments"},
    )

    # Linear pipeline
    graph.add_edge("compute_segments",    "apply_anti_shipping")
    graph.add_edge("apply_anti_shipping", "validate_safety")
    graph.add_edge("validate_safety",     "llm_route_advisor")
    graph.add_edge("llm_route_advisor",   "fetch_vmg")
    graph.add_edge("fetch_vmg",           "generate_route_plan")
    graph.add_edge("generate_route_plan", END)

    return graph.compile()
