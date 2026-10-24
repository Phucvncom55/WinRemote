#!/bin/bash
trap 'echo DL_ALL_DONE' EXIT
sudo mkdir -p /mnt/data/backup/parts
sudo chown -R codespace:codespace /mnt/data/backup 2>/dev/null || true
rm -f /tmp/restore_dl.log
REMOTE="${VM_BACKUP_REMOTE:-onedrive}"
rclone copy --stats 10s --stats-one-line --stats-log-level NOTICE --transfers 8 --retries 5 --low-level-retries 10 --drive-chunk-size 64M --onedrive-chunk-size 60M --log-file /tmp/restore_dl.log --config /home/codespace/.config/rclone/rclone.conf "$REMOTE:__RF__/parts/" /mnt/data/backup/parts/
RC=$?
echo "DOWNLOAD_DONE rc=$RC" >> /tmp/restore_dl.log
if [ "$RC" = "0" ]; then echo DL_OK; else echo "DL_FAIL rc=$RC"; fi
exit $RC
