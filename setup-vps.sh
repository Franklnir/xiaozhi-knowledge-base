#!/bin/bash
# EduSmart Xiaozhi - VPS Setup Script (1GB RAM)
# Tested on: Ubuntu 22.04/24.04, Debian 12
# Run: chmod +x setup.sh && sudo ./setup.sh

set -e

echo "============================================"
echo "  EduSmart Xiaozhi - VPS Setup (1GB RAM)"
echo "============================================"

# Check root
if [ "$EUID" -ne 0 ]; then
    echo "ERROR: Run as root (sudo ./setup.sh)"
    exit 1
fi

# Detect RAM
TOTAL_RAM=$(free -m | awk '/^Mem:/{print $2}')
echo "Detected RAM: ${TOTAL_RAM}MB"

# Setup swap (2GB)
echo "[1/7] Setting up 2GB swap..."
if [ ! -f /swapfile ]; then
    fallocate -l 2G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
    echo "  Swap: 2GB created"
else
    echo "  Swap: already exists"
fi

# Tune swap usage
echo 'vm.swappiness=10' >> /etc/sysctl.conf
echo 'vm.vfs_cache_pressure=50' >> /etc/sysctl.conf
sysctl -p > /dev/null 2>&1

# Install Docker
echo "[2/7] Installing Docker..."
if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com | sh
    systemctl enable docker
    systemctl start docker
    echo "  Docker: installed"
else
    echo "  Docker: already installed"
fi

# Install Docker Compose
echo "[3/7] Installing Docker Compose..."
if ! command -v docker-compose &> /dev/null; then
    curl -SL "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
    chmod +x /usr/local/bin/docker-compose
    echo "  Docker Compose: installed"
else
    echo "  Docker Compose: already installed"
fi

# Clone repo
echo "[4/7] Cloning repository..."
APP_DIR="/opt/edusmart"
if [ ! -d "$APP_DIR" ]; then
    mkdir -p $APP_DIR
    cd $APP_DIR
    git clone https://huggingface.co/spaces/Irsyadmiler/xiaozhi .
    echo "  Repo: cloned to $APP_DIR"
else
    cd $APP_DIR
    git pull origin main
    echo "  Repo: updated"
fi

# Create required directories
echo "[5/7] Creating directories..."
mkdir -p data

# Setup .env
echo "[6/7] Configuring environment..."
cd $APP_DIR
if [ ! -f .env ]; then
    cp .env.example .env
    
    # Auto-generate secrets
    APP_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))" 2>/dev/null || openssl rand -base64 32)
    JWT_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(64))" 2>/dev/null || openssl rand -base64 64)
    DATA_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" 2>/dev/null || echo "")
    
    # Replace placeholders
    sed -i "s/CHANGE_ME_GENERATE_RANDOM_STRING/$APP_SECRET/" .env
    sed -i "s/CHANGE_ME_GENERATE_RANDOM_STRING/$JWT_SECRET/" .env
    if [ -n "$DATA_KEY" ]; then
        sed -i "s/CHANGE_ME_GENERATE_FERNET_KEY/$DATA_KEY/" .env
    fi
    
    echo "  .env: created (edit HF_TOKEN manually!)"
    echo ""
    echo "  ============================================"
    echo "  IMPORTANT: Edit $APP_DIR/.env and set:"
    echo "    HF_TOKEN=hf_your_token_here"
    echo "    HF_DATASET_REPO=username/repo-name"
    echo "  ============================================"
else
    echo "  .env: already exists"
fi

# Build & start
echo "[7/7] Building and starting..."
cd $APP_DIR
docker compose -f docker-compose.prod.yml down 2>/dev/null || true
docker compose -f docker-compose.prod.yml up -d --build

echo ""
echo "============================================"
echo "  SETUP COMPLETE!"
echo "============================================"
echo ""
echo "  App:     http://$(hostname -I | awk '{print $1}')"
echo "  Logs:    docker logs -f edusmart"
echo "  Restart: cd $APP_DIR && docker compose -f docker-compose.prod.yml restart"
echo "  Update:  cd $APP_DIR && git pull && docker compose -f docker-compose.prod.yml up -d --build"
echo ""
echo "  Memory:  $(free -h | awk '/^Mem:/{print $2}') RAM + $(free -h | awk '/^Swap:/{print $2}') Swap"
echo "  Docker:  docker stats edusmart"
echo ""
