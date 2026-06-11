# SMAI — Enterprise AI Calling Platform

Native WebSocket audio streaming · FreeSWITCH · OpenAI Realtime · Sub-500ms target

## Architecture
## Quick start
```bash
git clone https://github.com/ankitvishwasmarttech-sudo/SMAI
cd SMAI
cp .env.example .env
# Edit .env: SERVER_IP, OPENAI_API_KEY, SIP creds, change all passwords
SIGNALWIRE_TOKEN=your_token bash scripts/install.sh
```

## Services & ports

| Service | Port | Protocol |
|---------|------|----------|
| FreeSWITCH SIP | 5060 | UDP/TCP |
| RTP media | 16384–32768 | UDP |
| ESL control | 8021 | TCP |
| AI Bridge | 5000 | TCP |
| n8n workflows | 3000 | HTTP |
| Prometheus | 9090 | HTTP |
| Grafana | 3001 | HTTP |
| Bridge metrics | 8000 | HTTP |

## Deploy sequence

1. `SIGNALWIRE_TOKEN=xxx bash scripts/install.sh`
2. Edit `/etc/freeswitch/vars.xml` — set public IP
3. Edit `sip_profiles/external/ai_trunk.xml` — SIP credentials
4. Edit `/opt/ai-bridge/.env` — OPENAI_API_KEY + all passwords
5. `systemctl start freeswitch ai-bridge n8n`
6. `bash scripts/test_call.sh`

## Key fixes in this version
- Proper FreeSWITCH outbound ESL handshake
- Correct audio pipeline: PCMU 8kHz ↔ PCM16 16/24kHz
- Safe installer: no blind ufw reset
- Ubuntu 22.04 jammy repo
- Minimal FreeSWITCH modules only
- No hardcoded passwords
- NAT traversal params for cloud
- DID ACL filtering
- AI failure failover → agent transfer → voicemail
