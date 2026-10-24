# -*- coding: utf-8 -*-
"""Bọc lệnh gh CLI (codespace list/start/stop/ssh/ports, auth, api user)."""
import base64
import json
import os
import re
import subprocess
import sys
import time
import urllib.request


class GhResult:
    __slots__ = ('rc', 'out', 'err')

    def __init__(self, rc, out, err):
        self.rc = rc
        self.out = out or ''
        self.err = err or ''

    @property
    def ok(self):
        return self.rc == 0

    @property
    def text(self):
        return (self.out + ('\n' + self.err if self.err.strip() else '')).strip()

    def __repr__(self):
        return f'GhResult(rc={self.rc}, out={self.out[:120]!r}, err={self.err[:120]!r})'


class GhCli:
    def __init__(self, cfg, base_dir, binary=None):
        self.cfg = cfg
        self.base_dir = base_dir
        self.binary = binary or os.environ.get('GH_BIN') or 'gh'
        self.home = os.path.join(base_dir, 'home')
        self.default_timeout = 120
        self._ssh_ok_ts = 0.0

    # ---------- tiện ích ----------
    def _env(self, token=None, from_cfg=True):
        env = os.environ.copy()
        tok = (self.cfg.get('github_token') or '') if from_cfg else (token or '')
        env.pop('GITHUB_TOKEN', None)
        if tok:
            env['GH_TOKEN'] = tok
        else:
            env.pop('GH_TOKEN', None)
        env['HOME'] = self.home
        env['GH_CONFIG_DIR'] = os.path.join(self.home, '.config', 'gh')
        env['GH_PROMPT_DISABLED'] = '1'
        env['GH_NO_UPDATE_NOTIFIER'] = '1'
        env['GH_PAGER'] = 'cat'
        env['GIT_TERMINAL_PROMPT'] = '0'
        env['NO_COLOR'] = '1'
        return env

    def _argv(self, args):
        exe = self.binary
        if exe.endswith('.py'):
            return [sys.executable, exe] + list(args)
        return [exe] + list(args)

    def run(self, args, input_text=None, timeout=None, env=None):
        timeout = timeout or self.default_timeout
        os.makedirs(self.home, exist_ok=True)
        try:
            p = subprocess.run(
                self._argv(args), input=input_text,
                capture_output=True, text=True, encoding='utf-8', errors='replace',
                env=env or self._env(), timeout=timeout, cwd=self.base_dir)
            return GhResult(p.returncode, p.stdout, p.stderr)
        except FileNotFoundError:
            return GhResult(127, '', 'Không tìm thấy lệnh gh trên đường dẫn.')
        except subprocess.TimeoutExpired:
            return GhResult(124, '', f'gh timeout sau {timeout}s')
        except Exception as e:  # noqa
            return GhResult(1, '', f'gh lỗi: {e}')

    def spawn(self, args):
        """Chạy gh dạng process nền (cho keepalive ssh dài hạn)."""
        os.makedirs(self.home, exist_ok=True)
        return subprocess.Popen(
            self._argv(args), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace',
            env=self._env(), cwd=self.base_dir)

    # ---------- auth ----------
    def check_token(self, token):
        """Kiểm tra token bằng GET /user với GH_TOKEN tạm (không dùng gh auth).

        Vì mọi lệnh gh khác đều chạy với GH_TOKEN, KHÔNG cần `gh auth login`
        (lệnh đó sẽ bị gh từ chối khi GH_TOKEN đang tồn tại).

        Trả về (ok, login_hoặc_lỗi, scopes). scopes = '' với fine-grained token.
        """
        token = (token or '').strip()
        if not token:
            return False, 'Token trống.', ''
        r = self.run(['api', 'user', '--include'], timeout=90,
                     env=self._env(token, from_cfg=False))
        txt = (r.out or '') + '\n' + (r.err or '')
        scopes = ''
        m = re.search(r'^x-oauth-scopes:\s*(.+)$', txt, re.I | re.M)
        if m:
            scopes = m.group(1).strip()
        login = ''
        mb = re.search(r'\r?\n\r?\n', r.out or '')
        body = (r.out or '')[mb.end():] if mb else ''
        try:
            login = str(json.loads(body).get('login') or '')
        except Exception:
            login = ''
        if r.ok and login:
            return True, login, scopes
        err = ''
        m = re.search(r'^HTTP/\S+\s+(\d{3})(.*)$', txt, re.M)
        if m:
            err = ('HTTP ' + m.group(1) + m.group(2)).strip()
        if not err:
            err = (r.err or r.out or 'không rõ').strip()
        return False, err[:500], scopes

    def login_with_token(self, token):
        """Lưu token vào GH_CONFIG_DIR — phải gỡ GH_TOKEN khỏi env cho lệnh này."""
        return self.run(['auth', 'login', '--with-token', '--insecure-storage'],
                        input_text=(token or '').strip() + '\n',
                        env=self._env('', from_cfg=False))

    def api_user(self):
        r = self.run(['api', 'user', '--jq', '.login'], timeout=90)
        if r.ok:
            return r.out.strip()
        return ''

    # ---------- SSH key cho codespace ----------
    def _ensure_ssh_key(self):
        """Tạo cặp key ed25519 trong HOME (persist /data/home/.ssh). Trả (ok, path|lỗi)."""
        key = os.path.join(self.home, '.ssh', 'id_ed25519')
        d = os.path.dirname(key)
        os.makedirs(d, exist_ok=True)
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass
        if os.path.exists(key) and os.path.exists(key + '.pub'):
            return True, key
        try:
            p = subprocess.run(
                ['ssh-keygen', '-t', 'ed25519', '-N', '', '-f', key, '-C', 'winremote-bot'],
                capture_output=True, text=True, encoding='utf-8', errors='replace',
                env=self._env(), timeout=60)
        except FileNotFoundError:
            return False, 'Không tìm thấy ssh-keygen trong container.'
        except subprocess.TimeoutExpired:
            return False, 'ssh-keygen timeout.'
        if p.returncode != 0 or not os.path.exists(key + '.pub'):
            return False, (p.stderr or p.stdout or 'ssh-keygen lỗi').strip()[:300]
        return True, key

    def ensure_ssh_registered(self):
        """Đảm bảo có SSH key và key đã đăng ký với GitHub (để `gh codespace ssh` chạy ngay).

        Trả về (ok, thông báo). Cache 30 phút khi thành công.
        """
        now = time.time()
        if self._ssh_ok_ts and now - self._ssh_ok_ts < 1800:
            return True, 'đã sẵn sàng'
        ok, key = self._ensure_ssh_key()
        if not ok:
            return False, key
        pub_path = key + '.pub'
        try:
            with open(pub_path, 'r', encoding='utf-8') as f:
                pub = f.read().strip()
        except OSError as e:
            return False, f'không đọc được public key: {e}'
        parts = pub.split()
        blob = ' '.join(parts[:2]) if len(parts) >= 2 else pub
        r = self.run(['api', 'user/keys', '--paginate', '--jq', '.[].key'], timeout=90)
        if r.ok:
            for line in (r.out or '').splitlines():
                lp = line.split()
                if len(lp) >= 2 and ' '.join(lp[:2]) == blob:
                    self._ssh_ok_ts = now
                    return True, 'key đã đăng ký'
        add = self.run(['ssh-key', 'add', pub_path, '--title', 'winremote-bot'], timeout=90)
        if add.ok:
            self._ssh_ok_ts = now
            return True, 'đã đăng ký key mới'
        err = ((add.err or '') + '\n' + (add.out or '')).strip()
        low = err.lower()
        if 'already' in low or 'duplicate' in low:
            self._ssh_ok_ts = now
            return True, 'key đã đăng ký'
        return False, err[:300]

    # ---------- codespaces ----------
    def codespace_list(self):
        r = self.run(['codespace', 'list', '--json', 'name,state,repository,lastUsedAt',
                      '--limit', '30'], timeout=120)
        if not r.ok:
            return []
        txt = r.out
        i, j = txt.find('['), txt.rfind(']')
        if i < 0 or j < 0:
            return []
        try:
            data = json.loads(txt[i:j + 1])
        except ValueError:
            return []
        return data if isinstance(data, list) else []

    def codespace_state(self, name):
        for row in self.codespace_list():
            if row.get('name') == name:
                return str(row.get('state') or 'Unknown')
        return 'NotFound'

    def codespace_start(self, name):
        return self.run(['codespace', 'start', '-c', name], timeout=600)

    def codespace_stop(self, name):
        return self.run(['codespace', 'stop', '-c', name], timeout=180)

    def codespace_create(self, repo, machine):
        """Tạo codespace từ repo với machine cho trước (gh sẽ chờ provision xong)."""
        return self.run(['codespace', 'create', '-R', repo, '-m', machine], timeout=900)

    # ---------- repo ----------
    def repo_exists(self, repo):
        r = self.run(['api', f'repos/{repo}', '--silent'], timeout=90)
        return r.ok

    def repo_create(self, repo, private=True):
        args = ['repo', 'create', repo, '--add-readme']
        if private:
            args.append('--private')
        return self.run(args, timeout=180)

    # ---------- GitHub Actions ----------
    def repo_file_sha(self, repo, path):
        r = self.run(['api', f'repos/{repo}/contents/{path}', '--jq', '.sha'], timeout=90)
        return r.out.strip() if r.ok else ''

    def repo_get_file(self, repo, path):
        """Đọc nội dung file trong repo (base64) — trả '' nếu chưa có/lỗi."""
        r = self.run(['api', f'repos/{repo}/contents/{path}', '--jq', '.content'], timeout=90)
        txt = (r.out or '').strip()
        if not r.ok or not txt:
            return ''
        try:
            b64 = txt.replace('\n', '').replace('\r', '')
            return base64.b64decode(b64).decode('utf-8', 'replace').strip()
        except Exception:
            return ''

    def repo_put_file(self, repo, path, content, message):
        """Tạo/cập nhật file trong repo qua Contents API (tự thêm sha khi file đã có)."""
        b64 = base64.b64encode(content.encode('utf-8')).decode('ascii')
        args = ['api', '-X', 'PUT', f'repos/{repo}/contents/{path}',
                '-f', 'message=' + message, '-f', 'content=' + b64]
        sha = self.repo_file_sha(repo, path)
        if sha:
            args += ['-f', 'sha=' + sha]
        return self.run(args, timeout=120)

    def secret_set(self, repo, name, value):
        return self.run(['secret', 'set', name, '-R', repo, '-b', value], timeout=90)

    def workflow_run(self, repo, wf):
        return self.run(['workflow', 'run', wf, '-R', repo], timeout=120)

    def run_list(self, repo, wf=None, limit=5):
        args = ['run', 'list', '-R', repo, '--limit', str(int(limit)),
                '--json', 'databaseId,status,conclusion,url,createdAt,workflowName']
        if wf:
            args += ['--workflow', wf]
        r = self.run(args, timeout=120)
        if not r.ok:
            return []
        txt = r.out
        i, j = txt.find('['), txt.rfind(']')
        if i < 0 or j < 0:
            return []
        try:
            data = json.loads(txt[i:j + 1])
        except ValueError:
            return []
        return data if isinstance(data, list) else []

    def run_cancel(self, repo, run_id):
        return self.run(['run', 'cancel', str(run_id), '-R', repo], timeout=90)

    def run_log_text(self, repo, run_id, timeout=120):
        r = self.run(['run', 'view', str(run_id), '-R', repo, '--log'], timeout=timeout)
        return r.out if r.ok else ''

    def ssh(self, name, cmd, timeout=60, input_text=None):
        return self.run(['codespace', 'ssh', '-c', name, '--', cmd],
                        timeout=timeout, input_text=input_text)

    def ports_visibility(self, name, spec):
        return self.run(['codespace', 'ports', 'visibility', spec, '-c', name], timeout=90)


def graph_drive_info(token_json_text):
    """Kiểm tra token OneDrive trực tiếp qua Microsoft Graph.

    Trả về (info, error). info = {'owner','total','used','drive_type'} khi OK.
    """
    try:
        tok = json.loads((token_json_text or '').strip())
    except ValueError as e:
        return None, f'Token không phải JSON hợp lệ ({e}).'
    if not isinstance(tok, dict):
        return None, 'Token phải là đối tượng JSON (rclone authorize onedrive).'
    access = str(tok.get('access_token') or '')
    if not access:
        return None, 'Token thiếu access_token.'
    req = urllib.request.Request(
        'https://graph.microsoft.com/v1.0/me/drive',
        headers={'Authorization': 'Bearer ' + access})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            d = json.loads(resp.read().decode('utf-8'))
    except Exception as e:  # noqa
        has_refresh = bool(tok.get('refresh_token'))
        extra = '' if has_refresh else ' (token thiếu refresh_token nên rclone sẽ hết hạn sau ~1 giờ)'
        return None, f'Graph API từ chối token: {e}.{extra}'
    own = ((d.get('owner') or {}).get('user') or {})
    q = d.get('quota') or {}
    return {
        'owner': own.get('emailAddress') or own.get('displayName') or '',
        'total': int(q.get('total') or 0),
        'used': int(q.get('used') or 0),
        'drive_type': str(d.get('driveType') or ''),
    }, None
