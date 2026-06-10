# Enterprise AI Calling Platform

Native WebSocket audio streaming. Target latency < 500ms.
FreeSWITCH + Python asyncio bridge + OpenAI Realtime + n8n.

## Quick start

```bash
git clone https://github.com/YOUR_USERNAME/ai-calling-platform
cd ai-calling-platform
cp .env.example .env
# Edit .env — set SERVER_IP, OPENAI_API_KEY, SIP credentials
SIGNALWIRE_TOKEN=your_token bash scripts/install.sh
bash scripts/test_call.sh
```

## File structure

```
ai-calling-platform/
├── bridge/               Python asyncio media bridge
│   ├── fast_ai_bridge.py   Main bridge — ESL ↔ OpenAI WS
│   ├── audio_utils.py      PCMU ↔ PCM16 conversion
│   ├── metrics.py          Prometheus metrics exporter
│   └── requirements.txt
├── freeswitch/           FreeSWITCH config files
│   ├── vars.xml
│   ├── dialplan/default.xml
│   ├── sip_profiles/trunk.xml
│   └── ivr/main_ivr.xml
├── n8n/workflows/        n8n workflow JSON exports
├── monitoring/           Prometheus + Grafana docker-compose
├── systemd/              systemd unit files
├── scripts/              install.sh, test_call.sh, health_check.sh
├── prompts/              System prompts (markdown)
└── .env.example
```

## Services and ports

| Service        | Port  | Proto |
|----------------|-------|-------|
| FreeSWITCH SIP | 5060  | UDP   |
| RTP media      | 16384-32768 | UDP |
| ESL            | 8021  | TCP   |
| AI Bridge      | 5000  | TCP   |
| n8n            | 3000  | HTTP  |
| Prometheus     | 9090  | HTTP  |
| Grafana        | 3001  | HTTP  |
| Bridge metrics | 8000  | HTTP  |

## Deploy sequence

1. `SIGNALWIRE_TOKEN=xxx bash scripts/install.sh`
2. Edit `/etc/freeswitch/vars.xml` — set IP
3. Edit `/etc/freeswitch/sip_profiles/external/ai_trunk.xml` — SIP creds
4. Edit `/opt/ai-bridge/.env` — OPENAI_API_KEY
5. `systemctl start freeswitch ai-bridge n8n`
6. `bash scripts/test_call.sh`
7. Test with Zoiper/Linphone → call your DID
8. Check `journalctl -u ai-bridge -f`

## Adding a new AI provider

Edit `bridge/fast_ai_bridge.py` — replace `OPENAI_WS_URL` and the
`session.update` payload with your provider's WebSocket endpoint.
See `AI_PROVIDER` in `.env.example` for the switch variable.
