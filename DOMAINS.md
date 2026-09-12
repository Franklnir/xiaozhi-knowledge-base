# Domain & SSL Management Guide

## 📋 Table of Contents

1. [Current Setup](#current-setup)
2. [Domain Configuration](#domain-configuration)
3. [SSL Certificate Management](#ssl-certificate-management)
4. [Nginx Configuration](#nginx-configuration)
5. [Adding New Domains](#adding-new-domains)
6. [Troubleshooting](#troubleshooting)
7. [Quick Reference](#quick-reference)

---

## Current Setup

| Item | Value |
|------|-------|
| **Domain** | `xiaozhiscig.biz.id` |
| **WWW** | `www.xiaozhiscig.biz.id` |
| **VPS IP** | `163.61.58.235` |
| **SSL** | Let's Encrypt (auto-renew) |
| **Certificate Expires** | 2026-12-11 |
| **Web Server** | Nginx 1.26.3 |
| **App Port** | 8080 (internal) |
| **Public Ports** | 80 (HTTP→HTTPS redirect), 443 (HTTPS) |

---

## Domain Configuration

### DNS Records Required

| Type | Name | Value | TTL |
|------|------|-------|-----|
| A | `@` | `163.61.58.235` | 300 |
| A | `www` | `163.61.58.235` | 300 |

### Where to Configure DNS

DNS dikonfigurasi di **registrar domain** tempat kamu membeli domain:
- Niagahoster
- Domainesia
- Rumahweb
- Atau registrar lainnya

### Check DNS Propagation

```bash
# Check A record
nslookup xiaozhiscig.biz.id

# Or use online tool
# https://dnschecker.org/#A/xiaozhiscig.biz.id
```

---

## SSL Certificate Management

### Current Certificate

```
Certificate: /etc/letsencrypt/live/xiaozhiscig.biz.id/fullchain.pem
Private Key: /etc/letsencrypt/live/xiaozhiscig.biz.id/privkey.pem
Expires: 2026-12-11
Auto-renew: Yes (certbot timer)
```

### Manual Renewal

```bash
# Test renewal
sudo certbot renew --dry-run

# Force renewal
sudo certbot renew --force-renewal

# Check certificate status
sudo certbot certificates
```

### Auto-Renewal

Certbot automatically renews certificates. Check timer:

```bash
sudo systemctl status certbot.timer
sudo certbot renew --dry-run
```

### Add SSL for New Domain

```bash
sudo certbot --nginx -d newdomain.com -d www.newdomain.com
```

---

## Nginx Configuration

### Config File Location

```
/etc/nginx/sites-available/xiaozhi
/etc/nginx/sites-enabled/xiaozhi
```

### Current Configuration

```nginx
server {
    listen 80;
    listen [::]:80;
    server_name xiaozhiscig.biz.id www.xiaozhiscig.biz.id;

    # Redirect HTTP to HTTPS
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl default_server;
    listen [::]:443 ssl default_server;

    server_name xiaozhiscig.biz.id www.xiaozhiscig.biz.id;

    # SSL Certificate
    ssl_certificate /etc/letsencrypt/live/xiaozhiscig.biz.id/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/xiaozhiscig.biz.id/privkey.pem;

    # SSL Security Settings
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers off;

    # Security Headers
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Referrer-Policy "no-referrer-when-downgrade" always;
    add_header Content-Security-Policy "default-src 'self' http: https: ws: wss: data: blob: 'unsafe-inline'; frame-ancestors 'self';" always;
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;

    # Proxy to Docker container
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;
    }

    # WebSocket support for MCP
    location /mcp {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 86400;
    }
}
```

### Edit Nginx Config

```bash
sudo nano /etc/nginx/sites-available/xiaozhi

# Test config
sudo nginx -t

# Reload nginx
sudo systemctl reload nginx
```

---

## Adding New Domains

### Step 1: Add DNS Record

Di registrar domain, tambahkan A record pointing ke `163.61.58.235`.

### Step 2: Update Nginx Config

```bash
sudo nano /etc/nginx/sites-available/xiaozhi
```

Tambahkan domain baru ke `server_name`:

```nginx
server_name xiaozhiscig.biz.id www.xiaozhiscig.biz.id newdomain.com www.newdomain.com;
```

### Step 3: Get SSL Certificate

```bash
sudo certbot --nginx -d newdomain.com -d www.newdomain.com
```

### Step 4: Reload Nginx

```bash
sudo nginx -t
sudo systemctl reload nginx
```

---

## Troubleshooting

### SSL Certificate Not Working

```bash
# Check certificate status
sudo certbot certificates

# Check nginx config
sudo nginx -t

# Check nginx logs
sudo tail -f /var/log/nginx/error.log
```

### HTTP Not Redirecting to HTTPS

```bash
# Check nginx config has redirect
cat /etc/nginx/sites-available/xiaozhi | grep -A5 "listen 80"

# Reload nginx
sudo systemctl reload nginx
```

### Certificate Renewal Failed

```bash
# Check certbot logs
sudo tail -f /var/log/letsencrypt/letsencrypt.log

# Manual renewal
sudo certbot renew --force-renewal

# Check certbot timer
sudo systemctl status certbot.timer
```

### Nginx Won't Start

```bash
# Check config syntax
sudo nginx -t

# Check error logs
sudo tail -f /var/log/nginx/error.log

# Check port 80/443 usage
sudo ss -tlnp | grep -E ':(80|443) '
```

---

## Quick Reference Commands

### Nginx

```bash
# Test config
sudo nginx -t

# Reload config
sudo systemctl reload nginx

# Restart nginx
sudo systemctl restart nginx

# Check status
sudo systemctl status nginx

# View logs
sudo tail -f /var/log/nginx/access.log
sudo tail -f /var/log/nginx/error.log
```

### SSL/Certbot

```bash
# List certificates
sudo certbot certificates

# Renew certificates
sudo certbot renew

# Test renewal
sudo certbot renew --dry-run

# Get new certificate
sudo certbot --nginx -d domain.com

# Delete certificate
sudo certbot delete --cert-name domain.com
```

### Docker

```bash
# Check container status
docker ps

# View logs
docker logs xiaozhi
docker logs -f xiaozhi

# Restart app
cd /opt/xiaozhi && docker compose restart

# Rebuild and restart
cd /opt/xiaozhi && docker compose down && docker compose up -d --build
```

---

## Architecture Diagram

```
Internet
    │
    ▼
┌─────────────────┐
│   Port 80       │ ──── HTTP redirect to HTTPS
│   Port 443      │ ──── HTTPS (SSL)
│   Nginx         │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   Port 8080     │ ──── Internal
│   Docker App    │
│   (FastAPI)     │
└─────────────────┘
```

---

## Security Checklist

- [x] SSL/HTTPS enabled
- [x] HTTP → HTTPS redirect
- [x] Firewall enabled (ufw)
- [x] fail2ban enabled
- [x] Security headers configured
- [x] Auto SSL renewal configured
- [x] Docker isolated (port 8080 internal)

---

## Maintenance Schedule

| Task | Frequency | Command |
|------|-----------|---------|
| Check SSL expiry | Monthly | `sudo certbot certificates` |
| Check fail2ban | Weekly | `sudo fail2ban-client status` |
| Update system | Weekly | `sudo apt update && sudo apt upgrade` |
| Check logs | Daily | `sudo tail -f /var/log/nginx/error.log` |
| Backup database | Daily | `cp /opt/xiaozhi/data/xiaozhi.db /backup/` |

---

**Last Updated:** 2026-09-12
**Maintained By:** AI Assistant (CommandCode)
