# EduSmart Xiaozhi - Complete Setup & Deployment Guide

## 📋 Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Prerequisites](#prerequisites)
4. [Local Development](#local-development)
5. [Production Deployment (VPS)](#production-deployment-vps)
6. [HuggingFace Spaces](#huggingface-spaces)
7. [GitHub Actions CI/CD](#github-actions-cicd)
8. [Security Configuration](#security-configuration)
9. [Environment Variables](#environment-variables)
10. [Domain & SSL](#domain--ssl)
11. [Troubleshooting](#troubleshooting)

---

## Overview

EduSmart Xiaozhi is a knowledge base platform with voice assistant integration, smart home control, and YouTube audio streaming.

**Tech Stack:**
- Backend: Python 3.11, FastAPI, Uvicorn
- Database: SQLite (production-ready)
- Deployment: Docker, Docker Compose
- CI/CD: GitHub Actions
- Platforms: VPS, HuggingFace Spaces

**Production URLs:**
- 🌐 **Main:** https://xiaozhiscig.biz.id
- 🌐 **WWW:** https://www.xiaozhiscig.biz.id
- 🔒 **SSL:** Let's Encrypt (auto-renew)

---

## Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   GitHub Repo   │────▶│ GitHub Actions  │────▶│   VPS (Debian)  │
│  (Source Code)  │     │  (Auto Deploy)  │     │  Docker Compose │
└─────────────────┘     └─────────────────┘     └─────────────────┘
        │                                               │
        │                                               ▼
        │                                       ┌─────────────────┐
        │                                       │   xiaozhi app   │
        │                                       │   Port: 80      │
        │                                       └─────────────────┘
        │
        ▼
┌─────────────────┐
│ HuggingFace     │
│ Spaces (Backup) │
└─────────────────┘
```

**VPS Details:**
- IP: `163.61.58.235`
- OS: Debian 13 (Trixie)
- RAM: ~1GB (with 2GB swap)
- App Location: `/opt/xiaozhi`
- User: `irsyad` (with sudo and docker access)

---

## Prerequisites

### For Local Development
- Python 3.11+
- Git
- Docker (optional)

### For VPS Deployment
- VPS with Debian/Ubuntu
- Domain name (optional, for SSL)
- GitHub account
- HuggingFace account (optional)

---

## Local Development

### 1. Clone Repository

```bash
git clone https://github.com/Franklnir/xiaozhi-knowledge-base.git
cd xiaozhi-knowledge-base
```

### 2. Create Virtual Environment

```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
# or
venv\Scripts\activate  # Windows
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Create .env File

```bash
cp .env.example .env
# Edit .env with your settings
```

### 5. Run Development Server

```bash
python -m uvicorn xiaozhi.main:app --reload --host 0.0.0.0 --port 7860
```

Access: http://localhost:7860

---

## Production Deployment (VPS)

### First Time Setup

1. **SSH into VPS:**
   ```bash
   ssh irsyad@163.61.58.235
   ```

2. **Run setup script** (already done):
   ```bash
   sudo /opt/xiaozhi/setup-vps.sh
   ```

3. **Configure .env:**
   ```bash
   cd /opt/xiaozhi
   nano .env
   ```
   
   Required variables:
   ```
   APP_SECRET_KEY=<generated>
   JWT_SECRET=<generated>
   DATA_ENCRYPTION_KEY=<generated>
   HF_TOKEN=hf_your_token
   HF_DATASET_REPO=username/repo
   ENVIRONMENT=production
   ```

4. **Start services:**
   ```bash
   docker compose up -d --build
   ```

### Manual Deployment

```bash
ssh irsyad@163.61.58.235
cd /opt/xiaozhi
git pull origin main
docker compose down
docker compose up -d --build
docker system prune -f
```

### Check Status

```bash
# View running containers
docker ps

# View logs
docker logs xiaozhi
docker logs -f xiaozhi  # Follow logs

# Check health
curl http://localhost/login
```

---

## HuggingFace Spaces

### Setup Auto-Deploy

1. Go to HuggingFace Space settings
2. Connect to GitHub repository
3. Set environment variables in Space secrets:
   - `APP_SECRET_KEY`
   - `JWT_SECRET`
   - `DATA_ENCRYPTION_KEY`
   - `HF_TOKEN`
   - `HF_DATASET_REPO`

### Push to Deploy

```bash
git push origin main  # Deploys to HuggingFace Spaces
git push github main  # Deploys to VPS via GitHub Actions
```

---

## GitHub Actions CI/CD

### Workflow File

Location: `.github/workflows/deploy.yml`

```yaml
name: Deploy to VPS

on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    
    steps:
      - name: Deploy via SSH
        uses: appleboy/ssh-action@v1
        with:
          host: ${{ secrets.VPS_HOST }}
          username: irsyad
          key: ${{ secrets.VPS_SSH_KEY }}
          script: |
            cd /opt/xiaozhi
            git pull origin main
            docker compose down
            docker compose up -d --build
            docker system prune -f
```

### Required GitHub Secrets

Go to: Repository → Settings → Secrets and variables → Actions

| Secret | Description |
|--------|-------------|
| `VPS_HOST` | VPS IP address: `163.61.58.235` |
| `VPS_SSH_KEY` | SSH private key for `irsyad` user |

### Generate SSH Key for GitHub Actions

```bash
# On your local machine
ssh-keygen -t ed25519 -C "github-actions" -f ~/.ssh/github_actions

# Copy public key to VPS
ssh-copy-id -i ~/.ssh/github_actions.pub irsyad@163.61.58.235

# Copy private key content to GitHub secret VPS_SSH_KEY
cat ~/.ssh/github_actions
```

---

## Security Configuration

### VPS User Setup

| User | Purpose | Access |
|------|---------|--------|
| `irsyad` | Main user | sudo, docker |
| `root` | Emergency only | Should be disabled |

### SSH Security (Recommended)

Disable root login and password authentication:

```bash
ssh irsyad@163.61.58.235
sudo nano /etc/ssh/sshd_config
```

Set:
```
PermitRootLogin no
PasswordAuthentication no
PubkeyAuthentication yes
```

Restart SSH:
```bash
sudo systemctl restart sshd
```

### Docker Security

`irsyad` can run Docker without sudo (already configured):

```bash
# Verify
docker ps  # Should work without sudo
```

### Application Security

All secrets are stored in `/opt/xiaozhi/.env`:
- `APP_SECRET_KEY` - Session encryption
- `JWT_SECRET` - JWT token signing
- `DATA_ENCRYPTION_KEY` - Data encryption

**Never commit `.env` to git!**

---

## Environment Variables

### Required Variables

| Variable | Description | How to Generate |
|----------|-------------|-----------------|
| `APP_SECRET_KEY` | Session encryption key | `python3 -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `JWT_SECRET` | JWT signing key | `python3 -c "import secrets; print(secrets.token_urlsafe(64))"` |
| `DATA_ENCRYPTION_KEY` | Data encryption key | `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `HF_TOKEN` | HuggingFace API token | https://huggingface.co/settings/tokens |
| `HF_DATASET_REPO` | Dataset repository | `username/repo-name` |
| `ENVIRONMENT` | Environment mode | `production` |

### Optional Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ADMIN_USERNAME` | `admin` | Admin username |
| `ADMIN_PASSWORD` | *(auto-generated if empty)* | Admin password (set in `.env`) |
| `COOKIE_SECURE` | `true` | HTTPS cookies |
| `ALLOWED_HOSTS` | `*` | Allowed hosts |
| `LOG_LEVEL` | `INFO` | Logging level |

---

## Troubleshooting

### App Won't Start

```bash
# Check logs
docker logs xiaozhi

# Common issue: Missing JWT_SECRET
# Solution: Add JWT_SECRET to .env
```

### Permission Denied

```bash
# Fix file permissions
sudo chown -R irsyad:irsyad /opt/xiaozhi
```

### Docker Permission Error

```bash
# Ensure irsyad is in docker group
sudo usermod -aG docker irsyad
# Logout and login again
```

### Port Already in Use

```bash
# Find process using port 80
sudo lsof -i :80

# Kill process
sudo kill -9 <PID>
```

### Database Issues

```bash
# Backup database
cp /opt/xiaozhi/data/xiaozhi.db /opt/xiaozhi/data/xiaozhi.db.backup

# Reset database (WARNING: loses all data)
rm /opt/xiaozhi/data/xiaozhi.db
docker compose restart
```

### GitHub Actions Deployment Failed

1. Check GitHub Secrets are set correctly
2. Verify SSH key has access to VPS
3. Check VPS is accessible
4. View GitHub Actions logs for details

---

## Quick Reference Commands

### VPS Management

```bash
# SSH into VPS
ssh irsyad@163.61.58.235

# Navigate to app
cd /opt/xiaozhi

# View logs
docker logs xiaozhi
docker logs -f xiaozhi  # Follow

# Restart app
docker compose restart

# Rebuild and restart
docker compose down
docker compose up -d --build

# Check status
docker ps
curl http://localhost/login

# Clean up Docker
docker system prune -f
```

### Git Commands

```bash
# Push to HuggingFace (auto-deploy)
git push origin main

# Push to GitHub (triggers VPS deploy)
git push github main

# Push to both
git push origin main && git push github main
```

---

## File Structure

```
/opt/xiaozhi/
├── .env                    # Environment variables (NOT in git)
├── .env.example            # Example env file
├── .gitignore              # Git ignore rules
├── docker-compose.yml      # Docker compose config
├── Dockerfile.prod         # Production Dockerfile
├── requirements.txt        # Python dependencies
├── setup-vps.sh            # VPS setup script
├── xiaozhi/                # Application code
│   ├── config.py           # Configuration
│   ├── main.py             # FastAPI app
│   ├── database/           # Database layer
│   ├── services/           # Business logic
│   ├── routers/            # API routes
│   └── mcp/                # MCP integration
├── templates/              # HTML templates
└── data/                   # SQLite database
    └── xiaozhi.db
```

---

## Support

For issues or questions:
1. Check logs: `docker logs xiaozhi`
2. Check this documentation
3. Check GitHub Issues

---

**Last Updated:** 2026-09-12
**Maintained By:** AI Assistant (CommandCode)
