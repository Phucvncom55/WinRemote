#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bootstrap WinRemote bot trên Google Colab (hoặc Linux sạch).

Chỉ dùng thư viện chuẩn. Việc cần làm:
  1. Cài gh CLI nếu chưa có (tải binary chính thức, không cần apt/root).
  2. Kiểm tra ssh/ssh-keygen.
  3. Lấy TELEGRAM_BOT_TOKEN từ biến môi trường, file secrets/token.txt,
     hoặc repo bí mật trên GitHub.
  4. Khôi phục bot-config.json từ repo bí mật (nếu có).
  5. Chạy bot long-poll.

Cách dùng trên Colab (1 dòng):
  !WM_GITHUB_TOKEN='github_pat_...' \
      bash <(curl -fsSL 'https://raw.githubusercontent.com/Phucvncom55/WinRemote/main/bootstrap.sh?v=1')

Hoặc chạy local:
  python colab.py --check
  python colab.py
"""
import argparse
import base64
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
UA = 'winremote-colab-bootstrap'


def say(msg=''):
    print(msg, flush=True)


def env(name, default=''):
    return (os.environ.get(name) or default).strip()


def run(cmd, check=False, timeout=300, env_extra=None):
    e = os.environ.copy()
    if env_extra:
        e.update(env_extra)
    p = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8',
                       errors='replace', timeout=timeout, env=e)
    if check and p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout or f'rc={p.returncode}').strip())
    return p


def _api_headers(token=''):
    h = {'Accept': 'application/vnd.github+json',
         'X-GitHub-Api-Version': '2022-11-28',
         'User-Agent': UA}
    if token:
        h['Authorization'] = 'Bearer ' + token
    return h


def github_file(repo, path, token='', timeout=30):
    """Đọc file text từ GitHub Contents API. Trả về str hoặc None."""
    if not repo:
        return None
    url = f'https://api.github.com/repos/{repo}/contents/{urllib.parse.quote(path)}'
    req = urllib.request.Request(url, headers=_api_headers(token))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode('utf-8', 'replace'))
    except urllib.error.HTTPError as e:
        if e.code != 404:
            say(f'  ! GitHub API {e.code} khi đọc {repo}/{path}')
        return None
    except Exception as e:  # noqa
        say(f'  ! Lỗi mạng khi đọc {repo}/{path}: {e}')
        return None
    if isinstance(data, dict) and data.get('encoding') == 'base64' and data.get('content'):
        try:
            return base64.b64decode(data['content']).decode('utf-8')
        except Exception:
            return None
    if isinstance(data, dict) and data.get('download_url'):
        try:
            req2 = urllib.request.Request(data['download_url'],
                                          headers={'User-Agent': UA})
            with urllib.request.urlopen(req2, timeout=timeout) as r2:
                return r2.read().decode('utf-8', 'replace')
        except Exception:
            return None
    return None


def _arch_tag():
    m = platform.machine().lower()
    if m in ('x86_64', 'amd64'):
        return 'amd64'
    if m in ('aarch64', 'arm64'):
        return 'arm64'
    raise RuntimeError(f'Kiến trúc chưa hỗ trợ: {platform.machine()}')


def ensure_gh():
    """Cài gh CLI bản Linux chính thức vào ~/.local/bin nếu chưa có."""
    found = shutil.which('gh')
    local = Path.home() / '.local' / 'bin' / 'gh'
    if found:
        return found
    if local.exists():
        return str(local)
    if platform.system().lower() != 'linux':
        raise RuntimeError('Chưa có gh CLI. Cài gh rồi chạy lại.')

    say('  • Chưa có gh CLI — đang tải bản chính thức...')
    tag = _arch_tag()
    api = 'https://api.github.com/repos/cli/cli/releases/latest'
    req = urllib.request.Request(api, headers={'User-Agent': UA,
                                               'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(req, timeout=30) as r:
        rel = json.loads(r.read().decode('utf-8'))
    version = str(rel.get('tag_name') or '').lstrip('v')
    if not version:
        raise RuntimeError('Không đọc được phiên bản gh mới nhất.')
    name = f'gh_{version}_linux_{tag}.tar.gz'
    asset = None
    for a in rel.get('assets') or []:
        if a.get('name') == name:
            asset = a.get('browser_download_url')
            break
    if not asset:
        raise RuntimeError(f'Không tìm thấy asset {name}.')
    local.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        tgz = Path(td) / name
        say(f'    tải {name}...')
        urllib.request.urlretrieve(asset, tgz)
        run(['tar', '-xzf', str(tgz), '-C', td], check=True)
        src = None
        for p in Path(td).rglob('bin/gh'):
            src = p
            break
        if not src:
            raise RuntimeError('Gói gh tải về không có bin/gh.')
        shutil.copy2(src, local)
        local.chmod(local.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    say(f'  ✓ gh: {local}')
    return str(local)


def ensure_ssh():
    missing = [x for x in ('ssh', 'ssh-keygen') if not shutil.which(x)]
    if not missing:
        return
    say(f'  • Thiếu {", ".join(missing)} — thử cài openssh-client...')
    if os.geteuid() != 0:
        raise RuntimeError('Thiếu ssh/ssh-keygen và không phải root. Cài openssh-client rồi chạy lại.')
    run(['apt-get', 'update', '-qq'], timeout=180)
    run(['apt-get', 'install', '-y', '-qq', 'openssh-client'], timeout=180)
    for x in ('ssh', 'ssh-keygen'):
        if not shutil.which(x):
            raise RuntimeError(f'Vẫn thiếu {x} sau khi cài.')


def telegram_me(token):
    url = f'https://api.telegram.org/bot{token}/getMe'
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            d = json.loads(r.read().decode('utf-8'))
    except Exception as e:  # noqa
        return None, str(e)
    if not d.get('ok'):
        return None, str(d.get('description') or 'Telegram từ chối token')
    return d.get('result') or {}, ''


def fetch_token():
    """Ưu tiên env -> secrets/token.txt -> repo bí mật."""
    tok = env('TELEGRAM_BOT_TOKEN')
    if tok:
        return tok, 'env TELEGRAM_BOT_TOKEN'

    local = ROOT / 'secrets' / 'token.txt'
    if local.exists():
        tok = local.read_text(encoding='utf-8').strip()
        if tok:
            return tok, str(local)

    repo = env('WM_SECRETS_REPO')
    token = env('WM_GITHUB_TOKEN')
    if repo:
        txt = github_file(repo, 'token.txt', token)
        if txt and txt.strip():
            return txt.strip().splitlines()[0].strip(), f'{repo}/token.txt'

    raise RuntimeError(
        'Không tìm thấy token bot. Đặt TELEGRAM_BOT_TOKEN, tạo secrets/token.txt, '
        'hoặc đặt WM_SECRETS_REPO + WM_GITHUB_TOKEN để đọc repo bí mật.')


def restore_config(data_dir):
    """Khôi phục bot-config.json từ repo bí mật hoặc từ secrets/ local."""
    dest = Path(data_dir) / 'bot-config.json'
    if dest.exists() and env('WM_PULL_CONFIG') != '1':
        say(f'  • Giữ cấu hình local: {dest} (đặt WM_PULL_CONFIG=1 để kéo đè từ repo)')
        return True
    sources = []
    repo = env('WM_SECRETS_REPO')
    token = env('WM_GITHUB_TOKEN')
    if repo:
        sources.append(('github', f'{repo}/bot-config.json'))
    local = ROOT / 'secrets' / 'bot-config.json'
    if local.exists():
        sources.append(('local', str(local)))

    for kind, label in sources:
        raw = None
        if kind == 'github':
            raw = github_file(repo, 'bot-config.json', token)
        else:
            try:
                raw = local.read_text(encoding='utf-8')
            except Exception:
                raw = None
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except ValueError:
            say(f'  ! Bỏ qua {label}: không phải JSON hợp lệ')
            continue
        if not isinstance(obj, dict):
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            backup = dest.with_suffix('.json.bak')
            try:
                shutil.copy2(dest, backup)
            except Exception:
                pass
        dest.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')
        say(f'  ✓ Khôi phục cấu hình từ {label}')
        return True
    return False


def main():
    ap = argparse.ArgumentParser(description='Chạy bot WinRemote trên Colab/Linux.')
    ap.add_argument('--check', action='store_true', help='kiểm tra môi trường rồi thoát')
    ap.add_argument('--no-install', action='store_true', help='không tự cài gh')
    ap.add_argument('--data-dir', default='', help='thư mục dữ liệu (mặc định ./data)')
    args = ap.parse_args()

    say('╔══════════════════════════════════════════════════════╗')
    say('║          WinRemote Bot — Colab Bootstrap             ║')
    say('╚══════════════════════════════════════════════════════╝')
    say(f'  Python : {sys.version.split()[0]} ({platform.system()} {platform.machine()})')
    say(f'  Thư mục: {ROOT}')

    gh = shutil.which('gh') or str(Path.home() / '.local' / 'bin' / 'gh')
    if not args.no_install:
        try:
            gh = ensure_gh()
        except Exception as e:  # noqa
            say(f'  ! Không cài được gh: {e}')
    if not (Path(gh).exists() and os.access(gh, os.X_OK)):
        say('  ! Thiếu gh CLI — bot sẽ không điều khiển được Codespace.')
    else:
        v = run([gh, '--version'], timeout=20)
        say('  ✓ ' + (v.stdout or '').splitlines()[0].strip())

    try:
        ensure_ssh()
        say('  ✓ ssh/ssh-keygen sẵn sàng')
    except Exception as e:  # noqa
        say(f'  ! SSH: {e}')

    try:
        token, source = fetch_token()
    except Exception as e:  # noqa
        say(f'  ✗ {e}')
        return 2
    say(f'  ✓ Token bot: lấy từ {source}')
    me, err = telegram_me(token)
    if me:
        say(f'  ✓ Telegram: @{me.get("username", "?")} ({me.get("first_name", "")})')
    else:
        say(f'  ✗ Token Telegram không hợp lệ: {err}')
        return 3

    data_dir = os.path.abspath(os.path.expanduser(
        args.data_dir or env('DATA_DIR') or str(ROOT / 'data')))
    os.makedirs(data_dir, exist_ok=True)
    say(f'  • DATA_DIR: {data_dir}')
    if restore_config(data_dir):
        pass
    else:
        say('  • Chưa có bot-config.json — sẽ tạo mới (nhập token qua /settings).')

    code_repo = env('WM_CODE_REPO')
    secrets_repo = env('WM_SECRETS_REPO')
    if secrets_repo:
        say(f'  ✓ Config sync: {secrets_repo}')
    else:
        say('  ! Không có WM_SECRETS_REPO — cấu hình sẽ mất khi Colab reset.')
    if args.check:
        say('\n✓ Môi trường OK. Chạy không có --check để bắt đầu bot.')
        return 0

    child_env = os.environ.copy()
    child_env.update({
        'TELEGRAM_BOT_TOKEN': token,
        'DATA_DIR': data_dir,
        'GH_BIN': gh,
        'PYTHONUNBUFFERED': '1',
        'PYTHONIOENCODING': 'utf-8',
        'TZ': env('TZ', 'Asia/Ho_Chi_Minh'),
    })
    if secrets_repo:
        child_env['CONFIG_SYNC_REPO'] = secrets_repo
        child_env['CONFIG_SYNC_PATH'] = env('WM_CONFIG_PATH', 'bot-config.json')
    # Không chuyển PAT chỉ-đọc sang tiến trình bot.
    child_env.pop('WM_GITHUB_TOKEN', None)
    if code_repo:
        child_env['WM_CODE_REPO'] = code_repo

    say('\n▶ Bắt đầu bot — nhấn Ctrl+C để dừng.\n')
    os.chdir(ROOT)
    try:
        os.execvpe(sys.executable, [sys.executable, '-m', 'bot.main'], child_env)
    except Exception as e:  # noqa
        say(f'Không chạy được bot: {e}')
        return 4


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        say('\nĐã dừng.')
        sys.exit(0)
