# -*- coding: utf-8 -*-
"""Chèn điểm kiểm tra HỦY (cancel) vào remote/92_backup_once.sh.

Chạy 1 lần: python tools/patch_remote.py
- Thêm biến CANCEL=/tmp/vm_backup_cancel + hàm ck()
- Chèn ck vào các ranh giới bước: trước khi dừng VM, sau khi dừng, sau tar,
  sau verify, sau split, trước upload, sau upload, trước check.
- Hủy an toàn: exit 13 -> trap EXIT restart_vm sẽ bật lại VM nếu trước đó đang chạy.
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, 'remote', '92_backup_once.sh')


def main():
    src = io.open(PATH, encoding='utf-8').read()
    if 'CANCEL=/tmp/vm_backup_cancel' in src:
        print('ALREADY PATCHED - khong lam gi')
        return 0

    def rep(old, new):
        nonlocal src
        n = src.count(old)
        if n != 1:
            print('LOI anchor x%d: %r' % (n, old[:90]))
            sys.exit(1)
        src = src.replace(old, new)

    rep('LOCK=/tmp/vm_backup.lock\n',
        'LOCK=/tmp/vm_backup.lock\nCANCEL=/tmp/vm_backup_cancel\n')

    anchor_state = ('state_get() { local k="$1" d="$2" v=""; if [ -f "$STATE" ]; '
                    'then v=$(grep -m1 "^$k=" "$STATE" 2>/dev/null | head -1 | cut -d= -f2-); '
                    'fi; [ -z "$v" ] && v="$d"; echo "$v"; }\n')
    rep(anchor_state, anchor_state +
        'ck() { if [ -f "$CANCEL" ]; then log "co yeu cau HUY -> dung an toan '
        '(VM se duoc bat lai neu truoc do dang chay)"; echo "RESULT=cancelled"; exit 13; fi; }\n')

    rep(': > "$RUNLOG"\n', ': > "$RUNLOG"\nrm -f "$CANCEL"\n')
    rep('prog stop_vm 0 0\n', 'ck\nprog stop_vm 0 0\n')
    rep('  sync; sync\n', '  sync; sync\n  ck\n')
    rep('log "zstd -t OK"\n', 'log "zstd -t OK"\nck\n')
    rep('split -b 1G -d -a 3 /mnt/data/backup/windows-backup.tar.zst /mnt/data/backup/parts/part.\n',
        'split -b 1G -d -a 3 /mnt/data/backup/windows-backup.tar.zst /mnt/data/backup/parts/part.\nck\n')
    rep('ARC_BYTES=$(stat -c %s /mnt/data/backup/windows-backup.tar.zst 2>/dev/null || echo 0)\n',
        'ck\nARC_BYTES=$(stat -c %s /mnt/data/backup/windows-backup.tar.zst 2>/dev/null || echo 0)\n')
    rep('prog upload "$ARC_BYTES" "$PARTS"\n', 'ck\nprog upload "$ARC_BYTES" "$PARTS"\n')
    rep('prog check "$ARC_BYTES" "$PARTS"\n', 'ck\nprog check "$ARC_BYTES" "$PARTS"\n')

    io.open(PATH, 'w', encoding='utf-8', newline='\n').write(src)
    print('PATCHED OK:', PATH)
    return 0


if __name__ == '__main__':
    sys.exit(main())
