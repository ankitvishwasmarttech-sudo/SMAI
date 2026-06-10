#!/bin/bash
# Full server installation script
# Run as root on a fresh Ubuntu 22.04 LTS
# Usage: bash scripts/install.sh

set -euo pipefail
log() { echo -e "\033[1;32m[$(date +%T)] $*\033[0m"; }
err() { echo -e "\033[1;31m[ERROR] $*\033[0m" >&2; exit 1; }

[[ $EUID -ne 0 ]] && err "Run as root"

log "=== AI Calling Platform Installer ==="

# ── 1. System base ─────────────────────────────────────────────────────
log "Updating system..."
apt update && apt upgrade -y
apt install -y curl wget git vim net-tools htop ufw fail2ban \
               jq redis-server docker.io docker-compose \
               python3.11 python3.11-venv python3-pip

# ── 2. Firewall ────────────────────────────────────────────────────────
log "Configuring firewall..."
ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 5060/udp     # SIP
ufw allow 5060/tcp     # SIP TCP
ufw allow 5080/udp     # SIP external
ufw allow 5080/tcp
ufw allow 16384:32768/udp  # RTP
ufw allow 8021/tcp     # ESL
ufw allow 5000/tcp     # bridge
ufw allow 3000/tcp     # n8n
ufw allow 3001/tcp     # Grafana
ufw allow 9090/tcp     # Prometheus
ufw allow 8000/tcp     # bridge metrics
ufw --force enable
log "Firewall configured"

# ── 3. FreeSWITCH ──────────────────────────────────────────────────────
log "Installing FreeSWITCH..."
TOKEN=${SIGNALWIRE_TOKEN:-""}
if [[ -z "$TOKEN" ]]; then
    echo "┌─────────────────────────────────────────────────────────────┐"
    echo "│  Get a free SignalWire PAT at: https://signalwire.com       │"
    echo "│  Then run: SIGNALWIRE_TOKEN=your_token bash scripts/install.sh │"
    echo "└─────────────────────────────────────────────────────────────┘"
    err "SIGNALWIRE_TOKEN not set"
fi

apt install -y gnupg2 lsb-release
wget --http-user=signalwire --http-password=$TOKEN \
     -O /usr/share/keyrings/signalwire-freeswitch-repo.gpg \
     https://freeswitch.signalwire.com/repo/deb/debian-release/signalwire-freeswitch-repo.gpg

echo "machine freeswitch.signalwire.com login signalwire password $TOKEN" \
     > /etc/apt/auth.conf
chmod 600 /etc/apt/auth.conf

echo "deb [signed-by=/usr/share/keyrings/signalwire-freeswitch-repo.gpg] \
  https://freeswitch.signalwire.com/repo/deb/debian-release/ focal main" \
  > /etc/apt/sources.list.d/freeswitch.list

apt update
apt install -y freeswitch freeswitch-meta-all
systemctl enable freeswitch
log "FreeSWITCH installed"

# ── 4. Python bridge ───────────────────────────────────────────────────
log "Setting up Python bridge..."
mkdir -p /opt/ai-bridge
cp -r bridge/* /opt/ai-bridge/
cp .env /opt/ai-bridge/.env 2>/dev/null || cp .env.example /opt/ai-bridge/.env
cp -r prompts /opt/ai-bridge/

cd /opt/ai-bridge
python3.11 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
deactivate

cp systemd/ai-bridge.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable ai-bridge
log "Bridge installed"

# ── 5. FreeSWITCH config ───────────────────────────────────────────────
log "Copying FreeSWITCH config..."
cp freeswitch/vars.xml /etc/freeswitch/vars.xml
cp freeswitch/dialplan/default.xml /etc/freeswitch/dialplan/default.xml
cp freeswitch/sip_profiles/trunk.xml /etc/freeswitch/sip_profiles/external/ai_trunk.xml
cp freeswitch/ivr/main_ivr.xml /etc/freeswitch/ivr_menus/main_ivr.xml
log "Config copied — edit /etc/freeswitch/vars.xml with your IP and SIP credentials"

# ── 6. n8n ─────────────────────────────────────────────────────────────
log "Installing n8n..."
curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
apt install -y nodejs
npm install -g n8n
cp systemd/n8n.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable n8n
log "n8n installed"

# ── 7. Monitoring ──────────────────────────────────────────────────────
log "Starting monitoring stack..."
cd monitoring
docker-compose up -d
cd ..
log "Prometheus on :9090, Grafana on :3001"

# ── 8. Redis ───────────────────────────────────────────────────────────
systemctl enable --now redis-server
log "Redis running"

log ""
log "=== Installation complete ==="
log ""
log "NEXT STEPS:"
log "1. Edit /etc/freeswitch/vars.xml  → set YOUR_PUBLIC_IP"
log "2. Edit /etc/freeswitch/sip_profiles/external/ai_trunk.xml → SIP credentials"
log "3. Edit /opt/ai-bridge/.env → OPENAI_API_KEY + SERVER_IP"
log "4. systemctl start freeswitch"
log "5. systemctl start ai-bridge"
log "6. systemctl start n8n"
log "7. bash scripts/test_call.sh"
log ""
log "n8n UI:       http://$(hostname -I | awk '{print $1}'):3000"
log "Grafana:      http://$(hostname -I | awk '{print $1}'):3001"
log "Prometheus:   http://$(hostname -I | awk '{print $1}'):9090"
