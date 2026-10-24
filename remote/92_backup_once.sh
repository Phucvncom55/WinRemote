#!/bin/bash
# vm-all-in-one backup_once v2
# Thoát: 0 OK | 2 không có data.img | 3 đang có backup chạy | 5 hết chỗ | 6 VM không dừng được
#        7 lỗi chuẩn bị | 8 tar lỗi | 9 zstd verify lỗi | 10 split lỗi | 11 upload lỗi | 12 check lỗi
DIR="$HOME/.vm_watchdog"
STATE="$DIR/state.env"
RUNLOG=/tmp/vm_backup_run.log
PROG=/tmp/vm_backup_progress
CONF="$HOME/.config/rclone/rclone.conf"
LOCK=/tmp/vm_backup.lock
CANCEL=/tmp/vm_backup_cancel

mkdir -p "$DIR"; umask 077

log() { local line="[$(date '+%F %T')] [backup] $*"; echo "$line"; echo "$line" >> "$RUNLOG"; }
prog() { printf '%s|%s|%s|%s\n' "$1" "${2:-0}" "${3:-0}" "$(date +%s)" > "$PROG"; }
state_set() { local k="$1" v="$2" tmp="$STATE.tmp.$$"; if [ -f "$STATE" ]; then grep -v "^$k=" "$STATE" > "$tmp" 2>/dev/null; else : > "$tmp"; fi; echo "$k=$v" >> "$tmp"; mv "$tmp" "$STATE"; }
state_get() { local k="$1" d="$2" v=""; if [ -f "$STATE" ]; then v=$(grep -m1 "^$k=" "$STATE" 2>/dev/null | head -1 | cut -d= -f2-); fi; [ -z "$v" ] && v="$d"; echo "$v"; }
ck() { if [ -f "$CANCEL" ]; then log "co yeu cau HUY -> dung an toan (VM se duoc bat lai neu truoc do dang chay)"; echo "RESULT=cancelled"; exit 13; fi; }

if command -v flock >/dev/null 2>&1; then
  exec 8>"$LOCK"
  if ! flock -n 8; then echo "BUSY"; echo "$(date +%s)" > /tmp/vm_backup_busy; exit 3; fi
fi

: > "$RUNLOG"
rm -f "$CANCEL"
rm -f /tmp/vm_backup_busy
log "===== bắt đầu backup ====="

if ! command -v zstd >/dev/null 2>&1; then
  log "zstd chưa có - đang cài bằng apt-get (có thể mất 1-2 phút)..."
  sudo apt-get update -qq >/dev/null 2>&1
  sudo apt-get install -y -qq zstd >/dev/null 2>&1 || true
fi
if ! command -v zstd >/dev/null 2>&1; then
  log "KHÔNG có zstd trên Codespace - không thể nén backup"
  echo "RESULT=no_zstd"; exit 7
fi

VM_WAS_RUNNING=false
if docker inspect windows >/dev/null 2>&1; then
  [ "$(docker inspect -f '{{.State.Running}}' windows 2>/dev/null)" = "true" ] && VM_WAS_RUNNING=true
fi
RESTARTED=false
restart_vm() {
  if [ "$RESTARTED" = "true" ]; then return; fi
  RESTARTED=true
  if [ "$VM_WAS_RUNNING" = "true" ] && [ "${VM_FORCE_NO_RESTART:-0}" != "1" ]; then
    log "khởi động lại VM (docker start windows)"
    docker start windows >/dev/null 2>&1
  fi
}
trap 'restart_vm' EXIT

[ -f /mnt/data/data.img ] || { log "KHÔNG có /mnt/data/data.img - bỏ qua backup"; echo "RESULT=no_image"; exit 2; }

DU_BYTES=$(du -s -B1 /mnt/data/data.img 2>/dev/null | cut -f1)
[ -z "$DU_BYTES" ] && DU_BYTES=0
EST=$(( (DU_BYTES * 7 / 10) + 5368709120 ))
FREE=$(df -B1 --output=avail /mnt/data 2>/dev/null | tail -1 | tr -d ' ')
[ -z "$FREE" ] && FREE=0
if [ "${VM_SKIP_SPACE_CHECK:-0}" != "1" ] && [ "$FREE" -gt 0 ] && [ "$FREE" -lt "$EST" ]; then
  log "hết chỗ trống=$(numfmt --to=iec "$FREE" 2>/dev/null || echo "$FREE") cần~$(numfmt --to=iec "$EST" 2>/dev/null || echo "$EST")"
  echo "RESULT=no_space"; exit 5
fi

FOLDER="${VM_BACKUP_FOLDER:-data_backup_$(date +%Y%m%d_%H%M%S)}"
log "thư mục đích: onedrive:$FOLDER/parts/"

ck
prog stop_vm 0 0
if [ "$VM_WAS_RUNNING" = "true" ]; then
  log "dừng VM an toàn (docker stop -t 300)..."
  docker stop -t 300 windows >/dev/null 2>&1
  STOP_RC=$?
  docker wait windows >/dev/null 2>&1
  sync; sync
  ck
  if [ "$(docker inspect -f '{{.State.Running}}' windows 2>/dev/null)" = "true" ]; then
    log "VM vẫn chạy sau docker stop -> hủy backup"
    echo "RESULT=stop_failed"; exit 6
  fi
  log "VM đã dừng (rc=$STOP_RC) + sync xong"
else
  log "VM đang không chạy - backup theo trạng thái hiện tại"
fi

prog tar 0 0
log "nén /mnt/data -> windows-backup.tar.zst (zstd -T4 -3)..."
sudo rm -rf /mnt/data/backup
mkdir -p /mnt/data/backup/parts || { echo "RESULT=mkdir_failed"; exit 7; }
cd /mnt/data || { echo "RESULT=cd_failed"; exit 7; }
sudo tar --sparse --exclude=./backup --exclude=backup --warning=no-file-changed --use-compress-program="zstd -T4 -3" -cf /mnt/data/backup/windows-backup.tar.zst . 2>/tmp/tar_warn.log
TAR_RC=$?
sync
if [ "$TAR_RC" != "0" ] && [ "$TAR_RC" != "1" ]; then
  log "tar lỗi rc=$TAR_RC"; tail -3 /tmp/tar_warn.log 2>/dev/null
  echo "RESULT=tar_failed"; exit 8
fi
ck
ARC_BYTES=$(stat -c %s /mnt/data/backup/windows-backup.tar.zst 2>/dev/null || echo 0)
[ "$ARC_BYTES" -gt 0 ] || { log "archive rỗng"; echo "RESULT=tar_empty"; exit 8; }
log "archive: $(numfmt --to=iec "$ARC_BYTES" 2>/dev/null || echo "$ARC_BYTES bytes")"

prog verify "$ARC_BYTES" 0
log "kiểm tra archive (zstd -t)..."
if ! zstd -t /mnt/data/backup/windows-backup.tar.zst >/dev/null 2>&1; then
  log "zstd -t THẤT BẠI"
  echo "RESULT=zstd_verify_failed"; exit 9
fi
log "zstd -t OK"
ck

prog split "$ARC_BYTES" 0
rm -rf /mnt/data/backup/parts; mkdir -p /mnt/data/backup/parts
split -b 1G -d -a 3 /mnt/data/backup/windows-backup.tar.zst /mnt/data/backup/parts/part.
ck
PARTS=$(ls /mnt/data/backup/parts 2>/dev/null | wc -l)
[ "$PARTS" -gt 0 ] || { log "split lỗi"; echo "RESULT=split_failed"; exit 10; }
log "chia thành $PARTS parts (1GB/part)"

ck
prog upload "$ARC_BYTES" "$PARTS"
log "upload lên onedrive:$FOLDER/parts/ ($PARTS parts)..."
rclone copy --stats 15s --stats-one-line --stats-log-level NOTICE --transfers 8 --retries 5 --low-level-retries 10 --onedrive-chunk-size 60M --log-file /tmp/vm_upload.log --config "$CONF" /mnt/data/backup/parts "onedrive:$FOLDER/parts/"
UP_RC=$?
if [ "$UP_RC" != "0" ]; then
  log "upload lỗi rc=$UP_RC"; tail -3 /tmp/vm_upload.log 2>/dev/null
  echo "RESULT=upload_failed"; exit 11
fi
log "upload xong"

ck
prog check "$ARC_BYTES" "$PARTS"
log "xác minh remote (rclone check --one-way)..."
if ! rclone check --one-way --config "$CONF" /mnt/data/backup/parts "onedrive:$FOLDER/parts/" >/tmp/vm_check.log 2>&1; then
  log "rclone check THẤT BẠI"; tail -3 /tmp/vm_check.log 2>/dev/null
  echo "RESULT=check_failed"; exit 12
fi
log "rclone check OK"

prog restart "$ARC_BYTES" "$PARTS"
restart_vm

if [ "${VM_KEEP_LOCAL:-0}" != "1" ]; then
  log "dọn bản sao local (/mnt/data/backup) - bản trên OneDrive giữ nguyên"
  rm -rf /mnt/data/backup
fi

state_set last_backup_epoch "$(date +%s)"
state_set last_backup_result ok
state_set last_backup_folder "$FOLDER"
state_set accumulated_sec 0
prog done "$ARC_BYTES" "$PARTS"
log "HOÀN TẤT backup -> onedrive:$FOLDER/parts/"
echo "RESULT=ok"
echo "BACKUP_FOLDER=$FOLDER"
echo "ARCHIVE_BYTES=$ARC_BYTES"
echo "PARTS=$PARTS"
exit 0
