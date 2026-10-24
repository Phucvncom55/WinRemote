#!/bin/bash
trap 'echo RCL_ALL_DONE' EXIT
CONF=/home/codespace/.config/rclone/rclone.conf
mkdir -p /home/codespace/.config/rclone
python3 - << 'PYCONF'
import json, os, re, sys, base64, urllib.request

CONF = '/home/codespace/.config/rclone/rclone.conf'
TOKEN_B64 = '__TOKB64__'

def say(*a): print(*a, flush=True)

old = ''
if os.path.exists(CONF):
    try: old = open(CONF, encoding='utf-8').read()
    except Exception: old = ''
m = re.search(r'(?m)^drive_id\s*=\s*(\S+)', old); old_drive = m.group(1) if m else ''
m2 = re.search(r'(?m)^drive_type\s*=\s*(\S+)', old); old_type = m2.group(1) if m2 else 'personal'

token_obj = None
if TOKEN_B64:
    try:
        raw = base64.b64decode(TOKEN_B64).decode('utf-8').strip()
        try: token_obj = json.loads(raw)
        except Exception: token_obj = {'access_token': raw, 'token_type': 'Bearer'}
    except Exception as e: say('TOKEN_DECODE_FAIL ' + str(e)[:150])

if not token_obj:
    if old.strip():
        say('USE_OLD_CONFIG'); sys.exit(0)
    say('NO_TOKEN'); sys.exit(2)

if 'refresh_token' not in token_obj:
    say('WARN_NO_REFRESH_TOKEN')

drive_id = old_drive; drive_type = old_type; owner = ''; total = 0; used = 0
try:
    req = urllib.request.Request('https://graph.microsoft.com/v1.0/me/drive',
        headers={'Authorization': 'Bearer ' + str(token_obj.get('access_token', ''))})
    with urllib.request.urlopen(req, timeout=20) as resp:
        d = json.loads(resp.read().decode('utf-8'))
    drive_id = d.get('id') or drive_id
    drive_type = d.get('driveType') or drive_type
    own = (d.get('owner') or {}).get('user') or {}
    owner = own.get('emailAddress') or own.get('displayName') or ''
    q = d.get('quota') or {}
    total = int(q.get('total') or 0); used = int(q.get('used') or 0)
    say('GRAPH_OK')
except Exception as e:
    say('GRAPH_FAIL ' + str(e)[:200])

if not drive_id:
    say('NO_DRIVE_ID'); sys.exit(3)

lines = ['[onedrive]', 'type = onedrive',
         'token = ' + json.dumps(token_obj)]
# Token đăng nhập tự động (device flow) thuộc app Microsoft Office — phải ghi
# đúng client_id để rclone dùng cùng app khi refresh (refresh_token gắn với app).
tok_client = str(token_obj.get('_client_id') or '')
if tok_client:
    lines.append('client_id = ' + tok_client)
lines += ['drive_id = ' + drive_id, 'drive_type = ' + drive_type, '']
with open(CONF, 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines))
os.chmod(CONF, 0o600)
say('CONFIG_WRITTEN')
if owner: say('OD_OWNER=' + owner)
say('OD_TOTAL=' + str(total)); say('OD_USED=' + str(used)); say('OD_TYPE=' + drive_type)
PYCONF
RC=$?
if [ "$RC" = "2" ]; then echo "RCL_FAIL no_token"; exit 2; fi
if [ "$RC" = "3" ]; then echo "RCL_FAIL no_drive_id"; exit 3; fi
if [ "$RC" != "0" ]; then echo "RCL_FAIL cfg rc=$RC"; exit $RC; fi

command -v rclone >/dev/null 2>&1 || { curl -fsSL https://rclone.org/install.sh | sudo bash >/dev/null 2>&1; }
ABOUT_RC=0
rclone about --config "$CONF" onedrive: >/tmp/rclone_about.log 2>&1 || ABOUT_RC=$?
echo "ABOUT_RC=$ABOUT_RC"
tail -4 /tmp/rclone_about.log 2>/dev/null
if [ "$ABOUT_RC" != "0" ]; then echo "RCL_FAIL about rc=$ABOUT_RC"; exit 4; fi
echo "RCL_OK"
