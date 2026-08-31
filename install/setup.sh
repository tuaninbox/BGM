#!/usr/bin/env bash
set -euo pipefail

# ============================
# 0. CONFIGURATION
# ============================
DISK="/dev/sdb"        # Change if needed
VG="vg_main"
LV_DATA="lv_app_data"
LV_LOG="lv_app_log"
DATA_MNT="/opt/data"
LOG_MNT="/opt/log"

# ============================
# 1. OS UPDATE
# ============================
echo "=== Updating OS packages ==="
sudo dnf update -y || { echo "[ERROR] OS update failed"; exit 1; }
echo "[OK] OS updated"

# ============================
# 2. Install required packages
# ============================
echo "=== Installing required packages ==="
sudo dnf install -y xfsprogs policycoreutils-python-utils lvm2 git curl wget python3 || {
    echo "[ERROR] Package installation failed"; exit 1;
}

for bin in pvcreate vgcreate lvcreate mkfs.xfs semanage restorecon; do
    command -v $bin >/dev/null || { echo "[ERROR] Missing binary: $bin"; exit 1; }
done
echo "[OK] Required binaries verified"

# ============================
# 3. Create LVM PV + VG
# ============================
echo "=== Creating PV on $DISK ==="
sudo pvcreate $DISK || { echo "[ERROR] pvcreate failed"; exit 1; }

sudo pvs | grep -q "$DISK" || { echo "[ERROR] PV not found in pvs output"; exit 1; }
echo "[OK] PV created"

echo "=== Creating VG $VG ==="
sudo vgcreate $VG $DISK || { echo "[ERROR] vgcreate failed"; exit 1; }

sudo vgs | grep -q "$VG" || { echo "[ERROR] VG not found"; exit 1; }
echo "[OK] VG created"

# ============================
# 4. Create LVs
# ============================
echo "=== Creating LVs ==="
sudo lvcreate -L 100G -n $LV_DATA $VG || { echo "[ERROR] LV data creation failed"; exit 1; }
sudo lvcreate -L 50G  -n $LV_LOG  $VG || { echo "[ERROR] LV log creation failed"; exit 1; }

sudo lvs | grep -q "$LV_DATA" || { echo "[ERROR] LV data missing"; exit 1; }
sudo lvs | grep -q "$LV_LOG"  || { echo "[ERROR] LV log missing"; exit 1; }
echo "[OK] LVs created"

# ============================
# 5. Format LVs
# ============================
echo "=== Formatting LVs as XFS ==="
sudo mkfs.xfs /dev/$VG/$LV_DATA || { echo "[ERROR] mkfs failed for data LV"; exit 1; }
sudo mkfs.xfs /dev/$VG/$LV_LOG  || { echo "[ERROR] mkfs failed for log LV"; exit 1; }

sudo blkid /dev/$VG/$LV_DATA | grep -q "xfs" || { echo "[ERROR] Data LV not XFS"; exit 1; }
sudo blkid /dev/$VG/$LV_LOG  | grep -q "xfs" || { echo "[ERROR] Log LV not XFS"; exit 1; }
echo "[OK] Filesystems created"

# ============================
# 6. Mount LVs
# ============================
echo "=== Creating mount points ==="
sudo mkdir -p $DATA_MNT $LOG_MNT

echo "=== Updating /etc/fstab ==="
echo "/dev/$VG/$LV_DATA $DATA_MNT xfs defaults 0 0" | sudo tee -a /etc/fstab
echo "/dev/$VG/$LV_LOG  $LOG_MNT  xfs defaults 0 0" | sudo tee -a /etc/fstab

echo "=== Mounting volumes ==="
sudo mount -a

mount | grep -q "$DATA_MNT" || { echo "[ERROR] Data mount failed"; exit 1; }
mount | grep -q "$LOG_MNT"  || { echo "[ERROR] Log mount failed"; exit 1; }
echo "[OK] Volumes mounted"

# ============================
# 7. Create system users
# ============================
echo "=== Creating system users ==="
sudo useradd --system --home $DATA_MNT/vault --shell /sbin/nologin vault || true
sudo useradd --system --home $DATA_MNT/breakglass --shell /sbin/nologin breakglass || true
echo "[OK] Users created"

# ============================
# 8. Create app directories
# ============================
echo "=== Creating app directories ==="
for d in vault breakglass nginx; do
    sudo mkdir -p $DATA_MNT/$d
    sudo mkdir -p $LOG_MNT/$d
done

# ============================
# 9. Permissions
# ============================
echo "=== Setting permissions ==="
sudo chown -R vault:vault $DATA_MNT/vault $LOG_MNT/vault
sudo chmod 700 $DATA_MNT/vault $LOG_MNT/vault

sudo chown -R breakglass:breakglass $DATA_MNT/breakglass $LOG_MNT/breakglass
sudo chmod 750 $DATA_MNT/breakglass $LOG_MNT/breakglass

sudo chown -R nginx:nginx $DATA_MNT/nginx $LOG_MNT/nginx
sudo chmod 755 $DATA_MNT/nginx $LOG_MNT/nginx

echo "[OK] Permissions applied"

# ============================
# 10. SELinux
# ============================
echo "=== Applying SELinux labels ==="
sudo semanage fcontext -a -t usr_t "$DATA_MNT(/.*)?"
sudo semanage fcontext -a -t var_log_t "$LOG_MNT(/.*)?"

sudo restorecon -Rv $DATA_MNT
sudo restorecon -Rv $LOG_MNT

ls -Zd $DATA_MNT | grep -q "usr_t" || { echo "[ERROR] SELinux label wrong for data"; exit 1; }
ls -Zd $LOG_MNT  | grep -q "var_log_t" || { echo "[ERROR] SELinux label wrong for log"; exit 1; }

echo "[OK] SELinux labels applied"

# ============================
# DONE
# ============================
echo "=== Server provisioning complete ==="
