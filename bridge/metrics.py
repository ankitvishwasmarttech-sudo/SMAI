from prometheus_client import Counter, Gauge, Histogram, start_http_server
import os

ACTIVE_CALLS_GAUGE  = Gauge("ai_bridge_active_calls", "Live AI calls")
BARGE_IN_COUNTER    = Counter("ai_bridge_barge_ins_total", "Barge-in events")
CALL_DURATION_HIST  = Histogram("ai_bridge_call_duration_seconds", "Call duration",
                                 buckets=[5,15,30,60,120,300])
AI_FAILURE_COUNTER  = Counter("ai_bridge_ai_failures_total", "OpenAI WS failures")
TRANSFER_COUNTER    = Counter("ai_bridge_transfers_total", "Agent transfers", ["reason"])

def start_metrics_server(port=8000):
    start_http_server(port)
