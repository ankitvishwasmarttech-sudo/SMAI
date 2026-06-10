"""
Prometheus metrics for the AI bridge.
Exposed on :8000/metrics — scrape from prometheus.yml
"""
from prometheus_client import Counter, Gauge, Histogram, start_http_server
import os

ACTIVE_CALLS_GAUGE  = Gauge("ai_bridge_active_calls",    "Currently active AI calls")
BARGE_IN_COUNTER    = Counter("ai_bridge_barge_ins_total","Total barge-in events")
CALL_DURATION_HIST  = Histogram("ai_bridge_call_duration_seconds",
                                 "Call duration", buckets=[10,30,60,120,300,600])
ERROR_COUNTER       = Counter("ai_bridge_errors_total",  "Bridge errors", ["type"])

def start_metrics_server(port: int = 8000):
    start_http_server(port)

if __name__ == "__main__":
    start_metrics_server(int(os.getenv("METRICS_PORT", 8000)))
    print("Metrics server on :8000")
    import time; time.sleep(9999)
