#!/usr/bin/env bash
# =============================================================================
# NAVIGUIDE — Local Development Startup Script
# Starts all 5 services: API + Orchestrator + Weather Routing + Polar API + Frontend
#
# Usage (from project root OR from naviguide_workspace/):
#   chmod +x naviguide_workspace/start_local.sh
#   ./naviguide_workspace/start_local.sh
#
# Services:
#   http://localhost:8000   — naviguide-api           (FastAPI + searoute)
#   http://localhost:3008   — naviguide-orchestrator  (LangGraph multi-agent)
#   http://localhost:3010   — naviguide-weather-routing
#   http://localhost:8001   — polar-api               (polaires voiliers)
#   http://localhost:5173   — naviguide-app            (Vite React frontend)
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"       # naviguide-berry-mappemonde/
LOG_DIR="$PROJECT_ROOT/logs"
mkdir -p "$LOG_DIR"

# ── Colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()    { echo -e "${CYAN}[NAVIGUIDE]${NC} $1"; }
success() { echo -e "${GREEN}[✓]${NC} $1"; }
warn()    { echo -e "${YELLOW}[!]${NC} $1"; }
err()     { echo -e "${RED}[✗]${NC} $1"; }

# ── Kill any leftover processes on our ports ──────────────────────────────────
info "Freeing ports 8000, 3008, 3010, 8001, 5173..."
for PORT in 8000 3008 3010 8001 5173; do
    lsof -ti tcp:"$PORT" | xargs kill -9 2>/dev/null || true
done

# ── Check Python ──────────────────────────────────────────────────────────────
PYTHON=$(command -v python3 2>/dev/null || command -v python 2>/dev/null)
if [ -z "$PYTHON" ]; then
    err "Python 3 not found. Install: brew install python"
    exit 1
fi
info "Python: $($PYTHON --version 2>&1)"

# ── Check Node.js ─────────────────────────────────────────────────────────────
if ! command -v node &>/dev/null; then
    err "Node.js not found. Install: brew install node"
    exit 1
fi
info "Node: $(node --version)"

# ── Install Python dependencies (first run only) ──────────────────────────────
info "Checking Python dependencies..."
$PYTHON -m pip install -q -r "$PROJECT_ROOT/naviguide-api/requirements.txt" --user 2>/dev/null || true
$PYTHON -m pip install -q -r "$SCRIPT_DIR/requirements.txt" --user 2>/dev/null || true
# polar_engine dependencies (numpy, scipy, pdfplumber for polar PDF parsing)
$PYTHON -m pip install -q numpy scipy pdfplumber openpyxl --user 2>/dev/null || true
success "Python dependencies ready"

# ── Install frontend dependencies (first run only) ───────────────────────────
if [ ! -d "$PROJECT_ROOT/naviguide-app/node_modules" ]; then
    info "Installing npm packages..."
    (cd "$PROJECT_ROOT/naviguide-app" && npm install --silent)
fi

# ── Service 1: naviguide-api (port 8000) ──────────────────────────────────────
info "Starting naviguide-api on :8000..."
API_DIR="$PROJECT_ROOT/naviguide-api"
if [ ! -f "$API_DIR/.env" ]; then
    cat > "$API_DIR/.env" <<'ENVEOF'
COPERNICUS_USERNAME=berrymappemonde@gmail.com
COPERNICUS_PASSWORD=Hackmyroute2027
PORT=8000
# ── Agents IA simulation — Anthropic Claude (obligatoire pour les agents) ─
# Renseigner ANTHROPIC_API_KEY pour activer les 4 agents IA en mode simulation.
# Sans cette clé, les agents affichent un contenu de fallback statique.
ANTHROPIC_API_KEY=
# Modèle optionnel (défaut : claude-opus-4-5)
ANTHROPIC_MODEL=claude-opus-4-5
# ── Agent météo — StormGlass (optionnel) ──────────────────────────────────
# Données météo live. Sans clé, l'agent météo utilise la climatologie LLM.
STORMGLASS_API_KEY=
ENVEOF
fi
API_LOG="$LOG_DIR/naviguide-api.log"
(cd "$API_DIR" && nohup $PYTHON main.py > "$API_LOG" 2>&1) &
API_PID=$!
echo "$API_PID" > "$LOG_DIR/naviguide-api.pid"
success "naviguide-api started (PID $API_PID)"

# ── Service 2: Orchestrator (port 3008) ───────────────────────────────────────
info "Starting orchestrator on :3008..."
ORCH_LOG="$LOG_DIR/orchestrator.log"
(cd "$SCRIPT_DIR" && PORT=3008 nohup $PYTHON -m naviguide_orchestrator.main > "$ORCH_LOG" 2>&1) &
ORCH_PID=$!
echo "$ORCH_PID" > "$LOG_DIR/orchestrator.pid"
success "orchestrator started (PID $ORCH_PID)"

# ── Service 3: Weather Routing (port 3010) ────────────────────────────────────
info "Starting weather-routing on :3010..."
WEATHER_LOG="$LOG_DIR/weather-routing.log"
(cd "$SCRIPT_DIR" && PORT=3010 nohup $PYTHON -m naviguide_weather_routing.main > "$WEATHER_LOG" 2>&1) &
WEATHER_PID=$!
echo "$WEATHER_PID" > "$LOG_DIR/weather-routing.pid"
success "weather-routing started (PID $WEATHER_PID)"

# ── Service 4: Polar API (port 8001) ──────────────────────────────────────────
# polar_api/ lives inside naviguide_workspace/ (= SCRIPT_DIR), NOT at PROJECT_ROOT
info "Starting polar-api on :8001..."
POLAR_LOG="$LOG_DIR/polar_api.log"
(cd "$SCRIPT_DIR/polar_api" && nohup $PYTHON -m uvicorn main:app --port 8001 --reload > "$POLAR_LOG" 2>&1) &
POLAR_PID=$!
echo "$POLAR_PID" > "$LOG_DIR/polar_api.pid"
success "polar-api started (PID $POLAR_PID)"

# ── Wait for backends to initialise ──────────────────────────────────────────
info "Waiting 18 s for backends to initialise..."
sleep 18

# ── Health checks ─────────────────────────────────────────────────────────────
check() {
    local name=$1 url=$2
    if curl -sf "$url" -o /dev/null 2>/dev/null; then
        success "$name  →  $url"
    else
        warn "$name not responding yet — check $LOG_DIR"
    fi
}
check "naviguide-api"     "http://localhost:8000/"
check "orchestrator"      "http://localhost:3008/"
check "weather-routing"   "http://localhost:3010/"
check "polar-api"         "http://localhost:8001/"

# ── Service 5: Frontend (Vite) ────────────────────────────────────────────────
info "Starting Vite frontend on :5173..."
FRONT_DIR="$PROJECT_ROOT/naviguide-app"

# Override .env to point all URLs to localhost services
cat > "$FRONT_DIR/.env.local" <<'ENVEOF'
VITE_API_URL=http://localhost:8000
VITE_ORCHESTRATOR_URL=http://localhost:3008
VITE_WEATHER_ROUTING_URL=http://localhost:3010
VITE_POLAR_API_URL=http://localhost:8001
ENVEOF

FRONT_LOG="$LOG_DIR/frontend.log"
(cd "$FRONT_DIR" && nohup npm run dev -- --host --port 5173 > "$FRONT_LOG" 2>&1) &
FRONT_PID=$!
echo "$FRONT_PID" > "$LOG_DIR/frontend.pid"
success "frontend started (PID $FRONT_PID)"

sleep 4

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}══════════════════════════════════════════════════${NC}"
echo -e "${BOLD}  NAVIGUIDE is running locally!${NC}"
echo -e "${GREEN}══════════════════════════════════════════════════${NC}"
echo -e "  ${CYAN}Frontend :${NC}         http://localhost:5173"
echo -e "  ${CYAN}API :${NC}              http://localhost:8000"
echo -e "  ${CYAN}Orchestrator :${NC}     http://localhost:3008"
echo -e "  ${CYAN}Weather Routing :${NC}  http://localhost:3010"
echo -e "  ${CYAN}Polar API :${NC}        http://localhost:8001"
echo ""
echo -e "  Logs  →  $LOG_DIR/"
echo -e "  Stop  →  ${BOLD}./naviguide_workspace/stop_local.sh${NC}"
echo -e "${GREEN}══════════════════════════════════════════════════${NC}"
