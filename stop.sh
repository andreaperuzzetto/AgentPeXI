#!/usr/bin/env bash
# AgentPeXI — Script di arresto manuale

set -euo pipefail

LOG_DIR="/Volumes/Progetti/AgentPeXI/logs"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_DIR/stop.log"
}

PLIST_LABEL="com.agentpexi"

# Prova prima via launchctl (se gestito da LaunchAgent)
if launchctl list | grep -q "$PLIST_LABEL"; then
    log "Arresto AgentPeXI via launchctl ..."
    launchctl stop "$PLIST_LABEL"
    log "LaunchAgent stoppato."
else
    # Fallback: kill processo uvicorn
    PIDS=$(pgrep -f "uvicorn apps.backend.api.main:app" || true)
    if [ -n "$PIDS" ]; then
        log "Arresto uvicorn (PID: $PIDS) ..."
        kill $PIDS
        sleep 2
        # Forza kill se ancora attivo
        REMAINING=$(pgrep -f "uvicorn apps.backend.api.main:app" || true)
        if [ -n "$REMAINING" ]; then
            log "Forza arresto (SIGKILL) ..."
            kill -9 $REMAINING
        fi
        log "AgentPeXI arrestato."
    else
        log "Nessun processo AgentPeXI trovato."
    fi
fi
