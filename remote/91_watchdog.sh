#!/bin/bash
# vm-all-in-one watchdog v2 - cộng dồn thời gian VM chạy; đủ ngưỡng -> backup tự động.
# accumulated_sec được script setup đồng bộ = số giây từ lúc bắt đầu setup trên máy cá nhân,
# rồi watchdog cộng dồn tiếp thời gian VM chạy (lưu state.env -> không mất khi Codespace restart).
# Giới hạn mặc định max_backups=1 (1 backup tự động/phiên); backup_done_count tự mở khóa ở phiên mới.
DIR="$HOME/.vm_watchdog"
STATE="$DIR/state.env"
STATUS="$DIR/status.json"
LOG="$DIR/watchdog.log"
PIDF="$DIR/watchdog.pid"
BKP="$DIR/backup_once.sh"
THRESHOLD_DEFAULT=36000

say() { echo "$*"; }

state_get() {
  local k="$1" d="$2" v=""
  if [ -f "$STATE" ]; then v=$(grep -m1 "^$k=" "$STATE" 2>/dev/null | head -1 | cut -d= -f2-); fi
  [ -z "$v" ] && v="$d"
  echo "$v"
}
state_set() {
  local k="$1" v="$2" tmp="$STATE.tmp.$$"
  if [ -f "$STATE" ]; then grep -v "^$k=" "$STATE" > "$tmp" 2>/dev/null; else : > "$tmp"; fi
  echo "$k=$v" >> "$tmp"
  mv "$tmp" "$STATE"
}
JSON_STR() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'; }

mkdir -p "$DIR"
umask 077
touch "$STATE"
echo $$ > "$PIDF"
say "[$(date '+%F %T')] ===== watchdog khởi động (pid $$) =====" >> "$LOG"
state_set last_tick "$(date +%s)"

# Phiên Codespace mới? (boot_id đổi -> máy đã khởi động lại) -> mở khóa: cho phép lại 1 backup/phiên
BOOT_ID=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null || stat -c %Y /proc/1 2>/dev/null || echo unknown)
STORED_SID=$(state_get session_id '')
if [ -z "$STORED_SID" ]; then
  state_set session_id "$BOOT_ID"
elif [ "$STORED_SID" != "$BOOT_ID" ]; then
  state_set backup_done_count 0
  state_set session_id "$BOOT_ID"
  say "[$(date '+%F %T')] Phiên Codespace mới -> mở khóa backup tự động (tối đa 1 lần/phiên)." >> "$LOG"
fi

while :; do
  NOW=$(date +%s)
  THRESHOLD=$(state_get threshold_sec $THRESHOLD_DEFAULT)
  case "$THRESHOLD" in ''|*[!0-9]*) THRESHOLD=$THRESHOLD_DEFAULT;; esac
  [ "$THRESHOLD" -lt 600 ] && THRESHOLD=$THRESHOLD_DEFAULT

  ACC=$(state_get accumulated_sec 0); case "$ACC" in ''|*[!0-9]*) ACC=0;; esac
  LAST_TICK=$(state_get last_tick 0); case "$LAST_TICK" in ''|*[!0-9]*) LAST_TICK=0;; esac
  CYCLES=$(state_get cycles 0); case "$CYCLES" in ''|*[!0-9]*) CYCLES=0;; esac
  LAST_BK=$(state_get last_backup_epoch 0); case "$LAST_BK" in ''|*[!0-9]*) LAST_BK=0;; esac
  LAST_RES=$(state_get last_backup_result '')
  RETRY_AT=$(state_get retry_at 0); case "$RETRY_AT" in ''|*[!0-9]*) RETRY_AT=0;; esac
  MAX_BK=$(state_get max_backups 1); case "$MAX_BK" in ''|*[!0-9]*) MAX_BK=1;; esac
  DONE_BK=$(state_get backup_done_count 0); case "$DONE_BK" in ''|*[!0-9]*) DONE_BK=0;; esac

  CONTAINER_EXISTS=false; VM_RUNNING=false
  if docker inspect windows >/dev/null 2>&1; then
    CONTAINER_EXISTS=true
    [ "$(docker inspect -f '{{.State.Running}}' windows 2>/dev/null)" = "true" ] && VM_RUNNING=true
  fi
  IMG_PRESENT=false
  [ -f /mnt/data/data.img ] && IMG_PRESENT=true

  LOCK_BUSY=false
  if command -v flock >/dev/null 2>&1 && ! flock -n /tmp/vm_backup.lock -c true 2>/dev/null; then
    LOCK_BUSY=true
  fi

  # Đang có backup chạy (máy PC hoặc tiến trình khác) -> TUYỆT ĐỐI không đụng vào VM
  if [ "$CONTAINER_EXISTS" = "true" ] && [ "$VM_RUNNING" = "false" ] && [ "$LOCK_BUSY" = "false" ]; then
    say "[$(date '+%F %T')] VM không chạy -> thử docker start windows" >> "$LOG"
    docker start windows >/dev/null 2>&1
    sleep 10
    [ "$(docker inspect -f '{{.State.Running}}' windows 2>/dev/null)" = "true" ] && VM_RUNNING=true
  fi

  if [ "$LAST_TICK" -gt 0 ]; then
    D=$((NOW - LAST_TICK))
    [ "$D" -lt 0 ] && D=0
    [ "$D" -gt 1800 ] && D=1800
    if [ "$VM_RUNNING" = "true" ] && [ "$IMG_PRESENT" = "true" ]; then
      ACC=$((ACC + D))
    fi
  fi
  LAST_TICK=$NOW

  STATE_LABEL=counting
  if [ "$CONTAINER_EXISTS" = "false" ]; then STATE_LABEL=no_container
  elif [ "$IMG_PRESENT" = "false" ]; then STATE_LABEL=no_image
  elif [ "$LOCK_BUSY" = "true" ]; then STATE_LABEL=backing_up
  fi

  BK_ALLOWED=true
  if [ "$MAX_BK" != "0" ] && [ "$DONE_BK" -ge "$MAX_BK" ]; then BK_ALLOWED=false; fi
  if [ "$STATE_LABEL" = "counting" ] && [ "$LOCK_BUSY" = "false" ] && [ "$ACC" -ge "$THRESHOLD" ] && [ "$NOW" -ge "$RETRY_AT" ] && [ "$BK_ALLOWED" = "true" ]; then
    say "[$(date '+%F %T')] Đủ ngưỡng ($ACC >= $THRESHOLD giây) - bắt đầu backup tự động..." >> "$LOG"
    SESSION0=$(stat -c %Y /proc/1 2>/dev/null || echo "$NOW")
    SESSION0=$((NOW - SESSION0)); [ "$SESSION0" -lt 0 ] && SESSION0=0
    printf '{"version":"2.0","heartbeat":%s,"state":"backing_up","vm_running":true,"image_present":true,"accumulated_sec":%s,"session_sec":%s,"threshold_sec":%s,"remain_sec":0,"cycles":%s,"max_backups":%s,"backup_done_count":%s,"locked":false,"updated":"%s"}\n' \
      "$NOW" "$ACC" "$SESSION0" "$THRESHOLD" "$CYCLES" "$MAX_BK" "$DONE_BK" "$(date '+%F %T')" > "$STATUS"

    bash "$BKP" >> "$LOG" 2>&1
    RC=$?
    NOW2=$(date +%s)
    if [ "$RC" = "0" ]; then
      state_set accumulated_sec 0
      state_set last_backup_epoch "$NOW2"
      state_set last_backup_result ok
      state_set retry_at 0
      state_set cycles $((CYCLES + 1))
      DONE_BK=$((DONE_BK + 1))
      state_set backup_done_count "$DONE_BK"
      LAST_TICK=$NOW2
      if [ "$MAX_BK" != "0" ] && [ "$DONE_BK" -ge "$MAX_BK" ]; then
        say "[$(date '+%F %T')] Backup OK - đã đạt giới hạn $DONE_BK/$MAX_BK lần cho phiên này -> KHÓA backup tự động đến hết phiên Codespace" >> "$LOG"
      else
        say "[$(date '+%F %T')] Backup OK - chu kỳ mới bắt đầu (hoàn tất $((CYCLES + 1)) chu kỳ)" >> "$LOG"
      fi
    elif [ "$RC" = "3" ]; then
      state_set retry_at $((NOW2 + 900))
      say "[$(date '+%F %T')] Backup khác đang chạy - thử lại sau 15 phút" >> "$LOG"
    elif [ "$RC" = "2" ] || [ "$RC" = "5" ]; then
      state_set last_backup_result "skip rc=$RC"
      state_set retry_at $((NOW2 + 3600))
      say "[$(date '+%F %T')] Không backup được (rc=$RC: thiếu data.img hoặc hết chỗ) - thử lại sau 1 giờ" >> "$LOG"
    else
      state_set last_backup_result "fail rc=$RC"
      state_set retry_at $((NOW2 + 900))
      LAST_TICK=$NOW2
      say "[$(date '+%F %T')] Backup THẤT BẠI rc=$RC - thử lại sau 15 phút" >> "$LOG"
    fi
    LAST_BK=$(state_get last_backup_epoch 0)
    LAST_RES=$(state_get last_backup_result '')
    ACC=$(state_get accumulated_sec 0); case "$ACC" in ''|*[!0-9]*) ACC=0;; esac
    RETRY_AT=$(state_get retry_at 0); case "$RETRY_AT" in ''|*[!0-9]*) RETRY_AT=0;; esac
  fi

  state_set last_tick "$LAST_TICK"
  # Lưu bộ đếm cộng dồn xuống đĩa mỗi vòng (bỏ qua khi đang có backup chạy - tránh đè reset của backup)
  if [ "$STATE_LABEL" = "counting" ]; then state_set accumulated_sec "$ACC"; fi

  SESSION_START=$(stat -c %Y /proc/1 2>/dev/null || echo "$NOW")
  SESSION=$((NOW - SESSION_START)); [ "$SESSION" -lt 0 ] && SESSION=0
  REMAIN=$((THRESHOLD - ACC)); [ "$REMAIN" -lt 0 ] && REMAIN=0
  VR=false; [ "$VM_RUNNING" = "true" ] && VR=true
  IP=false; [ "$IMG_PRESENT" = "true" ] && IP=true
  LK=false
  if [ "$MAX_BK" != "0" ] && [ "$DONE_BK" -ge "$MAX_BK" ]; then LK=true; fi
  printf '{"version":"2.0","heartbeat":%s,"state":"%s","vm_running":%s,"image_present":%s,"accumulated_sec":%s,"session_sec":%s,"threshold_sec":%s,"remain_sec":%s,"cycles":%s,"last_backup_epoch":%s,"last_backup_result":"%s","retry_at":%s,"max_backups":%s,"backup_done_count":%s,"locked":%s,"updated":"%s"}\n' \
    "$NOW" "$STATE_LABEL" "$VR" "$IP" "$ACC" "$SESSION" "$THRESHOLD" "$REMAIN" "$CYCLES" "$LAST_BK" "$(JSON_STR "$LAST_RES")" "$RETRY_AT" "$MAX_BK" "$DONE_BK" "$LK" "$(date '+%F %T')" > "$STATUS"

  sleep 60
done
