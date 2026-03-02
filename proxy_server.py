"""
NAVIGUIDE — Unified Proxy Server
Serves the React frontend as static files and proxies API calls
to the backend services running internally.

Routes proxied:
  GET  /route              → naviguide-api     :8000
  POST /wind               → naviguide-api     :8000
  POST /wave               → naviguide-api     :8000
  POST /current            → naviguide-api     :8000
  *    /api/v1/polar/*     → polar-api         :8004
  *    /api/v1/routing/*   → weather-routing   :3010
  *    /api/v1/*           → naviguide-orch    :3008
  *                        → static frontend   (dist/)
"""

import os
import logging
import json
from pathlib import Path

import uvicorn
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import Response, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_DIR = Path(os.environ.get(
    "NAVIGUIDE_LOG_DIR",
    str(Path(__file__).resolve().parent / "logs")
))
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    handlers=[
        logging.FileHandler(LOG_DIR / "proxy_server.log"),
        logging.StreamHandler(),
    ],
    format='{"time":"%(asctime)s","level":"%(levelname)s","msg":"%(message)s"}',
)
log = logging.getLogger("proxy_server")

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="NAVIGUIDE Proxy Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Backend targets ───────────────────────────────────────────────────────────
API_BACKEND             = "http://localhost:8001"
ORCHESTRATOR_BACKEND    = "http://localhost:3008"
WEATHER_ROUTING_BACKEND = "http://localhost:3010"
POLAR_API_BACKEND       = "http://localhost:8004"

STATIC_DIR = Path(__file__).resolve().parent / "naviguide-app" / "dist"

# ── Generic proxy helper ──────────────────────────────────────────────────────
async def proxy_request(request: Request, base_url: str, path: str) -> Response:
    url = f"{base_url}/{path}"
    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in ("host", "content-length")
    }
    body = await request.body()
    params = dict(request.query_params)

    log.info(f"Proxy {request.method} /{path} → {url}")

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.request(
                method=request.method,
                url=url,
                headers=headers,
                content=body,
                params=params,
            )
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            media_type=resp.headers.get("content-type", "application/json"),
        )
    except httpx.ConnectError as exc:
        log.error(f"Backend unreachable: {url} — {exc}")
        return JSONResponse(
            {"error": "Backend service unavailable", "url": url},
            status_code=503,
        )
    except Exception as exc:
        log.error(f"Proxy error for {url}: {exc}")
        return JSONResponse({"error": str(exc)}, status_code=500)


# ── naviguide-api routes ──────────────────────────────────────────────────────
@app.api_route("/route", methods=["GET", "POST"])
async def proxy_route(request: Request):
    return await proxy_request(request, API_BACKEND, "route")

@app.api_route("/wind", methods=["GET", "POST"])
async def proxy_wind(request: Request):
    return await proxy_request(request, API_BACKEND, "wind")

@app.api_route("/wave", methods=["GET", "POST"])
async def proxy_wave(request: Request):
    return await proxy_request(request, API_BACKEND, "wave")

@app.api_route("/current", methods=["GET", "POST"])
async def proxy_current(request: Request):
    return await proxy_request(request, API_BACKEND, "current")

# ── Polar API routes (must be before general /api/v1/*) ──────────────────────
@app.api_route("/api/v1/polar/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy_polar(request: Request, path: str):
    return await proxy_request(request, POLAR_API_BACKEND, f"api/v1/polar/{path}")

# ── Weather routing routes ────────────────────────────────────────────────────
@app.api_route("/api/v1/routing/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy_weather_routing(request: Request, path: str):
    return await proxy_request(request, WEATHER_ROUTING_BACKEND, f"api/v1/routing/{path}")

# ── Orchestrator routes ───────────────────────────────────────────────────────
@app.api_route("/api/v1/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy_orchestrator(request: Request, path: str):
    return await proxy_request(request, ORCHESTRATOR_BACKEND, f"api/v1/{path}")

# ── Health check ──────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok", "service": "NAVIGUIDE Proxy Server"}

# ── Static frontend (catch-all) ───────────────────────────────────────────────
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.getenv("PORT", 3014))
    log.info(f"Starting NAVIGUIDE Proxy Server on port {port}")
    log.info(f"Static files: {STATIC_DIR}")
    uvicorn.run("proxy_server:app", host="0.0.0.0", port=port, reload=False)
