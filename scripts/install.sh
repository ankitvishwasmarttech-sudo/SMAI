#!/bin/bash
# Safe installer — Fix #3: no blind ufw reset, no auto apt upgrade
# Fix #4: correct Ubuntu 22.04 jammy FreeSWITCH repo
set -euo pipefail
log()  { echo -e "\033[1;32m[$(date +%T)] $*\033[0m"; }
warn() { echo -e "\033[1;33m[WARN] $*\033[0m"; }
err()  { echo -e "\033[1;31m[ERROR] $*\033[0m" >&2; exit 1; }

[[ $EUID -ne 0 ]] && err "Run as root"

OS=$(lsb_release -sc 2>/dev/null || echo "unknown")
log "Detected OS codename: $OS"

# ── Fix #3: Safe apt — no blind upgrade ──────────────────────────────────────
log "Installing base packages..."
apt update -q
# Only install what's needed, no dist-upgrade
apt install -y --no-install-recommends \
    curl wget git vim net-tools htop \
    ufw fail2ban redis-server \
    docker.io docker-compose \
    python3.11 python3.11-venv python3-pip \
    gnupg2 lsb-release jq

# ── Fix #3: Safe firewall — add rules, don't reset ───────────────────────────
log "Configuring firewall (adding rules, NOT resetting)..."
ufw allow OpenSSH        2>/dev/null || true
ufw allow 5060/udp       2>/dev/null || true
ufw allow 5060/tcp       2>/dev/null || true
ufw allow 5080/udp       2>/dev/null || true
ufw allow 16384:32768/udp 2>/dev/null || true
ufw allow 8021/tcp       2>/dev/null || true
ufw allow 5000/tcp       2>/dev/null || true
ufw allow 3000/tcp       2>/dev/null || true
ufw allow 3001/tcp       2>/dev/null || true
ufw allow 9090/tcp       2>/dev/null || true
ufw allow 8000/tcp       2>/dev/null || true
# Only enable if not already active
ufw status | grep -q "Status: active" || ufw --force enable
log "Firewall rules added"

# ── Fix #4: Correct repo for Ubuntu 22.04 jammy ──────────────────────────────
log "Installing FreeSWITCH (Ubuntu 22.04 jammy repo)..."
TOKEN=${SIGNALWIRE_TOKEN:-""}
[[ -z "$TOKEN" ]] && err "Set SIGNALWIRE_TOKEN env var (free at signalwire.com)"

wget -q --http-user=signalwire --http-password=$TOKEN \
     -O /usr/share/keyrings/signalwire-freeswitch-repo.gpg \
     https://freeswitch.signalwire.com/repo/deb/debian-release/signalwire-freeswitch-repo.gpg

echo "machine freeswitch.signalwire.com login signalwire password $TOKEN" \
     > /etc/apt/auth.conf
chmod 600 /etc/apt/auth.conf

# Fix #4: Use jammy for Ubuntu 22.04, focal for 20.04
if [[ "$OS" == "jammy" ]]; then
    REPO_DIST="jammy"
elif [[ "$OS" == "focal" ]]; then
    REPO_DIST="focal"
else
    warn "Unknown OS $OS — defaulting to jammy"
    REPO_DIST="jammy"
fi

echo "deb [signed-by=/usr/share/keyrings/signalwire-freeswitch-repo.gpg] \
  https://freeswitch.signalwire.com/repo/deb/debian-release/ ${REPO_DIST} main" \
  > /etc/apt/sources.list.d/freeswitch.list

apt update -q

# Fix #5: Install only required modules, not meta-all
log "Installing FreeSWITCH (minimal modules only)..."
apt install -y \
    freeswitch \
    freeswitch-mod-sofia \
    freeswitch-mod-event-socket \
    freeswitch-mod-commands \
    freeswitch-mod-dptools \
    freeswitch-mod-loopback \
    freeswitch-mod-audio-stream \
    freeswitch-mod-dialplan-xml \
    freeswitch-mod-voicemail \
    freeswitch-sounds-en-us-callie

systemctl enable freeswitch
log "FreeSWITCH installed (minimal)"

# ── Python bridge ─────────────────────────────────────────────────────────────
log "Setting up Python bridge..."
mkdir -p /opt/ai-bridge
cp -r bridge/* /opt/ai-bridge/
cp -r prompts   /opt/ai-bridge/
[[ -f .env ]] && cp .env /opt/ai-bridge/.env || cp .env.example /opt/ai-bridge/.env

cd /opt/ai-bridge
python3.11 -m venv venv
source venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt
deactivate

cp systemd/ai-bridge.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable ai-bridge
log "Bridge installed"

# ── FreeSWITCH config ─────────────────────────────────────────────────────────
log "Copying FreeSWITCH config..."
cp freeswitch/vars.xml                       /etc/freeswitch/vars.xml
cp freeswitch/dialplan/default.xml           /etc/freeswitch/dialplan/default.xml
cp freeswitch/sip_profiles/trunk.xml         /etc/freeswitch/sip_profiles/external/ai_trunk.xml
cp freeswitch/ivr/main_ivr.xml               /etc/freeswitch/ivr_menus/main_ivr.xml 2>/dev/null || true

# ── n8n ───────────────────────────────────────────────────────────────────────
log "Installing n8n..."
curl -fsSL https://deb.nodesource.com/setup_20.x | bash - > /dev/null
apt install -y nodejs
npm install -g n8n --quiet
cp systemd/n8n.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable n8n
log "n8n installed"

# ── Redis ─────────────────────────────────────────────────────────────────────
systemctl enable --now redis-server

# ── Monitoring ────────────────────────────────────────────────────────────────
log "Starting monitoring stack..."
cd monitoring && docker-compose up -d && cd ..

log ""
log "=== Installation complete ==="
log "NEXT STEPS:"
log "1. Edit /etc/freeswitch/vars.xml        → YOUR_PUBLIC_IP, NAT settings"
log "2. Edit sip_profiles/external/ai_trunk.xml → SIP credentials"
log "3. Edit /opt/ai-bridge/.env             → OPENAI_API_KEY, correct ESL_PASSWORD"
log "4. systemctl start freeswitch ai-bridge n8n"
log "5. bash scripts/test_call.sh"
