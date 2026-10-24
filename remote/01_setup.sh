#!/bin/bash
trap 'echo ST_ALL_DONE' EXIT
echo "[setup] tìm ổ dữ liệu >= 300G..."
DATA_DEV=$(lsblk -rno NAME,SIZE,TYPE | awk '$3=="part" && $2+0>=300 {print "/dev/"$1; exit}')
if [ -z "$DATA_DEV" ]; then
  ROOT_SRC=$(findmnt -n -o SOURCE / 2>/dev/null | sed 's|^/dev/||')
  DATA_DEV=$(lsblk -rno NAME,SIZE,TYPE,MOUNTPOINT | awk -v root="$ROOT_SRC" \
    '$3=="part" && $2+0>=8 && $4!="/" && $4!="[SWAP]" && $1!=root {print $2" /dev/"$1}' \
    | sort -rn | awk '{print $2; exit}')
  if [ -n "$DATA_DEV" ]; then echo "[setup] cảnh báo: không có ổ >=300G → dùng $DATA_DEV"; fi
fi
if [ -z "$DATA_DEV" ]; then
  ROOT_SRC=$(findmnt -n -o SOURCE / 2>/dev/null | sed 's|^/dev/||')
  if [ -n "$ROOT_SRC" ] && [ -b "/dev/$ROOT_SRC" ]; then
    DATA_DEV="/dev/$ROOT_SRC"
    echo "[setup] cảnh báo: chỉ có ổ gốc → /mnt/data dùng chung $DATA_DEV (dung lượng hạn chế)"
  fi
fi
if [ -z "$DATA_DEV" ]; then echo "ST_FAIL no_disk"; exit 1; fi
echo "[setup] ổ dữ liệu: $DATA_DEV"
sudo mkdir -p /mnt/data
if ! mountpoint -q /mnt/data; then
  sudo mount "$DATA_DEV" /mnt/data || { echo "ST_FAIL mount"; exit 1; }
fi
UUID=$(sudo blkid -s UUID -o value "$DATA_DEV" 2>/dev/null || true)
if [ -n "$UUID" ] && ! grep -qF "/mnt/data" /etc/fstab; then
  echo "UUID=$UUID /mnt/data ext4 defaults,nofail 0 2" | sudo tee -a /etc/fstab >/dev/null
fi
command -v rclone >/dev/null 2>&1 || { echo "[setup] cài rclone..."; curl -fsSL https://rclone.org/install.sh | sudo bash >/dev/null 2>&1; }
command -v rclone >/dev/null 2>&1 || { echo "ST_FAIL rclone_install"; exit 1; }
mkdir -p /home/codespace/.config/rclone
[ "$(stat -c %U /mnt/data 2>/dev/null)" = "codespace" ] || sudo chown codespace:codespace /mnt/data 2>/dev/null || true
df -h /mnt/data | tail -1
echo "ST_OK"
