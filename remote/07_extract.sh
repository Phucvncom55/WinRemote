#!/bin/bash
trap 'echo EX_ALL_DONE' EXIT
rm -f /tmp/extract_err.log /mnt/data/.restore_done
# Chốt an toàn: dừng VM (nếu đang chạy) trước khi ghi đè data.img để tránh hỏng đĩa
docker stop -t 300 windows >/dev/null 2>&1 || true
sudo tar --sparse --warning=no-file-changed --use-compress-program=zstd -xf /mnt/data/backup/windows-backup.tar.zst -C /mnt/data/ 2>>/tmp/extract_err.log
RC=$?
if [ "$RC" = "0" ] || [ "$RC" = "1" ]; then
  echo EX_OK
  echo "restored_from=__RF__" | sudo tee /mnt/data/.restore_done >/dev/null
else
  echo "EX_FAIL rc=$RC"
  tail -5 /tmp/extract_err.log 2>/dev/null
fi
exit $RC
