#!/bin/bash
# Test the full call stack end-to-end
# Usage: bash scripts/test_call.sh [DID_NUMBER]

set -euo pipefail
log()  { echo -e "\033[1;34m[TEST] $*\033[0m"; }
ok()   { echo -e "\033[1;32m[ OK ] $*\033[0m"; }
fail() { echo -e "\033[1;31m[FAIL] $*\033[0m"; }
warn() { echo -e "\033[1;33m[WARN] $*\033[0m"; }

log "=== AI Calling Platform — System Test ==="

# 1. FreeSWITCH status
log "Checking FreeSWITCH..."
if fs_cli -x "status" 2>/dev/null | grep -q "RUNNING"; then
    ok "FreeSWITCH is RUNNING"
    SESSIONS=$(fs_cli -x "show calls count" 2>/dev/null | grep -oP "\d+" | head -1 || echo "0")
    ok "Active sessions: $SESSIONS"
else
    fail "FreeSWITCH not running — run: systemctl start freeswitch"
fi

# 2. SIP registration
log "Checking SIP trunk registration..."
REG=$(fs_cli -x "sofia status gateway primary_trunk" 2>/dev/null || echo "")
if echo "$REG" | grep -q "REGED\|UP"; then
    ok "SIP trunk registered"
else
    warn "SIP trunk not registered — check /etc/freeswitch/sip_profiles/external/ai_trunk.xml"
fi

# 3. ESL connectivity
log "Checking ESL..."
if nc -z 127.0.0.1 8021 2>/dev/null; then
    ok "ESL port 8021 is open"
else
    fail "ESL port 8021 not open"
fi

# 4. Bridge service
log "Checking AI bridge..."
if systemctl is-active --quiet ai-bridge; then
    ok "ai-bridge.service is active"
elif nc -z 127.0.0.1 5000 2>/dev/null; then
    ok "Bridge port 5000 is open"
else
    fail "Bridge not running — run: systemctl start ai-bridge"
fi

# 5. n8n
log "Checking n8n..."
if curl -sf http://127.0.0.1:3000/healthz > /dev/null 2>&1; then
    ok "n8n is reachable on :3000"
else
    warn "n8n not responding on :3000 — run: systemctl start n8n"
fi

# 6. Redis
log "Checking Redis..."
if redis-cli ping 2>/dev/null | grep -q "PONG"; then
    ok "Redis is running"
else
    warn "Redis not running — run: systemctl start redis-server"
fi

# 7. Monitoring
log "Checking Prometheus..."
if curl -sf http://127.0.0.1:9090/-/healthy > /dev/null 2>&1; then
    ok "Prometheus healthy"
else
    warn "Prometheus not reachable — run: cd monitoring && docker-compose up -d"
fi

# 8. OpenAI API key
log "Checking OpenAI API key..."
ENV_FILE=${ENV_FILE:-/opt/ai-bridge/.env}
if [[ -f "$ENV_FILE" ]]; then
    KEY=$(grep OPENAI_API_KEY "$ENV_FILE" | cut -d= -f2 | tr -d \'\")
    if [[ "$KEY" == sk-* ]]; then
        ok "OPENAI_API_KEY is set (${KEY:0:8}...)"
    else
        warn "OPENAI_API_KEY not set in $ENV_FILE"
    fi
fi

# 9. Optional: originate test call
DID=${1:-""}
if [[ -n "$DID" ]]; then
    log "Originating test call to $DID via echo app..."
    fs_cli -x "originate sofia/gateway/primary_trunk/$DID &echo" || warn "Originate failed"
fi

log ""
log "=== Test complete ==="
echo ""
echo "If all checks pass, you are ready for live calls."
echo "Monitor logs:"
echo "  journalctl -u ai-bridge -f"
echo "  journalctl -u freeswitch -f"
echo "  tail -f /var/log/freeswitch/freeswitch.log"
