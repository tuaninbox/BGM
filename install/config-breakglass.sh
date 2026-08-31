#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# CONFIG VARIABLES
# ============================================================
APP_DIR="/opt/data/breakglass"
LOG_DIR="/opt/log/breakglass"
VENV_DIR="$APP_DIR/venv"

BREAKGLASS_CLIENTS=("10.0.0.10" "10.0.0.11")
VAULT_CLIENTS=("10.0.0.20" "10.0.0.21")

INTERNAL_CERT="/etc/pki/tls/certs/internal.crt"
INTERNAL_KEY="/etc/pki/tls/private/internal.key"
INTERNAL_CA="/etc/pki/tls/certs/internal-ca.crt"

GUNICORN_WORKERS=4
GUNICORN_PORT=9000

# ============================================================
# PREPARE DIRECTORIES
# ============================================================
sudo mkdir -p "$APP_DIR" "$LOG_DIR"
sudo chown -R breakglass:breakglass "$APP_DIR" "$LOG_DIR"
sudo chmod 750 "$APP_DIR" "$LOG_DIR"

# ============================================================
# INSTALL PYTHON + GUNICORN + NGINX
# ============================================================
sudo dnf install -y python3 python3-venv nginx policycoreutils-python-utils

# ============================================================
# CREATE VENV + INSTALL APP
# ============================================================
sudo -u breakglass python3 -m venv "$VENV_DIR"
sudo -u breakglass "$VENV_DIR/bin/pip" install gunicorn

# ============================================================
# SYSTEMD SERVICE FOR GUNICORN
# ============================================================
sudo tee /etc/systemd/system/breakglass.service >/dev/null <<EOF
[Unit]
Description=Breakglass Python App
After=network.target

[Service]
User=breakglass
Group=breakglass
WorkingDirectory=/opt/data/breakglass

ExecStart=/opt/data/breakglass/venv/bin/gunicorn \
    -w 4 \
    -b 127.0.0.1:9000 \
    app:app

Restart=always
RestartSec=3

# ============================
# SYSTEMD HARDENING
# ============================

# Drop all capabilities except those explicitly needed (none)
CapabilityBoundingSet=

# Prevent gaining new privileges
NoNewPrivileges=true

# Protect system directories
ProtectSystem=strict
ProtectHome=true

# Prevent access to /dev, except stdin/stdout/stderr
PrivateDevices=true

# Private /tmp and /var/tmp
PrivateTmp=true

# Restrict network namespace (Gunicorn binds only to localhost)
PrivateNetwork=false

# Read-only root filesystem
ReadOnlyPaths=/

# Allow only app directory to be writable
ReadWritePaths=/opt/data/breakglass /opt/log/breakglass

# Protect kernel logs and dmesg
ProtectKernelLogs=true
ProtectKernelModules=true
ProtectKernelTunables=true

# Memory protections
MemoryDenyWriteExecute=true

# Restrict access to /proc
ProtectProc=invisible

# Hide /home, /root, /run/user
ProtectHome=yes

# Restrict system calls (safe for Python)
SystemCallFilter=@system-service
SystemCallArchitectures=native

# Environment sanitization
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable breakglass
sudo systemctl start breakglass

# ============================================================
# NGINX CONFIG
# ============================================================
sudo tee /etc/nginx/conf.d/breakglass.conf >/dev/null <<EOF
server {
    listen 443 ssl;
    server_name _;

    ssl_certificate     $INTERNAL_CERT;
    ssl_certificate_key $INTERNAL_KEY;
    ssl_client_certificate $INTERNAL_CA;
    ssl_verify_client optional;

    access_log $LOG_DIR/nginx-access.log;
    error_log  $LOG_DIR/nginx-error.log;

    # Restrict breakglass app access
    location / {
        $(for ip in "${BREAKGLASS_CLIENTS[@]}"; do echo "allow $ip;"; done)
        deny all;

        proxy_pass http://127.0.0.1:$GUNICORN_PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
    }

    # Proxy Vault API
    location /vault/ {
        $(for ip in "${VAULT_CLIENTS[@]}"; do echo "allow $ip;"; done)
        deny all;

        proxy_pass http://127.0.0.1:8200/;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
    }
}
EOF

sudo nginx -t
sudo systemctl restart nginx

# ============================================================
# SELINUX
# ============================================================
sudo semanage fcontext -a -t httpd_sys_rw_content_t "$APP_DIR(/.*)?"
sudo semanage fcontext -a -t httpd_sys_rw_content_t "$LOG_DIR(/.*)?"
sudo restorecon -Rv "$APP_DIR" "$LOG_DIR"

# ============================================================
# FIREWALL
# ============================================================
sudo firewall-cmd --add-service=https --permanent
sudo firewall-cmd --reload

echo "=== Breakglass app configured ==="
echo "App directory: $APP_DIR"
echo "Gunicorn running on 127.0.0.1:$GUNICORN_PORT"
echo "Nginx serving on port 443"
echo "Vault proxied at /vault/"
