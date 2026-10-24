#!/bin/bash
DIR="$HOME/.vm_watchdog"
PIDF="$DIR/watchdog.pid"
mkdir -p "$DIR"
chmod 700 "$DIR" 2>/dev/null || true

is_alive() {
  local pid="$1"
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null || return 1
  tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | grep -q 'vm_watchdog/watchdog.sh'
}

if [ -f "$PIDF" ]; then
  PID=$(cat "$PIDF" 2>/dev/null)
  if is_alive "$PID"; then echo "ALREADY_RUNNING pid=$PID"; exit 0; fi
fi
STRAY=$(pgrep -f 'vm_watchdog/watchdog\.sh' 2>/dev/null | head -1)
if [ -n "$STRAY" ]; then echo "$STRAY" > "$PIDF"; echo "ALREADY_RUNNING pid=$STRAY"; exit 0; fi

setsid nohup bash "$DIR/watchdog.sh" >> "$DIR/watchdog.log" 2>&1 < /dev/null &
sleep 2
NEW=$(pgrep -f 'vm_watchdog/watchdog\.sh' 2>/dev/null | head -1)
if [ -n "$NEW" ]; then echo "$NEW" > "$PIDF"; echo "STARTED pid=$NEW"; else echo "START_FAIL"; exit 1; fi
