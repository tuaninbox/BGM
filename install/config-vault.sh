#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# CONFIGURATION VARIABLES
# ============================================================
TENANT_NAME="${TENANT_NAME:-ncp}"

VAULT_ADDR="http://127.0.0.1:8200"
VAULT_ARTIFACTORY_URL="<ARTIFACTORY_URL>"
VAULT_REPO_PATH="<REPO_PATH>"
VAULT_GPG_KEY_URL="https://${VAULT_ARTIFACTORY_URL}/artifactory/${VAULT_REPO_PATH}/RPM-GPG-KEY-VAULT"

DATA_DIR="/opt/data/vault"
LOG_DIR="/opt/log/vault"
AUDIT_FILE="$LOG_DIR/audit.log"
UNSEAL_DIR="$DATA_DIR/unseal"
APPROLE_DIR="/opt/data/breakglass/approle"

KV_PATH="secret/${TENANT_NAME}"
ADMIN_POLICY="admin-${TENANT_NAME}"
APPROLE_POLICY="approle-${TENANT_NAME}"
APPROLE_NAME="breakglass-${TENANT_NAME}"

export VAULT_ADDR

# ============================================================
# PREPARE DIRECTORIES
# ============================================================
sudo mkdir -p "$DATA_DIR" "$LOG_DIR" "$UNSEAL_DIR" "$APPROLE_DIR"
sudo chown -R vault:vault "$DATA_DIR" "$LOG_DIR" || true
sudo chmod 700 "$DATA_DIR" "$LOG_DIR"

# ============================================================
# FIREWALLD CHECK + PORT ENABLE
# ============================================================
echo "=== Checking firewalld ==="

if ! systemctl is-active --quiet firewalld; then
    echo "[WARN] firewalld not running, enabling"
    sudo systemctl enable firewalld
    sudo systemctl start firewalld
fi

echo "=== Ensuring Vault ports are open ==="

# Port 8200 (API)
if ! sudo firewall-cmd --list-ports | grep -q "8200/tcp"; then
    echo "[INFO] Opening port 8200/tcp"
    sudo firewall-cmd --add-port=8200/tcp --permanent
else
    echo "[OK] Port 8200/tcp already open"
fi

# Port 8201 (Raft)
if ! sudo firewall-cmd --list-ports | grep -q "8201/tcp"; then
    echo "[INFO] Opening port 8201/tcp"
    sudo firewall-cmd --add-port=8201/tcp --permanent
else
    echo "[OK] Port 8201/tcp already open"
fi

sudo firewall-cmd --reload
echo "[OK] firewalld configured"

# ============================================================
# INSTALL VAULT FROM ARTIFACTORY
# ============================================================
echo "=== Installing Vault from Artifactory ==="

sudo tee /etc/yum.repos.d/vault-artifactory.repo >/dev/null <<EOF
[vault-artifactory]
name=Vault Internal Repository
baseurl=https://${VAULT_ARTIFACTORY_URL}/artifactory/${VAULT_REPO_PATH}/
enabled=1
gpgcheck=1
gpgkey=${VAULT_GPG_KEY_URL}
repo_gpgcheck=0
sslverify=1
EOF

sudo rpm --import "${VAULT_GPG_KEY_URL}" || true

sudo dnf clean all
sudo dnf makecache
sudo dnf install -y vault || { echo "[ERROR] Vault installation failed"; exit 1; }

vault --version || { echo "[ERROR] Vault binary missing"; exit 1; }

# ============================================================
# CREATE VAULT USER
# ============================================================
echo "=== Creating vault user ==="
sudo useradd --system --home "$DATA_DIR" --shell /sbin/nologin vault || true
sudo chown -R vault:vault "$DATA_DIR" "$LOG_DIR"

# ============================================================
# VAULT CONFIG
# ============================================================
echo "=== Writing Vault config ==="

sudo tee /etc/vault/config.hcl >/dev/null <<EOF
storage "raft" {
  path    = "$DATA_DIR"
  node_id = "vault-node-1"
}

listener "tcp" {
  address     = "0.0.0.0:8200"
  tls_disable = "true"
}

api_addr = "$VAULT_ADDR"
cluster_addr = "http://127.0.0.1:8201"

log_level = "info"
EOF

sudo chown vault:vault /etc/vault/config.hcl
sudo chmod 600 /etc/vault/config.hcl

# ============================================================
# SYSTEMD SERVICE
# ============================================================
echo "=== Installing vault.service ==="

sudo tee /etc/systemd/system/vault.service >/dev/null <<EOF
[Unit]
Description=HashiCorp Vault
Requires=network-online.target
After=network-online.target

[Service]
User=vault
Group=vault
ExecStart=/usr/bin/vault server -config=/etc/vault/config.hcl
ExecReload=/bin/kill -HUP \$MAINPID
Restart=on-failure
LimitNOFILE=65536
ProtectSystem=full
ProtectHome=read-only
PrivateTmp=yes
NoNewPrivileges=yes

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable vault
sudo systemctl start vault

sleep 3
vault status

# ============================================================
# INITIALIZE VAULT (4 key shares, threshold 2)
# ============================================================
echo "=== Initializing Vault ==="

if [ ! -f "$UNSEAL_DIR/init.json" ]; then
  vault operator init -key-shares=4 -key-threshold=2 > "$UNSEAL_DIR/init.json"
  sudo chown vault:vault "$UNSEAL_DIR/init.json"
  sudo chmod 600 "$UNSEAL_DIR/init.json"
else
  echo "[INFO] Vault already initialized"
fi

UNSEAL_KEYS=($(jq -r '.unseal_keys_b64[]' "$UNSEAL_DIR/init.json"))
ROOT_TOKEN=$(jq -r '.root_token' "$UNSEAL_DIR/init.json")

export VAULT_TOKEN="$ROOT_TOKEN"

# ============================================================
# UNSEAL VAULT (2 keys)
# ============================================================
echo "=== Unsealing Vault ==="

vault operator unseal "${UNSEAL_KEYS[0]}"
vault operator unseal "${UNSEAL_KEYS[1]}"

vault status

# ============================================================
# ENABLE AUDIT LOGGING
# ============================================================
echo "=== Enabling audit logging ==="

sudo touch "$AUDIT_FILE"
sudo chown vault:vault "$AUDIT_FILE"
sudo chmod 600 "$AUDIT_FILE"

if ! vault audit list | grep -q "file/"; then
  vault audit enable file file_path="$AUDIT_FILE"
fi

# ============================================================
# ENABLE KV ENGINE
# ============================================================
echo "=== Enabling KV engine at $KV_PATH ==="

if ! vault secrets list | grep -q "$KV_PATH"; then
  vault secrets enable -path="$KV_PATH" -version=2 kv
fi

# ============================================================
# ADMIN POLICY
# ============================================================
echo "=== Creating admin policy ==="

vault policy write "$ADMIN_POLICY" - <<EOF
path "sys/*" {
  capabilities = ["create", "read", "update", "delete", "list", "sudo"]
}

path "$KV_PATH/*" {
  capabilities = ["create", "read", "update", "delete", "list"]
}

path "auth/*" {
  capabilities = ["create", "read", "update", "delete", "list", "sudo"]
}
EOF

# ============================================================
# APPROLE POLICY
# ============================================================
echo "=== Creating AppRole policy ==="

vault policy write "$APPROLE_POLICY" - <<EOF
path "$KV_PATH/*" {
  capabilities = ["create", "read", "update", "delete", "list"]
}
EOF

# ============================================================
# ENABLE APPROLE
# ============================================================
echo "=== Enabling AppRole ==="

if ! vault auth list | grep -q "approle/"; then
  vault auth enable approle
fi

# ============================================================
# CREATE APPROLE
# ============================================================
echo "=== Creating AppRole ==="

vault write "auth/approle/role/$APPROLE_NAME" \
  token_policies="$APPROLE_POLICY" \
  token_ttl="1h" \
  token_max_ttl="24h" \
  secret_id_ttl="24h" \
  secret_id_num_uses="1"

ROLE_ID=$(vault read -field=role_id "auth/approle/role/$APPROLE_NAME/role-id")
SECRET_ID=$(vault write -field=secret_id "auth/approle/role/$APPROLE_NAME/secret-id")

echo "$ROLE_ID"   > "$APPROLE_DIR/role_id"
echo "$SECRET_ID" > "$APPROLE_DIR/secret_id"

sudo chown breakglass:breakglass "$APPROLE_DIR/role_id" "$APPROLE_DIR/secret_id"
sudo chmod 600 "$APPROLE_DIR/role_id" "$APPROLE_DIR/secret_id"

# ============================================================
# AUTO-UNSEAL SYSTEMD SERVICE
# ============================================================
echo "=== Installing auto-unseal service ==="

sudo tee /usr/local/bin/vault-unseal.sh >/dev/null <<EOF
#!/usr/bin/env bash
set -euo pipefail

UNSEAL_DIR="/opt/data/vault/unseal"
INIT_FILE="\$UNSEAL_DIR/init.json"

UNSEAL_KEYS=(\$(jq -r '.unseal_keys_b64[]' "\$INIT_FILE"))

vault operator unseal "\${UNSEAL_KEYS[0]}"
vault operator unseal "\${UNSEAL_KEYS[1]}"
EOF

sudo chmod 700 /usr/local/bin/vault-unseal.sh
sudo chown vault:vault /usr/local/bin/vault-unseal.sh

sudo tee /etc/systemd/system/vault-unseal.service >/dev/null <<EOF
[Unit]
Description=Auto Unseal Vault
After=vault.service
Requires=vault.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/vault-unseal.sh

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable vault-unseal

echo "=== Vault fully installed, initialized (4/2), unsealed, and configured ==="
echo "role_id:   $APPROLE_DIR/role_id"
echo "secret_id: $APPROLE_DIR/secret_id"
