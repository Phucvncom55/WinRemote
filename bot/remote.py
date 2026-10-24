# -*- coding: utf-8 -*-
"""Thao tác trên Codespace qua gh codespace ssh (chạy từ Colab/máy local)."""
import base64
import json
import os
import time
from pathlib import Path

CONF_PATH = '/home/codespace/.config/rclone/rclone.conf'
WD_DIR = '/home/codespace/.vm_watchdog'
STATE_ENV = WD_DIR + '/state.env'
STATUS_JSON = WD_DIR + '/status.json'


class RemoteError(Exception):
    def __init__(self, msg, detail=''):
        super().__init__(msg)
        self.detail = (detail or '')[-900:]


def bash_dq(s):
    """Escape chuỗi cho ngữ cảnh bash trong dấu nháy kép (như Escape-BashDq gốc)."""
    return (str(s or '').replace('\\', '\\\\').replace('"', '\\"')
            .replace('$', '\\$').replace('`', '\\`'))


def parse_kv(text):
    out = {}
    for line in str(text or '').splitlines():
        line = line.strip()
        if not line or '=' not in line:
            continue
        k, v = line.split('=', 1)
        if k and (k[0].isalpha() or k[0] == '_'):
            out[k] = v.strip()
    return out


class Remote:
    def __init__(self, gh, cfg, assets_dir=None):
        self.gh = gh
        self.cfg = cfg
        base = assets_dir or os.environ.get('WM_REMOTE_DIR')
        self.assets = Path(base) if base else (Path(__file__).resolve().parent.parent / 'remote')
        self._stdin_ok = True

    @property
    def cs(self):
        return self.cfg.get('codespace') or ''

    # ---------- tiện ích ----------
    def script(self, name, repl=None):
        text = (self.assets / name).read_text(encoding='utf-8')
        for k, v in (repl or {}).items():
            text = text.replace(k, str(v))
        return text

    @staticmethod
    def _out(r):
        return (r.out + ('\n' + r.err if r.err.strip() else '')).strip()

    def run(self, cmd, timeout=60, allow_fail=True, input_text=None):
        r = self.gh.ssh(self.cs, cmd, timeout=timeout, input_text=input_text)
        if not r.ok and not allow_fail:
            raise RemoteError(f'Lệnh trên Codespace lỗi (rc={r.rc})', self._out(r))
        return r

    def run_text(self, cmd, timeout=60, allow_fail=True):
        return self._out(self.run(cmd, timeout=timeout, allow_fail=allow_fail))

    def get_int(self, cmd, default=0):
        try:
            lines = [l.strip() for l in self.run_text(cmd).splitlines() if l.strip()]
            return int(float(lines[-1])) if lines else default
        except Exception:
            return default

    # ---------- trạng thái ----------
    def state(self):
        if not self.cfg.has_github or not self.cs:
            return 'NoConfig'
        return self.gh.codespace_state(self.cs)

    def ensure_available(self, progress=None, timeout=600):
        st = self.state()
        if st == 'NoConfig':
            raise RemoteError('Chưa cấu hình GitHub token / Codespace — mở /settings.')
        if st == 'NotFound':
            raise RemoteError(f"Không tìm thấy codespace '{self.cs}' — kiểm tra /settings.")
        try:
            self.gh.ensure_ssh_registered()
        except Exception:
            pass  # lỗi đăng ký key sẽ lộ ra khi chạy lệnh ssh thật
        if st == 'Available':
            return True
        if progress:
            progress(f'Codespace đang {st} — tự khởi động...')
        r = self.gh.codespace_start(self.cs)
        if not r.ok:
            raise RemoteError(f'Không khởi động được Codespace (đang {st})',
                              (r.out + r.err))
        t0 = time.time()
        while time.time() - t0 < timeout:
            time.sleep(10)
            st = self.state()
            if progress:
                progress(f'Chờ Codespace Available… ({st}, {int(time.time() - t0)}s)')
            if st == 'Available':
                return True
        raise RemoteError(f'Codespace không chuyển sang Available sau {timeout}s (đang: {st}).')

    # ---------- truyền file ----------
    def push_script(self, name, remote_path, repl=None):
        content = self.script(name, repl).encode('utf-8')
        b64 = base64.b64encode(content).decode('ascii')
        cmd = (f"base64 -d > '{remote_path}.tmp' && mv '{remote_path}.tmp' '{remote_path}' "
               f"&& chmod 700 '{remote_path}' && echo PUSH_OK")
        done = False
        r = None
        if self._stdin_ok:
            r = self.run(cmd, timeout=120, allow_fail=True, input_text=b64)
            done = 'PUSH_OK' in self._out(r)
            if not done:
                self._stdin_ok = False
        if not done:
            r = self.run(f"echo {b64} | {cmd}", timeout=120, allow_fail=True)
            done = 'PUSH_OK' in self._out(r)
        if not done:
            raise RemoteError(f'Không gửi được script {name} lên Codespace',
                              self._out(r) if r else '')
        return True

    def start_script(self, name, remote_path, log_path, repl=None, env_prefix=''):
        self.push_script(name, remote_path, repl=repl)
        envpart = f'env {env_prefix} ' if env_prefix else ''
        cmd = (f"rm -f '{log_path}'; setsid nohup {envpart}bash '{remote_path}' >>'{log_path}' "
               f"2>&1 < /dev/null & echo LAUNCHED")
        r = self.run(cmd, timeout=60, allow_fail=True)
        if 'LAUNCHED' not in self._out(r):
            raise RemoteError(f'Không khởi chạy được {name}', self._out(r))
        return True

    def tail(self, path, n=5):
        return self.run_text(f"tail -{int(n)} '{path}' 2>/dev/null || true", allow_fail=True)

    # ---------- probe tổng hợp ----------
    def probe(self):
        cmd = (
            "S=$(docker inspect --format '{{.State.Status}}' windows 2>/dev/null || echo none); "
            'echo "V_CONTAINER=$S"\n'
            "R=$(docker inspect --format '{{.RestartCount}}' windows 2>/dev/null || echo 0); "
            'echo "V_RESTARTS=$R"\n'
            'echo "V_IMG=$(stat -c %s /mnt/data/data.img 2>/dev/null || echo 0)"\n'
            'echo "V_CONF=$(test -f /home/codespace/.config/rclone/rclone.conf && echo 1 || echo 0)"\n'
            'echo "V_MOUNT=$(mountpoint -q /mnt/data && echo 1 || echo 0)"\n'
            'echo "V_LOCK=$( (flock -n /tmp/vm_backup.lock -c true 2>/dev/null && echo 0) || echo 1)"\n'
            "W=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 http://127.0.0.1:8006 || true); "
            '[ -z "$W" ] && W=000; echo "V_WEB=$W"\n'
            "D=$(ss -ltn 2>/dev/null | grep -c ':3389' || true); "
            'echo "V_RDP=$D"\n'
            'echo "V_DF=$(df -h /mnt/data 2>/dev/null | tail -1 | tr -s \' \')"\n'
            'echo "V_DFB=$(df -B1 /mnt/data 2>/dev/null | tail -1 | tr -s \' \' | cut -d\' \' -f3)"\n'
            'echo "V_WD=$(pgrep -fc \'vm_watchdog/watchdo[g]\' 2>/dev/null || echo 0)"\n'
            "echo V_END"
        )
        r = self.run(cmd, timeout=45, allow_fail=True)
        kv = parse_kv(r.out)
        return {
            'container': kv.get('V_CONTAINER', 'none'),
            'restarts': _int(kv.get('V_RESTARTS')),
            'img_bytes': _int(kv.get('V_IMG')),
            'conf': kv.get('V_CONF') == '1',
            'mount': kv.get('V_MOUNT') == '1',
            'lock_busy': kv.get('V_LOCK') == '1',
            'web': kv.get('V_WEB', '000'),
            'rdp': _int(kv.get('V_RDP')),
            'df': kv.get('V_DF', ''),
            'df_used': _int(kv.get('V_DFB')),
            'watchdog_alive': _int(kv.get('V_WD')),
            'raw': r.out,
        }

    def refetch_onedrive_token(self, note_cb=None):
        """Đọc token mới nhất (rclone tự refresh) từ conf trên Codespace về config."""
        line = self.run_text(
            f"grep -m1 \"^token = \" '{CONF_PATH}' 2>/dev/null | sed \"s/^token = //\"",
            allow_fail=True).strip()
        if line.startswith('{') and 'access_token' in line:
            if line != (self.cfg.get('onedrive_token') or ''):
                self.cfg.set('onedrive_token', line)
                if note_cb:
                    note_cb('Đã đồng bộ token OneDrive mới nhất (rclone tự làm mới).')
            return True
        return False


def _int(v, default=0):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default
