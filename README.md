# SMAI — Enterprise AI Calling Platform

Native WebSocket audio streaming · FreeSWITCH · OpenAI Realtime · Sub-500ms target

## Architecture
```
PSTN/SIP → FreeSWITCH → ESL outbound socket → Python bridge → OpenAI Realtime WS
                                                     ↓
                                               n8n workflows → CRM / Transfer
```

## Quick start
```bash
git clone https://github.com/Mkali10/SMAI
cd SMAI
cp .env.example .env
# Edit .env: SERVER_IP, OPENAI_API_KEY, SIP creds, change all passwords
SIGNALWIRE_TOKEN=your_token bash scripts/install.sh
```

## Key fixes in this version
- Proper FreeSWITCH outbound ESL handshake (not raw TCP)
- Correct audio pipeline: PCMU 8kHz ↔ PCM16 16/24kHz
- Safe installer: no blind ufw reset, no apt upgrade
- Ubuntu 22.04 jammy repo (not focal)
- Minimal FreeSWITCH modules only
- No hardcoded passwords in defaults
- NAT traversal params for cloud
- DID ACL filtering
- AI failure failover → agent transfer → voicemail
