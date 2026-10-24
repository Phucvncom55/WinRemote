# -*- coding: utf-8 -*-
"""Test local cho Engine: FakeTelegram + FakeRemote/FakeGh, không cần mạng.

Chạy:  python tests/test_local.py
"""
import json
import logging
import os
import shutil
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ['WM_POLL_SEC'] = '0.05'
os.environ['TELEGRAM_MODE'] = 'fake'

from bot import cfg as cfgmod  # noqa: E402
from bot import gh as ghmod  # noqa: E402
from bot import keepalive as ka_mod  # noqa: E402
from bot.engine import Engine  # noqa: E402
from bot.gh import GhResult  # noqa: E402
from bot.tg import FakeTelegram  # noqa: E402

logging.basicConfig(level=logging.WARNING)
LOG = logging.getLogger('test')
OWNER = 555000111

DEFAULT_PROBE = {
    'container': 'running', 'restarts': 0, 'img_bytes': 322122547200,
    'conf': True, 'mount': True, 'lock_busy': False, 'web': '302', 'rdp': 1,
    'df': '/dev/loop0 450G 300G 150G 67% /mnt/data', 'df_used': 322122547200,
    'watchdog_alive': 0, 'raw': '',
}

RCL_LSD = ('          -1 2026-10-17 12:00:00        -1 data_backup_20261017_120000\n')

PROG_TAR = '\n'.join([
    'MARK_P', 'stop_vm|0|0|1699999999',
    'MARK_B', '',
    'MARK_L', '[backup] dang dung VM an toan...',
    'MARK_U', '',
    'MARK_A', '0',
    'MARK_C', '0',
    'MARK_R', '',
    'MARK_AL', '1',
    'MARK_END', ''])

RESULT_OK = '\n'.join([
    'MARK_P', 'done|0|0|1699999999',
    'MARK_B', '',
    'MARK_L', '[backup] RESULT=ok',
    'MARK_U', '',
    'MARK_A', '3690800000',
    'MARK_C', '4',
    'MARK_R', 'RESULT=ok',
    'MARK_AL', '0',
    'MARK_END', ''])

RESULT_CANCELLED = RESULT_OK.replace('RESULT=ok', 'RESULT=cancelled')

DL_DONE = '\n'.join([
    'MARK_COUNT', '4',
    'MARK_SIZE', '3690800000',
    'MARK_LOG', 'Transferred:   3.6 GiB / 3.6 GiB, 100%, 45.0 MiB/s, ETA 0s',
    'MARK_PROC', '0',
    'M_D', '1',
    'M_OK', '1', ''])

DL_DONE_NOTICE = '\n'.join([
    'MARK_COUNT', '30',
    'MARK_SIZE', '26900000000',
    'MARK_LOG',
    '2026/10/17 06:55:46 NOTICE:    23.766 GiB / 43.823 GiB, 54%, 93.5 MiB/s, ETA 3m54s (xfr#17/44)',
    'MARK_PROC', '0',
    'M_D', '1',
    'M_OK', '1', ''])

DL_RUNNING_NOTICE = '\n'.join([
    'MARK_COUNT', '17',
    'MARK_SIZE', '12600000000',
    'MARK_LOG',
    '2026/10/17 06:55:46 NOTICE:    23.766 GiB / 43.823 GiB, 54%, 93.5 MiB/s, ETA 3m54s (xfr#17/44)',
    'MARK_PROC', '1',
    'M_D', '0',
    'M_OK', '0', ''])

ASM_DONE = '\n'.join([
    'MARK_SZ', '3690800000',
    'M_D', '1',
    'M_OK', '1', ''])

EXT_DONE = '\n'.join([
    'MARK_SZ', '322122547200',
    'M_D', '1',
    'M_OK', '1', ''])

FAKE_GH = '''
import os, sys
args = sys.argv[1:]
if args[:2] == ["api", "user"] and "--include" in args:
    print("HTTP/2.0 200 OK")
    print("x-oauth-scopes: codespace, repo, admin:public_key, user")
    print("")
    print('{"login": "phuc-test"}')
    sys.exit(0)
if args[:3] == ["auth", "login", "--with-token"]:
    tok = sys.stdin.read().strip()
    if os.environ.get("GH_TOKEN"):
        sys.stderr.write("The value of the GH_TOKEN environment variable is being used "
                         "for authentication.\\n")
        sys.exit(1)
    if not tok:
        sys.stderr.write("no token\\n")
        sys.exit(1)
    print("stored")
    sys.exit(0)
if args[:2] == ["api", "user/keys"]:
    sys.exit(0)
if args[:2] == ["ssh-key", "add"]:
    print("added")
    sys.exit(0)
sys.exit(2)
'''


class FakeGh:
    def __init__(self):
        self.token_ok = True
        self.login = 'phuc'
        self.cs_list = [{'name': 'winvm-cs', 'state': 'Available',
                         'repository': 'phuc/winvm'}]
        self.ports_ok = True
        self.action_runs = []

    def check_token(self, token):
        if self.token_ok:
            return True, self.login, 'codespace, repo, admin:public_key, user'
        return False, 'HTTP 401: Bad credentials', ''

    def login_with_token(self, token):
        return GhResult(0 if self.token_ok else 1, '', '' if self.token_ok else 'bad credentials')

    def api_user(self):
        return self.login if self.token_ok else ''

    def codespace_list(self):
        return list(self.cs_list)

    def codespace_state(self, name):
        for r in self.cs_list:
            if r.get('name') == name:
                return str(r.get('state') or 'Unknown')
        return 'NotFound'

    def codespace_start(self, name):
        for r in self.cs_list:
            if r.get('name') == name:
                r['state'] = 'Available'
        return GhResult(0, '', '')

    def codespace_stop(self, name):
        for r in self.cs_list:
            if r.get('name') == name:
                r['state'] = 'Shutdown'
        return GhResult(0, '', '')

    def ensure_ssh_registered(self):
        self.ssh_calls = getattr(self, 'ssh_calls', 0) + 1
        return True, 'đã đăng ký key mới'

    def repo_exists(self, repo):
        return True

    def repo_create(self, repo, private=True):
        self.created_repos = getattr(self, 'created_repos', [])
        self.created_repos.append(repo)
        return GhResult(0, '', '')

    def repo_list(self, limit=30):
        return [
            {'nameWithOwner': 'phuc/winvm-codespace', 'visibility': 'private'},
            {'nameWithOwner': 'phuc/winvm-tools', 'visibility': 'public'},
        ]

    def codespace_create(self, repo, machine):
        self.created_cs = getattr(self, 'created_cs', [])
        self.created_cs.append((repo, machine))
        self.cs_list.append({'name': 'winvm-cs-new', 'state': 'Available', 'repository': repo})
        return GhResult(0, 'winvm-cs-new\n', '')

    # ---------- Actions ----------
    def repo_file_sha(self, repo, path):
        return ''

    def repo_get_file(self, repo, path):
        return getattr(self, 'ip_file', '')

    def repo_put_file(self, repo, path, content, message):
        self.puts = getattr(self, 'puts', [])
        self.puts.append((repo, path, len(content)))
        return GhResult(0, '', '')

    def secret_set(self, repo, name, value):
        self.secrets = getattr(self, 'secrets', [])
        self.secrets.append((repo, name, value))
        return GhResult(0, '', '')

    def workflow_run(self, repo, wf):
        self.dispatched = getattr(self, 'dispatched', [])
        self.dispatched.append((repo, wf))
        self.action_runs.insert(0, {
            'databaseId': 123, 'status': 'queued', 'conclusion': '',
            'url': 'https://github.com/phuc/winvm-codespace/actions/runs/123',
            'createdAt': '2026-10-17T07:00:00Z',
            'workflowName': 'Environment Integration Test'})
        return GhResult(0, '', '')

    def run_list(self, repo, wf=None, limit=5):
        return list(self.action_runs)[:limit]

    def run_cancel(self, repo, run_id):
        self.cancelled_runs = getattr(self, 'cancelled_runs', [])
        self.cancelled_runs.append(run_id)
        return GhResult(0, '', '')

    def run_log_text(self, repo, run_id, timeout=120):
        return 'TS_IP=100.101.102.103\n'

    def ports_visibility(self, name, spec):
        return GhResult(0 if self.ports_ok else 1, '', '' if self.ports_ok else 'no port')

    def spawn(self, args):
        raise RuntimeError('spawn không dùng trong test')

    def ssh(self, name, cmd, timeout=60, input_text=None):
        self.ssh_targets = getattr(self, 'ssh_targets', [])
        self.ssh_targets.append(name)
        return GhResult(0, f'KA_TARGET={name}\nKA_DATE=2026-10-24T10:00:00Z\n'
                           f'KA_WORKSPACE=/workspaces/winvm', '')


class FakeRemote:
    def __init__(self, cfg, gh):
        self.cfg = cfg
        self.gh = gh
        self.lock = threading.RLock()
        self.rules = []
        self.calls = []
        self.pushed = {}
        self.launched = []

    # ---------- helper test ----------
    def add(self, match, out='', rc=0, err='', handler=None):
        self.rules.append({'match': match, 'out': out, 'rc': rc, 'err': err,
                           'queue': None, 'default': None, 'handler': handler})
        return self.rules[-1]

    def add_queue(self, match, outs, default=None):
        self.rules.append({'match': match, 'out': '', 'rc': 0, 'err': '',
                           'queue': list(outs), 'default': default, 'handler': None})
        return self.rules[-1]

    def _respond(self, cmd):
        with self.lock:
            self.calls.append(cmd)
            for r in self.rules:
                if r['match'] and r['match'] in cmd:
                    if r['handler']:
                        return r['handler'](cmd, self)
                    if r['queue'] is not None:
                        if r['queue']:
                            return GhResult(r['rc'], r['queue'].pop(0), r['err'])
                        return GhResult(r['rc'], r['default'] or '', r['err'])
                    return GhResult(r['rc'], r['out'], r['err'])
            return GhResult(0, '', '')

    def matched_calls(self, sub):
        with self.lock:
            return [c for c in self.calls if sub in c]

    # ---------- API engine dùng ----------
    @property
    def cs(self):
        return self.cfg.get('codespace') or ''

    def state(self):
        if not self.cfg.has_github or not self.cfg.get('codespace'):
            return 'NoConfig'
        return self.gh.codespace_state(self.cfg.get('codespace'))

    def ensure_available(self, progress=None, timeout=600):
        return True

    def run(self, cmd, timeout=60, allow_fail=True, input_text=None):
        return self._respond(cmd)

    def run_text(self, cmd, timeout=60, allow_fail=True):
        r = self._respond(cmd)
        return (r.out + ('\n' + r.err if r.err.strip() else '')).strip()

    def get_int(self, cmd, default=0):
        r = self._respond(cmd)
        try:
            lines = [l.strip() for l in (r.out or '').splitlines() if l.strip()]
            return int(float(lines[-1])) if lines else default
        except Exception:
            return default

    def probe(self):
        r = self._respond('__PROBE__')
        try:
            return json.loads(r.out)
        except Exception:
            return dict(DEFAULT_PROBE)

    def refetch_onedrive_token(self, note_cb=None):
        self._respond('__REFETCH__')
        return True

    def tail(self, path, n=5):
        return self._respond('__TAIL__').out or ''

    def script(self, name, repl=None):
        return ''

    def push_script(self, name, remote_path, repl=None):
        with self.lock:
            self.pushed[name] = remote_path
        return True

    def start_script(self, name, remote_path, log_path, repl=None, env_prefix=''):
        with self.lock:
            self.launched.append((name, remote_path, log_path, env_prefix))
        return True


class Env:
    def __init__(self):
        self.tmp = tempfile.mkdtemp(prefix='wmtest_')
        self.cfg = cfgmod.Config(os.path.join(self.tmp, 'bot-config.json'))
        self.inbox = os.path.join(self.tmp, 'inbox.jsonl')
        self.outbox = os.path.join(self.tmp, 'outbox.jsonl')
        self.tg = FakeTelegram(self.inbox, self.outbox)
        self.gh = FakeGh()
        self.remote = FakeRemote(self.cfg, self.gh)
        self.eng = Engine(self.cfg, self.tg, self.gh, self.remote, None, LOG)

    def cleanup(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def configure(self):
        self.cfg.set('github_token', 'ghp_test')
        self.cfg.set('codespace', 'winvm-cs')
        self.cfg.set('onedrive_token', '{"access_token":"a","refresh_token":"r"}')
        self.cfg.set('owner_chat_id', OWNER)

    def send(self, text, chat_id=OWNER, mid=1):
        self.eng.handle_update({'update_id': 0, 'message': {
            'chat': {'id': chat_id}, 'message_id': mid, 'text': text}})

    def cb(self, data, chat_id=OWNER, mid=100):
        self.eng.handle_update({'update_id': 0, 'callback_query': {
            'id': 'cb1', 'data': data,
            'message': {'chat': {'id': chat_id}, 'message_id': mid}}})

    def events(self):
        out = []
        if os.path.exists(self.outbox):
            with open(self.outbox, encoding='utf-8') as f:
                for ln in f:
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        out.append(json.loads(ln))
                    except ValueError:
                        pass
        return out

    def wait(self, substr, kind=None, key='text', timeout=15, since=0):
        t0 = time.time()
        while time.time() - t0 < timeout:
            for e in self.events()[since:]:
                if kind and e.get('kind') != kind:
                    continue
                if substr in str(e.get(key) or ''):
                    return e
            time.sleep(0.05)
        raise AssertionError(f'Không thấy event {kind or "any"}/{key} chứa {substr!r} trong {timeout}s')

    def assert_no(self, substr, since=0):
        for e in self.events()[since:]:
            if substr in str(e.get('text') or ''):
                raise AssertionError(f'Có event chứa {substr!r}: {e}')


# ============================ tests ============================

def test_claim_status():
    env = Env()
    try:
        env.send('/start')
        assert env.cfg.get('owner_chat_id') == OWNER
        env.wait('chủ sở hữu')
        env.wait('TRẠNG THÁI HỆ THỐNG')
        env.wait('chưa chọn')
    finally:
        env.cleanup()


def test_nonowner_ignored():
    env = Env()
    try:
        env.send('/start')
        env.wait('chủ sở hữu')
        n = len(env.events())
        env.send('/status', chat_id=12345)
        env.cb('home', chat_id=12345, mid=7)
        time.sleep(0.3)
        env.assert_no('TRẠNG THÁI', since=n)
    finally:
        env.cleanup()


def test_settings_flows():
    env = Env()
    try:
        env.send('/start')
        env.wait('chủ sở hữu')
        env.send('/settings')
        env.wait('CÀI ĐẶT')

        # GitHub token — sai
        env.gh.token_ok = False
        env.cb('set:gh', mid=101)
        env.wait('Gửi GitHub token')
        env.send('ghp_bad', mid=2)
        env.wait('không hợp lệ')

        # GitHub token — đúng
        env.gh.token_ok = True
        env.cb('set:gh', mid=102)
        env.wait('Gửi GitHub token')
        env.send('ghp_good_token', mid=3)
        env.wait('GitHub OK')
        assert env.cfg.get('github_login') == 'phuc'
        assert env.cfg.get('github_token') == 'ghp_good_token'

        # OneDrive token — JSON hỏng
        env.cb('set:od', mid=103)
        env.wait('OneDrive token JSON')
        env.send('not a json', mid=4)
        env.wait('Không phải JSON')

        # OneDrive token — hợp lệ (chặn Graph API bằng monkeypatch)
        orig = ghmod.graph_drive_info
        ghmod.graph_drive_info = lambda t: (
            {'owner': 'phuc@example.com', 'total': 1024 ** 4, 'used': 1024 ** 3,
             'drive_type': 'personal'}, None)
        try:
            env.cb('set:od', mid=104)
            env.wait('OneDrive token JSON')
            env.send('{"access_token":"a","refresh_token":"r"}', mid=5)
            env.wait('OneDrive OK')
        finally:
            ghmod.graph_drive_info = orig
        assert env.cfg.get('od_owner') == 'phuc@example.com'

        # chọn Codespace
        env.cb('set:cs', mid=105)
        env.wait('Chọn Codespace')
        env.cb('set:csp:0', mid=105)
        env.wait('Đã chọn Codespace')
        assert env.cfg.get('codespace') == 'winvm-cs'
    finally:
        env.cleanup()


def test_keepalive_log_view():
    env = Env()
    try:
        env.configure()
        env.gh.ssh_targets = []
        env.send('/kalog')
        env.wait('KEEPALIVE — NHẬT KÝ')
        env.wait('Codespace đích')
        env.cb('ka:test', mid=960)
        e = env.wait('KA_TARGET=winvm-cs', timeout=20)
        assert env.gh.ssh_targets == ['winvm-cs']
        env.cb('ka:clear', mid=961)
        env.wait('Keepalive')
    finally:
        env.cleanup()


def test_repo_flow():
    env = Env()
    try:
        env.cfg.set('github_token', 'ghp_test')
        env.cfg.set('github_login', 'phuc')
        env.cfg.set('owner_chat_id', OWNER)
        env.send('/repo')
        env.wait('REPO CHO CODESPACE')
        # chọn repo có sẵn
        env.cb('set:repol', mid=950)
        env.wait('CHỌN REPO')
        env.cb('set:repop:1', mid=951)
        env.wait('winvm-tools')
        assert env.cfg.get('cs_repo') == 'phuc/winvm-tools'
        # nhập owner/repo
        env.cb('set:repoi', mid=952)
        env.wait('owner/repo')
        env.send('phuc/khac', mid=40)
        env.wait('Đã đặt repo')
        assert env.cfg.get('cs_repo') == 'phuc/khac'
        # tạo repo mới từ tên ngắn
        env.gh.repo_exists = lambda repo: False
        env.cb('set:repon', mid=953)
        env.wait('Tạo repo mới')
        env.send('repo-moi', mid=41)
        env.wait('repo-moi')
        assert env.cfg.get('cs_repo') == 'phuc/repo-moi'
        assert env.gh.created_repos == ['phuc/repo-moi']
    finally:
        env.cleanup()


def test_backup_flow():
    env = Env()
    try:
        env.configure()
        r = env.remote
        r.add('__PROBE__', out=json.dumps(DEFAULT_PROBE))
        r.add('test -f /home/codespace/.vm_watchdog/backup_once.sh', out='YES')
        r.add('nohup env', out='LAUNCHED')
        r.add_queue('MARK_END', [PROG_TAR, RESULT_OK], default=RESULT_OK)
        r.add('BACKUP_FOLDER', out=('BACKUP_FOLDER=data_backup_20261017_120000\n'
                                    'ARCHIVE_BYTES=3690800000\nPARTS=4\n'))
        env.send('/backup')
        env.wait('XÁC NHẬN BACKUP')
        env.cb('bk:go', mid=200)
        e = env.wait('HOÀN TẤT BACKUP', timeout=25)
        assert 'data_backup_20261017_120000' in e['text']
        assert '4 part' in e['text']
    finally:
        env.cleanup()


def test_backup_cancel():
    env = Env()
    try:
        env.configure()
        r = env.remote
        r.add('__PROBE__', out=json.dumps(DEFAULT_PROBE))
        r.add('test -f /home/codespace/.vm_watchdog/backup_once.sh', out='YES')
        r.add('nohup env', out='LAUNCHED')
        state = {'cancel': False}

        def on_probe(cmd, remote):
            return GhResult(0, RESULT_CANCELLED if state['cancel'] else PROG_TAR, '')

        def on_cancel(cmd, remote):
            state['cancel'] = True
            return GhResult(0, 'OK', '')

        r.add('MARK_END', handler=on_probe)
        r.add('touch /tmp/vm_backup_cancel', handler=on_cancel)

        env.send('/backup')
        env.wait('XÁC NHẬN BACKUP')
        env.cb('bk:go', mid=600)
        env.wait('BACKUP ĐANG CHẠY', timeout=20)
        env.cb('job', mid=601)
        env.wait('Đã yêu cầu HỦY')
        env.wait('Đã hủy backup an toàn', timeout=20)
        assert r.matched_calls('touch /tmp/vm_backup_cancel')
    finally:
        env.cleanup()


def test_restore_flow():
    env = Env()
    try:
        env.configure()
        r = env.remote
        r.add('__PROBE__', out=json.dumps(DEFAULT_PROBE))
        r.add('rclone lsd', out=RCL_LSD)
        r.add('wm_03_size.sh', out='SIZE {"bytes":26900000000,"count":30}')
        # Vài vòng "đang tải" (đủ lâu để qua throttle edit 2.5s) rồi mới xong
        r.add_queue('MARK_COUNT', [DL_RUNNING_NOTICE] * 80, default=DL_DONE_NOTICE)
        r.add('wm_assemble.log', out=ASM_DONE)
        r.add('VA_ALL_DONE', out='D1\nK1')
        r.add('wm_extract.log', out=EXT_DONE)
        r.add('stat -c %s /mnt/data/data.img', out='322122547200')
        env.send('/restore')
        env.wait('KHÔI PHỤC TỪ ONEDRIVE')
        env.cb('rst:pk:0', mid=700)
        env.wait('XÁC NHẬN KHÔI PHỤC')
        env.cb('rst:go:0', mid=700)
        # Tiến độ tải phải gọn: có tốc độ + ETA đã tách, KHÔNG in log rclone thô
        env.wait('còn 3m54s', timeout=20)
        env.assert_no('NOTICE')
        e = env.wait('HOÀN TẤT KHÔI PHỤC', timeout=30)
        assert 'data_backup_20261017_120000' in e['text']
        assert [x[0] for x in r.launched][:4] == [
            '04_dl.sh', '05_assemble.sh', '06_verify_arc.sh', '07_extract.sh']
    finally:
        env.cleanup()


def test_vm_start():
    env = Env()
    try:
        env.configure()
        r = env.remote
        r.add('__PROBE__', out=json.dumps(DEFAULT_PROBE))
        env.send('/vm')
        env.wait('QUẢN LÝ VM')
        env.cb('vm:start', mid=300)
        env.wait('VM đã khởi động', timeout=20)
    finally:
        env.cleanup()


def test_ssh():
    env = Env()
    try:
        env.configure()
        r = env.remote
        r.add('__PROBE__', out=json.dumps(DEFAULT_PROBE))
        r.add_queue('base64 -d | bash', ['hello world\nline2', 'y' * 4000],
                    default='y' * 4000)
        env.send('/ssh echo hello')
        env.wait('<pre>hello world')
        env.send('/ssh echo long')
        env.wait('output dài')
        env.wait('ssh_output.txt', kind='doc', key='filename')
    finally:
        env.cleanup()


def test_callbacks():
    env = Env()
    try:
        env.configure()
        r = env.remote
        r.add('__PROBE__', out=json.dumps(DEFAULT_PROBE))
        r.add('rclone lsd', out=RCL_LSD)
        env.cb('home', mid=400)
        e = env.wait('TRẠNG THÁI HỆ THỐNG')
        assert 'Watchdog' not in e['text'] and 'Tự động backup' not in e['text']
        env.cb('vm:menu', mid=401)
        env.wait('QUẢN LÝ VM')
        env.cb('set:menu', mid=403)
        e = env.wait('CÀI ĐẶT')
        assert 'Hook' not in e['text'] and 'tự động backup' not in e['text']
        env.cb('rst:scan', mid=404)
        env.wait('KHÔI PHỤC TỪ ONEDRIVE')
        env.cb('rst:pk:5', mid=405)
        env.wait('Danh sách đã cũ')
        env.wait('data_backup_20261017_120000')
    finally:
        env.cleanup()


def test_cleanup():
    env = Env()
    try:
        env.configure()
        r = env.remote
        r.add('__PROBE__', out=json.dumps(DEFAULT_PROBE))
        r.add('sudo rm -rf /mnt/data/backup; ',
              out='/dev/loop0 450G 100G 350G 23% /mnt/data')
        env.send('/cleanup')
        env.wait('DỌN FILE TẠM')
        env.cb('cl:go', mid=500)
        env.wait('Đã dọn xong', timeout=15)
    finally:
        env.cleanup()


def test_ghcli_token_flow():
    """Regression: check_token không dùng gh auth; login_with_token phải gỡ GH_TOKEN."""
    tmp = tempfile.mkdtemp(prefix='wmtest_gh_')
    try:
        gh_path = os.path.join(tmp, 'fake_gh.py')
        with open(gh_path, 'w', encoding='utf-8') as f:
            f.write(FAKE_GH)
        cfg = cfgmod.Config(os.path.join(tmp, 'cfg.json'))
        cli = ghmod.GhCli(cfg, tmp, binary=gh_path)
        ok, login, scopes = cli.check_token('ghp_x')
        assert ok, f'check_token phải OK, nhận: {(ok, login, scopes)}'
        assert login == 'phuc-test'
        assert 'codespace' in scopes
        # token sai → trả lỗi HTTP rõ ràng (fake gh trả rc=2, không có header HTTP)
        os.environ['GITHUB_TOKEN'] = 'x'
        try:
            env = cli._env('abc', from_cfg=False)
            assert env.get('GH_TOKEN') == 'abc'
            assert 'GITHUB_TOKEN' not in env
        finally:
            os.environ.pop('GITHUB_TOKEN', None)
        r = cli.login_with_token('ghp_x')
        assert r.ok, f'login_with_token phải chạy khi đã gỡ GH_TOKEN: {r.err}'
        # ensure_ssh_registered: key đã có sẵn → chỉ cần đăng ký qua fake gh
        sshdir = os.path.join(tmp, 'home', '.ssh')
        os.makedirs(sshdir, exist_ok=True)
        with open(os.path.join(sshdir, 'id_ed25519'), 'w', encoding='utf-8') as f:
            f.write('priv-key')
        with open(os.path.join(sshdir, 'id_ed25519.pub'), 'w', encoding='utf-8') as f:
            f.write('ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI fake@test\n')
        ok2, msg2 = cli.ensure_ssh_registered()
        assert ok2, f'ensure_ssh_registered phải OK: {msg2}'
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_cs_create_flow():
    env = Env()
    try:
        env.cfg.set('github_token', 'ghp_test')
        env.cfg.set('github_login', 'phuc')
        env.cfg.set('owner_chat_id', OWNER)
        r = env.remote
        r.add('ST_ALL_DONE', out='D1\nK1')
        env.gh.repo_exists = lambda repo: False
        env.cb('set:csnew', mid=800)
        env.wait('Tạo Codespace mới')
        env.send('auto', mid=20)
        env.wait('Môi trường Codespace sẵn sàng', timeout=25)
        assert env.cfg.get('codespace') == 'winvm-cs-new'
        assert env.cfg.get('cs_repo') == 'phuc/winvm-codespace'
        assert env.gh.created_repos == ['phuc/winvm-codespace']
        assert env.gh.created_cs == [('phuc/winvm-codespace', 'standardLinux32gb')]
        assert env.gh.ssh_calls >= 1
        assert '01_setup.sh' in [x[0] for x in r.launched]
    finally:
        env.cleanup()


def test_actions_flow():
    env = Env()
    try:
        env.cfg.set('github_token', 'ghp_test')
        env.cfg.set('github_login', 'phuc')
        env.cfg.set('owner_chat_id', OWNER)
        env.send('/actions')
        env.wait('VM WINDOWS TRÊN GITHUB ACTIONS')
        # Chưa có Tailscale key -> bot phải hỏi trước khi chạy
        env.cb('act:go', mid=900)
        env.wait('Tailscale auth key')
        env.send('tskey-auth-test123', mid=30)
        env.wait('ĐÃ KHỞI ĐỘNG VM ACTIONS', timeout=25)
        assert env.cfg.get('tailscale_key') == 'tskey-auth-test123'
        assert env.gh.dispatched == [('phuc/winvm-codespace', 'winvm-actions.yml')]
        assert env.gh.puts == [('phuc/winvm-codespace',
                                '.github/workflows/winvm-actions.yml', 0)]
        assert env.gh.secrets == [('phuc/winvm-codespace', 'TAILSCALE_AUTHKEY',
                                   'tskey-auth-test123')]
        env.gh.action_runs[0]['status'] = 'in_progress'
        env.cb('act:st', mid=901)
        e = env.wait('VM ACTIONS — đang chạy', timeout=20)
        assert '100.101.102.103' in e['text']

        # File IP mới (đúng run hiện tại) được ưu tiên
        env.gh.ip_file = 'TS_IP=100.9.9.9 run=123'
        time.sleep(0.5)
        n = len(env.events())
        env.cb('act:st', mid=902)
        e2 = env.wait('100.9.9.9', timeout=20, since=n)
        assert '100.101.102.103' not in e2['text']

        # File IP cũ (run khác) phải bị bỏ qua → quay về log
        env.gh.ip_file = 'TS_IP=100.8.8.8 run=999'
        time.sleep(0.5)
        n = len(env.events())
        env.cb('act:st', mid=903)
        e3 = env.wait('100.101.102.103', timeout=20, since=n)
        assert '100.8.8.8' not in e3['text']

        # Màn /actions hiển thị run gần nhất + IP ngay trên đầu (UI mới)
        env.gh.ip_file = 'TS_IP=100.9.9.9 run=123'
        n = len(env.events())
        env.cb('act:menu', mid=904)
        e4 = env.wait('100.9.9.9', timeout=20, since=n)
        assert 'Run #123' in e4['text']
    finally:
        env.cleanup()


def test_keepalive_log_callable():
    """Regression: Keepalive nhận logging.Logger (không phải hàm) vẫn phải gọi log được."""
    tmp = tempfile.mkdtemp(prefix='wmtest_ka_')
    try:
        cfg = cfgmod.Config(os.path.join(tmp, 'cfg.json'))
        ka = ka_mod.Keepalive(FakeGh(), cfg, logging.getLogger('ka-test'))
        ka.log('keepalive: test log')  # trước đây: TypeError 'Logger' object is not callable
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


TESTS = [
    test_claim_status,
    test_nonowner_ignored,
    test_settings_flows,
    test_repo_flow,
    test_keepalive_log_view,
    test_backup_flow,
    test_backup_cancel,
    test_restore_flow,
    test_vm_start,
    test_ssh,
    test_callbacks,
    test_cleanup,
    test_ghcli_token_flow,
    test_cs_create_flow,
    test_actions_flow,
    test_keepalive_log_callable,
]


def main():
    try:  # console Windows hay lỗi cp1252 khi in tiếng Việt
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    ok = True
    for t in TESTS:
        t0 = time.time()
        try:
            t()
            print(f'PASS  {t.__name__}  ({time.time() - t0:.1f}s)')
        except Exception as e:  # noqa
            ok = False
            import traceback
            print(f'FAIL  {t.__name__}: {e}')
            traceback.print_exc()
    print('ALL PASS' if ok else 'CÓ TEST FAIL')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
