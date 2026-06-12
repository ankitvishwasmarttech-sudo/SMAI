#!/bin/bash
# SMAI Install Script — FreeSWITCH from source (Ubuntu 22.04)
set -euo pipefail
log()  { echo -e "\033[1;32m[$(date +%T)] $*\033[0m"; }
warn() { echo -e "\033[1;33m[WARN] $*\033[0m"; }
err()  { echo -e "\033[1;31m[ERROR] $*\033[0m" >&2; exit 1; }

[[ $EUID -ne 0 ]] && err "Run as root"

# ── 1. Base packages ──────────────────────────────────────────────────
log "Installing base packages..."
apt update -q
apt install -y --no-install-recommends \
    curl wget git vim net-tools htop \
    ufw fail2ban redis-server \
    docker.io docker-compose \
    python3.11 python3.11-venv python3-pip \
    gnupg2 lsb-release jq \
    build-essential cmake autoconf automake libtool libtool-bin \
    pkg-config libssl-dev libsqlite3-dev libcurl4-openssl-dev \
    libpcre3-dev libspeexdsp-dev libldns-dev libedit-dev \
    uuid-dev libopus-dev libsndfile1-dev libjpeg-dev libtiff-dev \
    libsofia-sip-ua-dev libpq-dev yasm nasm python3-distutils

# ── 2. Firewall — safe, no reset ─────────────────────────────────────
log "Configuring firewall..."
ufw allow OpenSSH        2>/dev/null || true
ufw allow 5060/udp       2>/dev/null || true
ufw allow 5060/tcp       2>/dev/null || true
ufw allow 16384:32768/udp 2>/dev/null || true
ufw allow 8021/tcp       2>/dev/null || true
ufw allow 5000/tcp       2>/dev/null || true
ufw allow 3000/tcp       2>/dev/null || true
ufw allow 3001/tcp       2>/dev/null || true
ufw allow 9090/tcp       2>/dev/null || true
ufw allow 8000/tcp       2>/dev/null || true
ufw status | grep -q "Status: active" || ufw --force enable
log "Firewall done"

# ── 3. SpanDSP ────────────────────────────────────────────────────────
log "Building SpanDSP..."
cd /usr/src
[[ -d spandsp ]] || git clone https://github.com/freeswitch/spandsp.git
cd spandsp
./bootstrap.sh && ./configure && make -j$(nproc) && make install
ldconfig

# ── 4. Sofia-SIP ──────────────────────────────────────────────────────
log "Building Sofia-SIP..."
cd /usr/src
[[ -d sofia-sip ]] || git clone https://github.com/freeswitch/sofia-sip.git
cd sofia-sip
./bootstrap.sh && ./configure && make -j$(nproc) && make install
ldconfig

# ── 5. libks ──────────────────────────────────────────────────────────
log "Building libks..."
cd /usr/src
[[ -d libks ]] || git clone https://github.com/signalwire/libks.git
cd libks
cmake . && make -j$(nproc) && make install
ldconfig

# ── 6. FreeSWITCH from source ─────────────────────────────────────────
log "Building FreeSWITCH (15-20 min)..."
cd /usr/src
[[ -d freeswitch ]] || git clone https://github.com/signalwire/freeswitch.git
cd freeswitch
git checkout v1.10.11

# Disable modules we don't need
cp modules.conf modules.conf.bak
sed -i 's|^applications/mod_verto|#applications/mod_verto|' modules.conf
sed -i 's|^applications/mod_signalwire|#applications/mod_signalwire|' modules.conf
sed -i 's|^applications/mod_av|#applications/mod_av|' modules.conf

./bootstrap.sh -j
./configure
make -j$(nproc)
make install
make samples

ln -sf /usr/local/freeswitch/bin/freeswitch /usr/bin/freeswitch
ln -sf /usr/local/freeswitch/bin/fs_cli /usr/bin/fs_cli
ldconfig
log "FreeSWITCH installed"

# ── 7. Python bridge ──────────────────────────────────────────────────
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

# ── 8. Copy FS config ─────────────────────────────────────────────────
log "Copying FreeSWITCH config..."
cp freeswitch/vars.xml              /usr/local/freeswitch/conf/vars.xml
cp freeswitch/dialplan/default.xml  /usr/local/freeswitch/conf/dialplan/default.xml
cp freeswitch/sip_profiles/trunk.xml /usr/local/freeswitch/conf/sip_profiles/external/ai_trunk.xml

# ── 9. n8n ────────────────────────────────────────────────────────────
log "Installing n8n..."
curl -fsSL https://deb.nodesource.com/setup_20.x | bash - >/dev/null
apt install -y nodejs
npm install -g n8n --quiet
cp systemd/n8n.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable n8n
log "n8n installed"

# ── 10. Redis + Docker services ───────────────────────────────────────
systemctl enable --now redis-server
log "Starting monitoring stack..."
cd monitoring && docker-compose up -d && cd ..

log ""
log "=== Installation complete ==="
log "NEXT STEPS:"
log "1. Edit /usr/local/freeswitch/conf/vars.xml       → YOUR_PUBLIC_IP"
log "2. Edit conf/sip_profiles/external/ai_trunk.xml  → SIP credentials"
log "3. Edit /opt/ai-bridge/.env                       → OPENAI_API_KEY"
log "4. systemctl start freeswitch ai-bridge n8n"
log "5. bash scripts/test_call.sh"
