#!/bin/bash
trap 'echo AS_ALL_DONE' EXIT
cd /mnt/data/backup || { echo "AS_FAIL cd"; exit 1; }
COUNT=$(ls parts/part.* 2>/dev/null | wc -l)
if [ "$COUNT" -eq 0 ]; then echo "AS_FAIL no_parts"; exit 1; fi
cat parts/part.* > windows-backup.tar.zst
echo "ARCHIVE_BYTES=$(stat -c %s windows-backup.tar.zst)"
echo AS_OK
