#!/bin/bash
trap 'echo VA_ALL_DONE' EXIT
cd /mnt/data/backup || { echo "VA_FAIL cd"; exit 1; }
command -v zstd >/dev/null 2>&1 || { sudo apt-get update -qq >/dev/null 2>&1; sudo apt-get install -y -qq zstd >/dev/null 2>&1 || true; }
if zstd -t windows-backup.tar.zst >/tmp/zstd_arc.log 2>&1; then
  echo VA_OK
else
  echo "VA_FAIL zstd"
  tail -3 /tmp/zstd_arc.log
fi
