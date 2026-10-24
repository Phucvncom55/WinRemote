#!/bin/bash
# Gộp nhiều tài khoản Google Drive thành MỘT remote union (ổ đĩa lớn).
#   - mỗi tài khoản -> [gdrive1], [gdrive2], ...
#   - remote union  -> [gdrive] với upstreams = gdrive1: gdrive2: ...
# Ghi vào conf hiện có, KHÔNG xóa [onedrive] hay remote khác.
# Payload: __ACCOUNTS_B64__ = base64(JSON [{name, token}, ...])
#          __UNION__         = tên remote gộp (mặc định gdrive)
trap 'echo GD_ALL_DONE' EXIT
CONF=/home/codespace/.config/rclone/rclone.conf
mkdir -p /home/codespace/.config/rclone
command -v rclone >/dev/null 2>&1 || { curl -fsSL https://rclone.org/install.sh | sudo bash >/dev/null 2>&1; }
python3 - << 'PYGD'
import base64, json, os, re, subprocess, sys

CONF = '/home/codespace/.config/rclone/rclone.conf'
B64 = '__ACCOUNTS_B64__'
UNION = ('__UNION__' or 'gdrive').strip() or 'gdrive'

def say(*a): print(*a, flush=True)

def parse_conf(text):
    secs, cur, buf = [], None, []
    for line in (text or '').splitlines():
        m = re.match(r'^\s*\[([^\]]+)\]\s*$', line)
        if m:
            if cur is not None:
                secs.append((cur, '\n'.join(buf).strip('\n')))
            cur, buf = m.group(1), []
        elif cur is not None:
            buf.append(line)
    if cur is not None:
        secs.append((cur, '\n'.join(buf).strip('\n')))
    return secs

def write_conf(secs):
    chunks = []
    for name, body in secs:
        chunks.append('[' + name + ']\n' + body.strip('\n') + '\n')
    with open(CONF, 'w', encoding='utf-8') as f:
        f.write('\n'.join(chunks))
    os.chmod(CONF, 0o600)

try:
    accounts = json.loads(base64.b64decode(B64).decode('utf-8')) if B64 else []
except Exception as e:
    say('GD_BAD_PAYLOAD ' + str(e)[:200]); sys.exit(2)
if not accounts:
    say('GD_NO_ACCOUNTS'); sys.exit(3)

old = ''
if os.path.exists(CONF):
    try: old = open(CONF, encoding='utf-8').read()
    except Exception: old = ''
sections = parse_conf(old)
# Xóa các remote google cũ để tránh tài khoản đã xóa còn sót lại.
Keep = [s for s in sections
        if not re.match(r'^gdrive\d+$', s[0]) and s[0] != UNION]

names = []
for i, a in enumerate(accounts, 1):
    name = str((a or {}).get('name') or ('gdrive%d' % i)).strip()
    if not re.match(r'^gdrive\d+$', name):
        name = 'gdrive%d' % i
    tok = (a or {}).get('token')
    if isinstance(tok, dict):
        tok = json.dumps(tok)
    tok = str(tok or '').strip()
    try:
        obj = json.loads(tok)
    except Exception:
        say('GD_SKIP %s (token JSON lỗi)' % name); continue
    if not obj.get('access_token'):
        say('GD_SKIP %s (thiếu access_token)' % name); continue
    body = 'type = drive\nscope = drive\ntoken = ' + json.dumps(obj)
    cid = str(obj.get('_client_id') or '')
    sec = str(obj.get('_client_secret') or '')
    if cid: body += '\nclient_id = ' + cid
    if sec: body += '\nclient_secret = ' + sec
    names.append(name)
    Keep.append((name, body))

if not names:
    say('GD_NO_VALID_ACCOUNTS'); sys.exit(4)

upstreams = ' '.join(n + ':' for n in names)
Keep.append((UNION, 'type = union\nupstreams = ' + upstreams +
             '\naction_policy = epall\ncreate_policy = epmfs\nsearch_policy = ff'))
write_conf(Keep)
say('GD_CONFIG_WRITTEN ' + upstreams)

total_all = used_all = 0
ok_count = 0
for i, name in enumerate(names, 1):
    try:
        p = subprocess.run(['rclone', 'about', '--json', '--config', CONF, name + ':'],
                           capture_output=True, text=True, timeout=90)
        txt = (p.stdout or '').strip()
        if p.returncode != 0:
            say('GD_ACC_FAIL %s %s' % (name, ((p.stderr or '') + txt).strip()[:200]))
            continue
        d = json.loads(txt)
        total = int(d.get('total') or 0)
        used = int(d.get('used') or 0)
        total_all += total
        used_all += used
        ok_count += 1
        say('GD_ACC %s total=%d used=%d' % (name, total, used))
    except Exception as e:
        say('GD_ACC_FAIL %s %s' % (name, str(e)[:200]))

say('GD_TOTAL=%d' % total_all)
say('GD_USED=%d' % used_all)
say('GD_COUNT=%d' % ok_count)
say('GD_UNION=%s' % UNION)
if ok_count <= 0:
    say('GD_FAIL no_account_reachable'); sys.exit(5)
say('GD_OK')
PYGD
RC=$?
if [ "$RC" = "2" ]; then echo "GD_FAIL bad_payload"; exit 2; fi
if [ "$RC" = "3" ]; then echo "GD_FAIL no_accounts"; exit 3; fi
if [ "$RC" = "4" ]; then echo "GD_FAIL no_valid_accounts"; exit 4; fi
if [ "$RC" != "0" ]; then echo "GD_FAIL verify rc=$RC"; exit $RC; fi
# Kiểm tra nhanh remote union đọc được (liệt kê gốc)
rclone lsd --config "$CONF" gdrive: >/tmp/gdrive_lsd.log 2>&1
LSD_RC=$?
echo "GD_LSD_RC=$LSD_RC"
if [ "$LSD_RC" != "0" ]; then tail -4 /tmp/gdrive_lsd.log 2>/dev/null; fi
exit 0
