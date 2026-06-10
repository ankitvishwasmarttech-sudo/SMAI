#!/bin/bash
# Quick health check — run from cron every 5 min
# 0/5 * * * * /opt/ai-bridge/health_check.sh >> /var/log/ai-health.log 2>&1

TS=$(date "+%Y-%m-%d %H:%M:%S")
ALERT_URL=${TELEGRAM_WEBHOOK:-""}

alert() {
    echo "[$TS] ALERT: $1"
    if [[ -n "$ALERT_URL" ]]; then
        curl -sf -X POST "$ALERT_URL" -d "text=AI-CALLING ALERT: $1" > /dev/null 2>&1 || true
    fi
}

# FreeSWITCH
if ! systemctl is-active --quiet freeswitch; then
    alert "freeswitch DOWN — restarting"
    systemctl restart freeswitch
fi

# AI Bridge
if ! systemctl is-active --quiet ai-bridge; then
    alert "ai-bridge DOWN — restarting"
    systemctl restart ai-bridge
fi

# n8n
if ! curl -sf http://127.0.0.1:3000/healthz > /dev/null 2>&1; then
    alert "n8n DOWN — restarting"
    systemctl restart n8n
fi

# Latency check from bridge metrics
LATENCY=$(curl -sf http://127.0.0.1:8000/metrics 2>/dev/null \
  | grep "ai_bridge_call_duration" | awk '{print $2}' | head -1 || echo "")
echo "[$TS] OK — bridge=${LATENCY}ms"
