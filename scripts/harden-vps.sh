#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
  echo "Run with sudo: sudo sh scripts/harden-vps.sh" >&2
  exit 1
fi

DEPLOY_USER="${SUDO_USER:-irsyad}"
APP_DIR="/opt/xiaozhi"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="/root/xiaozhi-hardening-${STAMP}"
mkdir -m 700 "$BACKUP_DIR"

DEPLOY_HOME="$(getent passwd "$DEPLOY_USER" | cut -d: -f6)"
if [ -z "$DEPLOY_HOME" ] || [ ! -s "$DEPLOY_HOME/.ssh/authorized_keys" ]; then
  echo "Refusing to disable password SSH: authorized_keys for $DEPLOY_USER is missing." >&2
  exit 1
fi

cp -a /etc/ssh/sshd_config "$BACKUP_DIR/sshd_config"
if [ -d /etc/ssh/sshd_config.d ]; then
  cp -a /etc/ssh/sshd_config.d "$BACKUP_DIR/sshd_config.d"
fi

install -d -m 755 /etc/ssh/sshd_config.d
cat > /etc/ssh/sshd_config.d/99-xiaozhi-hardening.conf <<'EOF'
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
ChallengeResponseAuthentication no
PubkeyAuthentication yes
AuthenticationMethods publickey
PermitEmptyPasswords no
X11Forwarding no
AllowAgentForwarding no
MaxAuthTries 3
LoginGraceTime 30
EOF

/usr/sbin/sshd -t
systemctl reload ssh

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends ufw unattended-upgrades needrestart

ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp comment SSH
ufw allow 80/tcp comment HTTP
ufw allow 443/tcp comment HTTPS
ufw --force enable

cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
EOF

if [ -f /etc/nginx/sites-available/xiaozhi ]; then
  cp -a /etc/nginx/sites-available/xiaozhi "$BACKUP_DIR/nginx-xiaozhi"
fi

cat > /etc/nginx/conf.d/xiaozhi-security.conf <<'EOF'
log_format xiaozhi_no_query '$remote_addr - $remote_user [$time_local] "$request_method $uri $server_protocol" $status $body_bytes_sent "$http_referer" "$http_user_agent"';
limit_req_zone $binary_remote_addr zone=xiaozhi_api:10m rate=15r/s;
limit_req_zone $binary_remote_addr zone=xiaozhi_auth:10m rate=5r/m;
server_tokens off;
EOF

cat > /etc/nginx/sites-available/xiaozhi <<'EOF'
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;
    return 444;
}

server {
    listen 80;
    listen [::]:80;
    server_name xiaozhiscig.biz.id www.xiaozhiscig.biz.id;

    location /.well-known/acme-challenge/ {
        root /var/www/html;
    }
    location / {
        return 301 https://xiaozhiscig.biz.id$request_uri;
    }
}

server {
    listen 443 ssl default_server;
    listen [::]:443 ssl default_server;
    server_name _;
    ssl_certificate /etc/letsencrypt/live/xiaozhiscig.biz.id/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/xiaozhiscig.biz.id/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;
    return 444;
}

server {
    listen 443 ssl;
    listen [::]:443 ssl;
    http2 on;
    server_name xiaozhiscig.biz.id www.xiaozhiscig.biz.id;
    client_max_body_size 100M;
    access_log /var/log/nginx/xiaozhi_access.log xiaozhi_no_query;

    ssl_certificate /etc/letsencrypt/live/xiaozhiscig.biz.id/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/xiaozhiscig.biz.id/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Frame-Options "DENY" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header Permissions-Policy "camera=(), microphone=(), geolocation=()" always;
    add_header Content-Security-Policy "frame-ancestors 'none'; base-uri 'self'; object-src 'none'" always;

    location = /api/v1/nginx-diag { return 404; }

    location ~ ^/(login|register)$ {
        limit_req zone=xiaozhi_auth burst=5 nodelay;
        proxy_pass http://127.0.0.1:8080;
        include /etc/nginx/proxy_params;
        proxy_set_header X-Forwarded-Proto https;
    }

    location /api/ {
        limit_req zone=xiaozhi_api burst=30 nodelay;
        proxy_pass http://127.0.0.1:8080;
        include /etc/nginx/proxy_params;
        proxy_set_header X-Forwarded-Proto https;
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 3600s;
    }

    location /ws/ {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        include /etc/nginx/proxy_params;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 86400s;
    }

    location /mcp {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        include /etc/nginx/proxy_params;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 86400s;
    }

    location / {
        proxy_pass http://127.0.0.1:8080;
        include /etc/nginx/proxy_params;
        proxy_set_header X-Forwarded-Proto https;
        proxy_buffering off;
    }
}
EOF

nginx -t
systemctl reload nginx

if [ -d "$APP_DIR" ]; then
  find "$APP_DIR" -maxdepth 2 -type f \( -name '.env' -o -name '.env.*' -o -name '*.bak' -o -name '*.backup' \) -exec chmod 600 {} \;
fi

apt-get -y full-upgrade
unattended-upgrade --dry-run --debug >/var/log/xiaozhi-unattended-upgrade-dry-run.log 2>&1 || true

echo "Hardening complete. Backup: $BACKUP_DIR"
echo "Verify a second key-only SSH login before closing this session."
if [ -f /var/run/reboot-required ]; then
  echo "A reboot is required: sudo reboot"
fi
