#!/bin/bash
# ============================================================
# NAVIGUIDE-BERRY  — Unified Multi-Agent Startup Script
# Services:
#   8000  Base API        (searoute + Copernicus)
#   8001  Agent 1         (Route Intelligence / anti-shipping)
#   8002  Orchestrator    (coordinator + LLM briefing)
#   8003  Agent 3         (Risk Assessment)
#   3005  Frontend proxy  (React app + service proxies)
# ============================================================

BASE="/mnt/efs/spaces/ef014a98-8a1c-4b16-8e06-5d2c5b364d08/e40cd69a-a887-4dd9-9d40-f7f8bf47412e"
LOGS="$BASE/logs"
API_DIR="$BASE/naviguide/naviguide-api-main"
AGENT1_DIR="$BASE/naviguide-berry/agent1"
AGENT3_DIR="$BASE/naviguide-berry/agent3"
ORCH_DIR="$BASE/naviguide-berry/orchestrator"
PROXY_DIR="$BASE/naviguide"

export PATH="$HOME/.local/bin:$PATH"
mkdir -p "$LOGS"

echo "🚢  Starting NAVIGUIDE-BERRY multi-agent system..."

# --- 1. Base API (port 8000) ---
echo "  [1/5] Base API        → port 8000"
cd "$API_DIR"
nohup python3 -m uvicorn main:app --host 0.0.0.0 --port 8000 \
  > "$LOGS/naviguide-api.log" 2>&1 &
echo $! > "$LOGS/pid_api.txt"

# --- 2. Agent 1 – Route Intelligence (port 8001) ---
echo "  [2/5] Agent 1         → port 8001"
cd "$AGENT1_DIR"
nohup python3 -m uvicorn main:app --host 0.0.0.0 --port 8001 \
  > "$LOGS/naviguide-agent1.log" 2>&1 &
echo $! > "$LOGS/pid_agent1.txt"

# --- 3. Agent 3 – Risk Assessment (port 8003) ---
echo "  [3/5] Agent 3         → port 8003"
cd "$AGENT3_DIR"
nohup python3 -m uvicorn main:app --host 0.0.0.0 --port 8003 \
  > "$LOGS/naviguide-agent3.log" 2>&1 &
echo $! > "$LOGS/pid_agent3.txt"

# --- 4. Orchestrator (port 8002) ---
echo "  [4/5] Orchestrator    → port 8002"
cd "$ORCH_DIR"
nohup python3 -m uvicorn main:app --host 0.0.0.0 --port 8002 \
  > "$LOGS/naviguide-orchestrator.log" 2>&1 &
echo $! > "$LOGS/pid_orchestrator.txt"

# Wait for Python services to bind
sleep 6

# --- 5. Frontend proxy (port 3005) ---
echo "  [5/5] Frontend proxy  → port 3005"
cd "$PROXY_DIR"
nohup node server.js \
  > "$LOGS/naviguide-frontend.log" 2>&1 &
echo $! > "$LOGS/pid_frontend.txt"

sleep 3

# --- Health checks ---
echo ""
echo "  Health checks:"
for svc in "8000:Base API" "8001:Agent 1" "8002:Orchestrator" "8003:Agent 3" "3005:Frontend"; do
  port="${svc%%:*}"; name="${svc##*:}"
  if curl -sf "http://localhost:$port/" > /dev/null 2>&1; then
    echo "    ✅  $name (port $port)"
  else
    echo "    ⚠️   $name (port $port) — still starting"
  fi
done

echo ""
echo "✅  NAVIGUIDE-BERRY is running!"
echo "   Public URL : https://fg6cpxl7.run.complete.dev"
echo "   Logs       : $LOGS/"
