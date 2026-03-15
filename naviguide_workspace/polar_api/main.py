"""
NAVIGUIDE — Polar API Service
==============================
FastAPI service: upload a polar PDF, parse it with polar_engine,
persist the 181×61 interpolated grid, and expose it to all agents.

Endpoints
─────────
GET  /                                      Health check
POST /api/v1/polar/upload                   Upload PDF → parse → store 181×61 grid
GET  /api/v1/polar/{expedition_id}          Retrieve full polar grid (181×61)
GET  /api/v1/polar/{expedition_id}/summary  VMG summary only (lightweight, for briefing agents)
POST /api/v1/polar/chat                     Polar agent chat (VMG-aware, Nova + Claude)
POST /api/v1/chat                           General nav chat (expedition or leg context)
"""

import json
import logging
import sys
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

# Load workspace .env (AWS_BEARER_TOKEN_BEDROCK for Nova + Claude)
_WS = Path(__file__).resolve().parents[1]
load_dotenv(_WS / ".env")
if str(_WS) not in sys.path:
    sys.path.insert(0, str(_WS))

# ── Path setup: import polar_engine from polar_agent/ at repo root ─────────────
REPO_ROOT = Path(__file__).resolve().parent.parent.parent   # naviguide-berry-mappemonde/
sys.path.insert(0, str(REPO_ROOT / "polar_agent"))

from polar_engine import parse_polar_pdf, parse_polar_csv, parse_polar_excel, PolarData  # noqa: E402

# ── Storage directory for polar JSON files ────────────────────────────────────
POLAR_DATA_DIR = Path(__file__).resolve().parent.parent / "polar_data"
POLAR_DATA_DIR.mkdir(parents=True, exist_ok=True)

# ── Logging ───────────────────────────────────────────────────────────────────
_DEFAULT_LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_DIR = Path(os.getenv("LOG_DIR", str(_DEFAULT_LOG_DIR)))
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    handlers=[
        logging.FileHandler(LOG_DIR / "polar_api.log"),
        logging.StreamHandler(),
    ],
    format='{"time":"%(asctime)s","level":"%(levelname)s","msg":"%(message)s"}',
)
log = logging.getLogger("polar_api")

# ── FastAPI app ───────────────────────────────────────────────────────────────
app = FastAPI(
    title="NAVIGUIDE — Polar API",
    description=(
        "Upload a polar PDF, parse it into a full 181×61 interpolated grid "
        "(TWA 0→180° × TWS 0→60 kts), and expose the result to all NAVIGUIDE agents."
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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _polar_path(expedition_id: str) -> Path:
    """Return storage path for a given expedition_id."""
    safe = expedition_id.replace("/", "_").replace("\\", "_").replace(" ", "_")
    return POLAR_DATA_DIR / f"polar_{safe}.json"


def _serialize_polar(polar: PolarData, expedition_id: str) -> Dict[str, Any]:
    """
    Serialize a PolarData object to a JSON-serializable dict.

    Stored fields:
    - expedition_id, boat_name, created_at
    - raw   : sparse grid as-parsed from the PDF (twa_rows, tws_cols, matrix)
    - grid  : full 181×61 interpolated grid — grid[twa_deg][tws_kt] = speed (kts)
    - vmg_summary : optimal VMG angles/speeds for key TWS values
    """
    full_grid   = polar.generate_full_grid()   # numpy shape (181, 61)
    vmg_summary = polar.summary()              # {tws: {upwind, downwind, gybe_angle}}

    return {
        "expedition_id": expedition_id,
        "boat_name":     polar.boat_name,
        "created_at":    datetime.now(timezone.utc).isoformat(),
        "grid_shape":    [181, 61],
        # Raw sparse grid extracted from the PDF
        "raw": {
            "twa_rows": polar.twa_rows,
            "tws_cols": polar.tws_cols,
            "matrix":   polar.matrix.tolist(),
        },
        # Full interpolated grid: index by [twa_degree][tws_knot]
        "grid": full_grid.tolist(),
        # VMG optimals for standard TWS values (8, 10, 12, 16, 20, 25 kts)
        "vmg_summary": {
            str(tws): {
                "upwind":     d["upwind"],
                "downwind":   d["downwind"],
                "gybe_angle": d["gybe_angle"],
            }
            for tws, d in vmg_summary.items()
        },
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/")
def health():
    """Health check — returns service status."""
    return {
        "service":    "NAVIGUIDE Polar API",
        "version":    "1.0.0",
        "status":     "operational",
        "storage":    str(POLAR_DATA_DIR),
        "endpoints": [
            "POST /api/v1/polar/upload",
            "GET  /api/v1/polar/{expedition_id}",
            "GET  /api/v1/polar/{expedition_id}/summary",
        ],
    }


@app.post("/api/v1/polar/upload")
async def upload_polar(
    file:          UploadFile     = File(...,  description="Polar table in PDF format"),
    expedition_id: str            = Form(...,  description="Unique expedition identifier (e.g. 'berry-mappemonde-2026')"),
    boat_name:     Optional[str]  = Form(None, description="Boat name (optional, defaults to expedition_id)"),
):
    """
    Upload a polar PDF for an expedition.

    1. Reads the PDF bytes from the multipart form.
    2. Parses the polar table with `polar_engine.parse_polar_pdf()`.
    3. Generates the full 181×61 interpolated grid (TWA 0→180°, TWS 0→60 kts).
    4. Serialises to `polar_data/polar_{expedition_id}.json`.
    5. Returns metadata + VMG summary. Retrieve the full grid via:
       GET /api/v1/polar/{expedition_id}
    """
    fname = file.filename.lower()
    allowed = (".pdf", ".csv", ".xlsx", ".xls")
    if not any(fname.endswith(ext) for ext in allowed):
        raise HTTPException(status_code=400, detail="Accepted formats: PDF, CSV, XLSX, XLS.")

    bname = boat_name or expedition_id
    log.info(f"Polar upload: expedition_id={expedition_id}, boat={bname}, file={file.filename}")

    # 1 — Parse file (PDF / CSV / Excel)
    try:
        raw_bytes = await file.read()
        if fname.endswith(".pdf"):
            polar = parse_polar_pdf(raw_bytes, boat_name=bname)
        elif fname.endswith(".csv"):
            polar = parse_polar_csv(raw_bytes, boat_name=bname)
        else:  # .xlsx / .xls
            polar = parse_polar_excel(raw_bytes, boat_name=bname)
    except Exception as exc:
        log.error(f"File parsing failed: {exc}")
        raise HTTPException(status_code=422, detail=f"Parsing error: {exc}")

    # 2 — Generate grid + serialise
    try:
        data = _serialize_polar(polar, expedition_id)
    except Exception as exc:
        log.error(f"Grid generation failed: {exc}")
        raise HTTPException(status_code=500, detail=f"Grid generation error: {exc}")

    # 3 — Persist to JSON
    try:
        dest = _polar_path(expedition_id)
        with open(dest, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        log.info(f"Polar stored: {dest} ({dest.stat().st_size:,} bytes)")
    except Exception as exc:
        log.error(f"Storage error: {exc}")
        raise HTTPException(status_code=500, detail=f"Storage error: {exc}")

    return {
        "status":        "ok",
        "expedition_id": expedition_id,
        "boat_name":     bname,
        "grid_shape":    data["grid_shape"],
        "raw_rows":      len(polar.twa_rows),
        "raw_cols":      len(polar.tws_cols),
        "vmg_summary":   data["vmg_summary"],
        "stored_at":     str(dest),
        "created_at":    data["created_at"],
    }


@app.get("/api/v1/polar/{expedition_id}")
def get_polar(expedition_id: str):
    """
    Retrieve the full polar dataset for an expedition.
    Returns: raw sparse grid, full 181×61 interpolated grid, VMG summary.
    """
    dest = _polar_path(expedition_id)
    if not dest.exists():
        raise HTTPException(
            status_code=404,
            detail=(
                f"No polar data found for expedition '{expedition_id}'. "
                "Upload a PDF via POST /api/v1/polar/upload first."
            ),
        )

    try:
        with open(dest, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        log.info(f"Polar retrieved: expedition_id={expedition_id}")
        return data
    except Exception as exc:
        log.error(f"Retrieval error: {exc}")
        raise HTTPException(status_code=500, detail=f"Retrieval error: {exc}")


@app.get("/api/v1/polar/{expedition_id}/summary")
def get_polar_summary(expedition_id: str):
    """
    Lightweight polar summary for briefing agents.
    Returns only metadata + VMG optimals — omits the full 181×61 grid.
    Useful for Agent 1 (ETA calc) and Agent 3 (risk × speed) integrations.
    """
    dest = _polar_path(expedition_id)
    if not dest.exists():
        raise HTTPException(
            status_code=404,
            detail=f"No polar data found for expedition '{expedition_id}'.",
        )

    try:
        with open(dest, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return {
            "expedition_id": data["expedition_id"],
            "boat_name":     data["boat_name"],
            "created_at":    data["created_at"],
            "grid_shape":    data["grid_shape"],
            "vmg_summary":   data["vmg_summary"],
        }
    except Exception as exc:
        log.error(f"Summary retrieval error: {exc}")
        raise HTTPException(status_code=500, detail=f"Retrieval error: {exc}")


# ── Polar Chat ────────────────────────────────────────────────────────────────

class PolarChatRequest(BaseModel):
    expedition_id: str
    message:       str
    history:       Optional[List[Dict[str, str]]] = []   # [{role, content}]


def _build_polar_system_prompt(data: Dict[str, Any]) -> str:
    """
    Build a concise system prompt for the polar chat agent,
    embedding the VMG summary so Claude can answer performance questions.
    """
    vmg = data.get("vmg_summary", {})
    boat = data.get("boat_name", "the boat")

    vmg_lines = []
    for tws_key in sorted(vmg.keys(), key=lambda x: int(x)):
        entry = vmg[tws_key]
        uw = entry.get("upwind",   {})
        dw = entry.get("downwind", {})
        vmg_lines.append(
            f"  TWS {tws_key} kts: upwind {uw.get('vmg',0):.1f}kts VMG @ {uw.get('twa',0)}°, "
            f"speed {uw.get('speed',0):.1f}kts | "
            f"downwind {dw.get('vmg',0):.1f}kts VMG @ {dw.get('twa',0)}°, "
            f"speed {dw.get('speed',0):.1f}kts"
        )

    vmg_table = "\n".join(vmg_lines) or "  No VMG data available."
    grid_shape = data.get("grid_shape", [181, 61])

    return f"""You are the NAVIGUIDE polar performance assistant, expert in sailboat polars and offshore racing.
You have access to the polar performance data for **{boat}** (expedition: {data['expedition_id']}).

POLAR DATA SUMMARY
══════════════════
Boat: {boat}
Grid: {grid_shape[0]} TWA rows × {grid_shape[1]} TWS columns (fully interpolated)
Loaded: {data.get('created_at', 'unknown')}

VMG OPTIMALS (key TWS values):
{vmg_table}

Answer questions about:
- Optimal VMG angles and boat speeds at any wind condition
- Whether to tack, gybe, or hold course
- ETA estimates for given distances and wind conditions
- Comparison between upwind and downwind performance

Be concise (max 120 words), precise, and use nautical terms. Always cite specific values from the polar data above."""


@app.post("/api/v1/polar/chat")
async def polar_chat(request: PolarChatRequest):
    """
    Chat with the polar agent about boat polar performance.
    Loads VMG context for the expedition, answers via Nova + Claude (Bedrock).
    Falls back to a structured answer if LLM is unavailable.
    """
    dest = _polar_path(request.expedition_id)
    if not dest.exists():
        raise HTTPException(
            status_code=404,
            detail=f"No polar data for '{request.expedition_id}'. Upload a PDF first.",
        )

    with open(dest, "r", encoding="utf-8") as fh:
        polar_data = json.load(fh)

    system_prompt = _build_polar_system_prompt(polar_data)

    # Build prompt from history + new message
    conv_lines = []
    for m in (request.history or []):
        role = m.get("role", "user")
        content = m.get("content", "")
        conv_lines.append(f"{role.capitalize()}: {content}")
    conv_lines.append(f"User: {request.message}")
    prompt = "\n\n".join(conv_lines)

    log.info(f"Polar chat: expedition={request.expedition_id}, msg='{request.message[:60]}'")

    try:
        from llm_utils import invoke_llm
        reply = invoke_llm(prompt, system=system_prompt, fallback_msg="")
        source = "nova" if reply else "fallback"
    except Exception as exc:
        log.warning(f"LLM unavailable ({exc}) — using fallback")
        reply = None
        source = "fallback"

    if not reply:
        reply = _polar_fallback_reply(request.message, polar_data)
        source = "fallback"

    return {
        "reply":        reply,
        "source":       source,
        "expedition_id": request.expedition_id,
        "boat_name":    polar_data.get("boat_name"),
    }


# ── General Nav Chat (expedition + simulation) ─────────────────────────────────

class NavChatRequest(BaseModel):
    mode:     str   # "expedition" | "simulation"
    context:  Dict[str, Any]
    message:  str
    history:  Optional[List[Dict[str, str]]] = []


def _build_system_prompt_from_context(mode: str, ctx: Dict[str, Any]) -> str:
    """Build system prompt from pre-built context (frontend sends summarized context)."""
    lang = ctx.get("language", "fr")
    lang_full = "French" if lang == "fr" else "English"

    if mode == "expedition":
        summary = ctx.get("summary", {})
        briefing = ctx.get("briefing", "")
        alerts = ctx.get("critical_alerts", [])
        waypoints = ctx.get("waypoints", [])
        legs = ctx.get("legs_summary", [])
        polar = ctx.get("polar_summary", {})
        satellite = ctx.get("satellite_summary", "")

        stats = (
            f"Distance: {summary.get('total_distance_nm', '—')} nm | "
            f"Segments: {summary.get('total_segments', '—')} | "
            f"Risk: {summary.get('expedition_risk_level', '—')}"
        )
        alerts_txt = "\n".join(
            f"• {a.get('waypoint', '?')}: {a.get('risk_level', '?')} — {a.get('dominant_risk', '')}"
            for a in (alerts[:5] or [])
        ) or "Aucune alerte critique."
        legs_txt = "\n".join(
            f"• {l.get('from', '?')} → {l.get('to', '?')}: {l.get('distance_nm', '?')} nm"
            for l in (legs[:20] or [])
        ) or "—"
        wp_txt = ", ".join(w.get("name", "?") for w in (waypoints[:30] or [])) or "—"
        polar_txt = (
            f"Boat: {polar.get('boat_name', '—')} | "
            f"VMG upwind @12kt: {polar.get('vmg_at_12kt', {}).get('upwind_vmg', '—')} kts | "
            f"VMG downwind @12kt: {polar.get('vmg_at_12kt', {}).get('downwind_vmg', '—')} kts"
        )

        return f"""Tu es l'assistant NAVIGUIDE pour l'expédition Berry-Mappemonde — tour du monde en catamaran.

CONTEXTE EXPÉDITION:
{stats}

BRIEFING:
{briefing[:2000] if briefing else "—"}

ALERTES CRITIQUES:
{alerts_txt}

WAYPOINTS: {wp_txt}

LEGS (résumé):
{legs_txt}

POLAIRES: {polar_txt}

DONNÉES SATELLITE (vent/vague/courant sur la route): {satellite or "Données intégrées sur les segments."}

Réponds aux questions du skipper en t'appuyant UNIQUEMENT sur ces données. Si une information n'est pas dans le contexte, dis-le clairement.
Sois concis, précis, utilise le vocabulaire maritime. Max 200 mots par réponse sauf si le skipper demande plus de détails.
Langue de réponse : {lang_full}."""

    else:
        # mode == "simulation"
        leg = ctx.get("leg", {})
        expedition = ctx.get("expedition_summary", {})
        polar = ctx.get("polar_summary", {})
        satellite = ctx.get("satellite_data", {})
        alerts = ctx.get("alerts_on_leg", [])

        wind = satellite.get("wind", {}) or {}
        wave = satellite.get("wave", {}) or {}
        current = satellite.get("current", {}) or {}

        wind_txt = (
            f"{wind.get('wind_speed_knots', wind.get('speed_knots', '?'))} kt from "
            f"{wind.get('wind_direction', wind.get('direction', '?'))}°"
        ) if wind else "N/A"
        wave_txt = (
            f"{wave.get('significant_wave_height_m', wave.get('height_m', '?'))} m, "
            f"{wave.get('mean_wave_period', '?')} s"
        ) if wave else "N/A"
        curr_txt = (
            f"{current.get('speed_knots', '?')} kt @ {current.get('direction_deg', '?')}°"
        ) if current else "N/A"

        return f"""Tu es l'assistant NAVIGUIDE pour l'expédition Berry-Mappemonde. Le skipper est en mode simulation sur le leg actif.

LEG ACTIF:
• De {leg.get('from_stop', '?')} vers {leg.get('to_stop', '?')}
• Position: {leg.get('lat', '?')}° / {leg.get('lon', '?')}°
• Distance restante: {leg.get('nm_remaining_to_stop', '?')} nm
• ETA: {leg.get('eta_hours', '?')} h
• Cap: {leg.get('bearing', '?')}°
• Vitesse: {leg.get('speed_knots', '?')} kt

DONNÉES SATELLITE À LA POSITION:
• Vent: {wind_txt}
• Vague: {wave_txt}
• Courant: {curr_txt}

EXPÉDITION: {expedition.get('total_distance_nm', '?')} nm, risque {expedition.get('expedition_risk_level', '?')}
POLAIRES: {polar.get('boat_name', '?')}, VMG upwind/downwind @ 12 kt
ALERTES SUR CE LEG: {', '.join(a.get('waypoint', '') for a in alerts) or 'Aucune'}

Réponds aux questions en te basant sur ce contexte. Priorise les infos du leg actif.
Concis, vocabulaire maritime. Max 200 mots. Langue : {lang_full}."""


@app.post("/api/v1/chat")
async def nav_chat(request: NavChatRequest):
    """
    Chat with full expedition or leg context.
    mode=expedition: plan, briefing, alerts, segments, polar, satellite summary.
    mode=simulation: leg context + satellite data at position.
    """
    if request.mode not in ("expedition", "simulation"):
        raise HTTPException(status_code=400, detail="mode must be 'expedition' or 'simulation'")

    system_prompt = _build_system_prompt_from_context(request.mode, request.context)

    conv_lines = []
    for m in (request.history or []):
        role = m.get("role", "user")
        content = m.get("content", "")
        conv_lines.append(f"{role.capitalize()}: {content}")
    conv_lines.append(f"User: {request.message}")
    prompt = "\n\n".join(conv_lines)

    log.info(f"Nav chat: mode={request.mode}, msg='{request.message[:60]}'")

    try:
        from llm_utils import invoke_llm
        reply = invoke_llm(prompt, system=system_prompt, fallback_msg="")
        source = "nova" if reply else "fallback"
    except Exception as exc:
        log.warning(f"LLM unavailable ({exc}) — using fallback")
        reply = None
        source = "fallback"

    if not reply:
        reply = (
            "[NAVIGUIDE — Service temporairement indisponible.] "
            "Réessayez dans quelques instants ou consultez les ressources de secours (Windy, Passage Weather)."
        )
        source = "fallback"

    return {"reply": reply, "source": source, "mode": request.mode}


def _polar_fallback_reply(message: str, data: Dict[str, Any]) -> str:
    """Structured fallback when LLM is unavailable — picks key VMG values from polar data."""
    vmg = data.get("vmg_summary", {})
    boat = data.get("boat_name", "the boat")

    # Find best upwind/downwind at TWS 12
    entry12 = vmg.get("12", vmg.get("10", {}))
    uw = entry12.get("upwind",   {})
    dw = entry12.get("downwind", {})

    return (
        f"[Polar summary for **{boat}** — AI unavailable]\n\n"
        f"At TWS 12 kts:\n"
        f"• Upwind: best VMG **{uw.get('vmg',0):.1f} kts** at **{uw.get('twa',0)}° TWA** "
        f"(boat speed {uw.get('speed',0):.1f} kts)\n"
        f"• Downwind: best VMG **{dw.get('vmg',0):.1f} kts** at **{dw.get('twa',0)}° TWA** "
        f"(boat speed {dw.get('speed',0):.1f} kts)\n\n"
        f"Use `GET /api/v1/polar/{data['expedition_id']}` for the full 181×61 grid."
    )


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8004))
    log.info(f"Starting NAVIGUIDE Polar API on port {port}")
    uvicorn.run("polar_api.main:app", host="0.0.0.0", port=port, reload=False)
