#!/bin/bash
CONF=/home/codespace/.config/rclone/rclone.conf
REMOTE="${VM_BACKUP_REMOTE:-onedrive}"
ERR=/tmp/vmaio_size_err.log
rm -f "$ERR"
SZRC=""
for i in 1 2 3; do
  OUT=$(rclone size --json --config "$CONF" "$REMOTE:__RF__/parts/" 2>"$ERR")
  RC=$?
  if [ "$RC" = "0" ] && [ -n "$OUT" ]; then echo "$OUT"; SZRC=0; break; fi
  SZRC=$RC
  sleep 4
done
if [ -n "$SZRC" ]; then echo "SIZE_RC=$SZRC"; fi
tail -3 "$ERR" 2>/dev/null | sed 's/^/SIZE_ERR: /'
echo "SIZE_DONE"
