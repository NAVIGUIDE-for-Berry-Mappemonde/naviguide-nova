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
POST /api/v1/polar/chat                     Polar agent chat (VMG-aware, Anthropic-backed)
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

# Load workspace .env (ANTHROPIC_API_KEY)
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

# Anthropic client (optional — chat falls back gracefully)
try:
    import anthropic as _anthropic
    _ANTHROPIC_CLIENT    = _anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    _ANTHROPIC_AVAILABLE = bool(os.getenv("ANTHROPIC_API_KEY"))
except Exception:
    _ANTHROPIC_CLIENT    = None
    _ANTHROPIC_AVAILABLE = False

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
    Loads VMG context for the expedition, answers via Anthropic Claude.
    Falls back to a structured answer if Anthropic is unavailable.
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

    # Build message list for Anthropic
    messages = [{"role": m["role"], "content": m["content"]} for m in (request.history or [])]
    messages.append({"role": "user", "content": request.message})

    log.info(f"Polar chat: expedition={request.expedition_id}, msg='{request.message[:60]}'")

    if _ANTHROPIC_AVAILABLE and _ANTHROPIC_CLIENT:
        try:
            resp = _ANTHROPIC_CLIENT.messages.create(
                model      = "claude-haiku-4-5",
                max_tokens = 300,
                system     = system_prompt,
                messages   = messages,
            )
            reply  = resp.content[0].text
            source = "anthropic"
        except Exception as exc:
            log.warning(f"Anthropic unavailable ({exc}) — using fallback")
            reply  = _polar_fallback_reply(request.message, polar_data)
            source = "fallback"
    else:
        reply  = _polar_fallback_reply(request.message, polar_data)
        source = "fallback"

    return {
        "reply":        reply,
        "source":       source,
        "expedition_id": request.expedition_id,
        "boat_name":    polar_data.get("boat_name"),
    }


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
