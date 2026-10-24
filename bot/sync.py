# -*- coding: utf-8 -*-
"""Đồng bộ bot-config.json lên một repo GitHub private (tùy chọn).

Mục đích: Colab là môi trường tạm. Sau khi runtime bị thu hồi, toàn bộ
token/cấu hình đã nhập qua /settings sẽ mất. Module này giúp bot tự đẩy
cấu hình lên repo bí mật để lần chạy sau tự khôi phục.

Chỉ dùng GitHub REST API bằng stdlib, chạy nền, lỗi mạng không ảnh hưởng bot.
"""
import base64
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

API = 'https://api.github.com'
UA = 'winremote-colab-bot'


def _request(url, token, method='GET', payload=None, timeout=30):
    data = None
    headers = {
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': UA,
        'Authorization': 'Bearer ' + token,
    }
    if payload is not None:
        data = json.dumps(payload).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode('utf-8', 'replace')
            return resp.status, (json.loads(body) if body.strip() else {})
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', 'replace')
        try:
            parsed = json.loads(body)
        except ValueError:
            parsed = {'message': body[:300]}
        return e.code, parsed
    except Exception as e:  # noqa
        return 0, {'message': f'{type(e).__name__}: {e}'}


def _contents_url(repo, path):
    return f'{API}/repos/{repo}/contents/{urllib.parse.quote(path)}'


def pull_text(repo, path, token, timeout=30):
    """Đọc 1 file text từ repo. Trả về (text, sha) hoặc (None, '')."""
    if not (repo and path and token):
        return None, ''
    status, data = _request(_contents_url(repo, path), token, timeout=timeout)
    if status != 200 or not isinstance(data, dict):
        return None, ''
    if data.get('encoding') == 'base64' and data.get('content'):
        try:
            raw = base64.b64decode(data['content']).decode('utf-8')
        except Exception:
            return None, ''
        return raw, str(data.get('sha') or '')
    # File lớn hơn 1 MB: dùng download_url
    dl = data.get('download_url')
    if dl:
        try:
            req = urllib.request.Request(dl, headers={'User-Agent': UA})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode('utf-8', 'replace'), str(data.get('sha') or '')
        except Exception:
            return None, ''
    return None, ''


class ConfigPusher:
    """Đẩy config lên repo theo kiểu debounce, không chặn luồng chính."""

    def __init__(self, cfg, repo, path, log, debounce=2.0, token_getter=None):
        self.cfg = cfg
        self.repo = (repo or '').strip()
        self.path = (path or 'bot-config.json').strip().lstrip('/')
        self.log = log
        self.debounce = max(0.5, float(debounce))
        self.token_getter = token_getter
        self._lock = threading.RLock()
        self._timer = None
        self._dirty = False
        self._last_error = ''
        self._last_ok = 0.0

    @property
    def enabled(self):
        return bool(self.repo)

    def schedule(self):
        if not self.enabled:
            return
        with self._lock:
            self._dirty = True
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self.debounce, self._push)
            self._timer.daemon = True
            self._timer.start()

    def flush(self, timeout=6.0):
        """Đẩy ngay (dùng khi bot thoát). Trả về True nếu đã thử."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            if not self._dirty:
                return False
            self._dirty = False
        t = threading.Thread(target=self._push_now, daemon=True)
        t.start()
        t.join(timeout)
        return True

    def _token(self):
        if self.token_getter:
            try:
                tok = self.token_getter()
                if tok:
                    return tok
            except Exception:
                pass
        return str(self.cfg.get('github_token') or '')

    def _push(self):
        with self._lock:
            self._timer = None
            if not self._dirty:
                return
            self._dirty = False
        self._push_now()

    def _push_now(self):
        token = self._token()
        if not token:
            return
        with self.cfg.lock:
            payload = json.dumps(self.cfg.data, ensure_ascii=False, indent=2)
        b64 = base64.b64encode(payload.encode('utf-8')).decode('ascii')
        url = _contents_url(self.repo, self.path)
        status, data = _request(url, token)
        sha = ''
        if status == 200 and isinstance(data, dict):
            sha = str(data.get('sha') or '')
        elif status not in (404,):
            self._warn(status, data)
            return
        body = {'message': 'bot: sync config', 'content': b64}
        if sha:
            body['sha'] = sha
        status, data = _request(url, token, method='PUT', payload=body)
        if status in (200, 201):
            self._last_ok = time.time()
            self._last_error = ''
            self.log.info('config sync: đã lưu lên %s/%s', self.repo, self.path)
            return
        # 409: file vừa đổi ở nơi khác -> lấy sha mới rồi thử lại 1 lần
        if status == 409:
            status2, data2 = _request(url, token)
            if status2 == 200 and isinstance(data2, dict) and data2.get('sha'):
                body['sha'] = str(data2['sha'])
                status, data = _request(url, token, method='PUT', payload=body)
                if status in (200, 201):
                    self._last_ok = time.time()
                    self._last_error = ''
                    return
        self._warn(status, data)

    def _warn(self, status, data):
        msg = (data or {}).get('message') if isinstance(data, dict) else ''
        text = f'HTTP {status}: {msg or "không rõ"}'
        if text != self._last_error:
            self.log.warning('config sync lỗi: %s', text[:300])
            self._last_error = text
