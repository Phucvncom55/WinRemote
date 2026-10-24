#!/bin/bash
trap 'echo SV_ALL_DONE' EXIT
echo "=== [1/4] Tạo Swap (mục tiêu 100GB) ==="
SW=/tmp/vmswap
sudo swapoff "$SW" 2>/dev/null || true
sudo rm -f "$SW" 2>/dev/null || true
SW_OK=0
for SZ in 100G 64G 32G 16G; do
  if sudo fallocate -l "$SZ" "$SW" 2>/dev/null; then SW_OK=1; echo "swap tạo: $SZ"; break; fi
  sudo rm -f "$SW" 2>/dev/null
done
if [ "$SW_OK" = "1" ]; then
  sudo chmod 600 "$SW"
  if sudo mkswap "$SW" >/dev/null 2>&1 && sudo swapon "$SW" 2>/dev/null; then
    echo "SWAP_OK"
  else
    echo "WARN swapon lỗi - chạy tiếp không swap"
    sudo rm -f "$SW" 2>/dev/null
  fi
else
  echo "WARN không đủ chỗ tạo swap - chạy tiếp không swap"
fi
sudo sysctl vm.swappiness=100 >/dev/null 2>&1 || true

echo "=== [2/4] Xóa container cũ (nếu có) ==="
docker rm -f windows 2>/dev/null || true

echo "=== [3/4] Khởi động Windows VM ==="
if docker run -d \
  --name windows \
  --restart unless-stopped \
  --oom-score-adj=-1000 \
  -e "VERSION=11" \
  -e "CPU_CORES=__CORES__" \
  -e "RAM_SIZE=__RAM__" \
  -e "DISK_SIZE=__DISK__" \
  -e "USERNAME=__USER__" \
  -e "PASSWORD=__PASS__" \
  -v "/mnt/data:/storage" \
  -v "/mnt/data/oem:/oem" \
  -p "8006:8006" \
  -p "3389:3389" \
  --device /dev/kvm \
  --device /dev/net/tun \
  --cap-add NET_ADMIN \
  --stop-timeout 120 \
  dockurr/windows; then
  echo "DOCKER_RUN_OK"
else
  echo "SV_FAIL docker_run (kiểm tra /dev/kvm: ls -l /dev/kvm)"
  exit 9
fi

sleep 20
echo "=== [4/4] Log khởi động ==="
docker logs --tail 10 windows 2>&1 || true
echo SV_OK
