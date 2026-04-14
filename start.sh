#!/usr/bin/env bash
# AgentPeXI — Script di avvio
# Avviato automaticamente da LaunchAgent al login, o manualmente.

set -euo pipefail

PROJECT_DIR="/Volumes/Progetti/AgentPeXI"
VENV_DIR="$PROJECT_DIR/.venv"
LOG_DIR="$PROJECT_DIR/logs"
STORAGE_PATH="${STORAGE_PATH:-/Volumes/Progetti/agentpexi-storage}"

mkdir -p "$LOG_DIR"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_DIR/start.log"
}

# 1. Attesa montaggio SSD
log "In attesa del montaggio SSD: $STORAGE_PATH ..."
MAX_WAIT=120  # secondi
WAITED=0
while [ ! -d "$STORAGE_PATH" ]; do
    sleep 1
    WAITED=$((WAITED + 1))
    if [ "$WAITED" -ge "$MAX_WAIT" ]; then
        log "ERRORE: SSD non montato dopo ${MAX_WAIT}s. Uscita."
        exit 1
    fi
done
log "SSD disponibile dopo ${WAITED}s."

# 2. Posizionamento nella directory progetto
cd "$PROJECT_DIR"

# 3. Attivazione virtualenv
if [ ! -f "$VENV_DIR/bin/activate" ]; then
    log "ERRORE: virtualenv non trovato in $VENV_DIR"
    exit 1
fi
source "$VENV_DIR/bin/activate"

# 4. Check frontend build — se dist/ manca, builda
if [ ! -d "apps/frontend/dist" ]; then
    log "Frontend build mancante, avvio npm run build ..."
    if command -v npm &>/dev/null; then
        (cd apps/frontend && npm install && npm run build)
        log "Frontend build completata."
    else
        log "ATTENZIONE: npm non trovato, frontend non disponibile."
    fi
else
    log "Frontend build già presente."
fi

# 5. Avvio uvicorn
log "Avvio uvicorn su porta ${PORT:-8000} ..."
exec uvicorn apps.backend.api.main:app \
    --host 0.0.0.0 \
    --port "${PORT:-8000}" \
    --log-level info \
    2>&1 | tee -a "$LOG_DIR/uvicorn.log"
