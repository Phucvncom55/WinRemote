# -*- coding: utf-8 -*-
"""Đăng nhập OneDrive tự động bằng OAuth 2.0 device code flow.

Không cần trình duyệt trên server: bot hiển thị link + mã, người dùng đăng nhập
trên điện thoại/máy tính, bot tự poll lấy token (access + refresh) và lưu vào
bot-config.json. Token được ghi vào rclone.conf trên Codespace ở bước /setup.
"""
import datetime
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

# Client công khai của Microsoft Office — hỗ trợ device code cho cả tài khoản
# Microsoft cá nhân và tài khoản công việc (người dùng tự đồng ý quyền).
DEFAULT_CLIENT_ID = 'd3590ed6-52b3-4102-aeff-aad2292ab01c'
SCOPE = 'Files.ReadWrite.All offline_access'
AUTHORITY = 'https://login.microsoftonline.com/common/oauth2/v2.0'
DEVICE_URL = AUTHORITY + '/devicecode'
TOKEN_URL = AUTHORITY + '/token'


def _post(url, fields, timeout=30):
    data = urllib.parse.urlencode(fields).encode('utf-8')
    req = urllib.request.Request(
        url, data=data,
        headers={'Content-Type': 'application/x-www-form-urlencoded',
                 'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode('utf-8', 'replace')), None
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode('utf-8', 'replace'))
        except Exception:
            body = {'error': 'http_%s' % e.code,
                    'error_description': str(e)}
        return body, None
    except Exception as e:  # noqa
        return None, f'Lỗi mạng khi gọi Microsoft: {e}'


def device_start(client_id=None):
    """Bắt đầu device flow. Trả (info, error)."""
    cid = (client_id or DEFAULT_CLIENT_ID).strip()
    data, err = _post(DEVICE_URL, {'client_id': cid, 'scope': SCOPE})
    if err:
        return None, err
    if not data or not data.get('device_code'):
        msg = (data or {}).get('error_description') or (data or {}).get('error') or 'không rõ'
        return None, f'Microsoft từ chối yêu cầu đăng nhập: {msg}'
    data['client_id'] = cid
    return data, None


def normalize_token(raw, client_id=None):
    """Chuẩn hóa response token thành JSON rclone (có expiry RFC3339)."""
    tok = {
        'access_token': str(raw.get('access_token') or ''),
        'token_type': str(raw.get('token_type') or 'Bearer'),
    }
    if raw.get('refresh_token'):
        tok['refresh_token'] = str(raw.get('refresh_token'))
    if raw.get('scope'):
        tok['scope'] = str(raw.get('scope'))
    try:
        ttl = int(raw.get('expires_in') or 3600)
    except (TypeError, ValueError):
        ttl = 3600
    exp = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=max(60, ttl - 60))
    tok['expiry'] = exp.strftime('%Y-%m-%dT%H:%M:%SZ')
    if client_id:
        tok['_client_id'] = client_id
    return tok


def device_poll(device_code, client_id=None, expires_in=900, interval=5,
                cancelled=None, progress=None):
    """Poll token endpoint tới khi người dùng đăng nhập xong.

    Trả (token_dict, error). error = 'cancelled' nếu bị hủy.
    """
    cid = (client_id or DEFAULT_CLIENT_ID).strip()
    deadline = time.time() + max(30, int(expires_in or 900))
    interval = max(3, int(interval or 5))
    last_note = 0.0
    while time.time() < deadline:
        if cancelled and cancelled():
            return None, 'cancelled'
        data, err = _post(TOKEN_URL, {
            'grant_type': 'urn:ietf:params:oauth:grant-type:device_code',
            'client_id': cid,
            'device_code': device_code,
        })
        if err:
            return None, err
        if data and data.get('access_token'):
            return normalize_token(data, cid), None
        code = str((data or {}).get('error') or '')
        desc = str((data or {}).get('error_description') or '')
        if code == 'authorization_pending':
            pass
        elif code == 'slow_down':
            interval = min(30, interval + 5)
        elif code == 'expired_token':
            return None, 'Mã đăng nhập đã hết hạn — bấm đăng nhập lại.'
        elif code == 'authorization_declined':
            return None, 'Bạn đã từ chối cấp quyền OneDrive.'
        elif code in ('bad_verification_code', 'invalid_grant'):
            return None, 'Mã đăng nhập không hợp lệ hoặc đã dùng rồi.'
        elif code:
            return None, f'Microsoft báo lỗi: {code}. {desc[:200]}'
        if progress:
            try:
                progress(int(max(0, deadline - time.time())), code)
            except Exception:
                pass
        last_note = time.time()
        # ngủ ngắt được
        end = time.time() + interval
        while time.time() < end:
            if cancelled and cancelled():
                return None, 'cancelled'
            time.sleep(0.5)
    return None, 'Hết thời gian chờ đăng nhập (15 phút) — thử lại.'


def refresh_onedrive(token_json, client_id=None):
    """Làm mới access_token từ refresh_token. Trả (token, error)."""
    try:
        tok = json.loads(token_json or '{}')
    except ValueError:
        return None, 'Token không phải JSON'
    if not isinstance(tok, dict) or not tok.get('refresh_token'):
        return None, 'Token thiếu refresh_token — cần đăng nhập lại'
    cid = (client_id or tok.get('_client_id') or DEFAULT_CLIENT_ID).strip()
    data, err = _post(TOKEN_URL, {
        'grant_type': 'refresh_token',
        'client_id': cid,
        'refresh_token': str(tok.get('refresh_token')),
        'scope': SCOPE,
    })
    if err:
        return None, err
    if not data or not data.get('access_token'):
        msg = (data or {}).get('error_description') or (data or {}).get('error') or 'không rõ'
        return None, f'Không làm mới được token: {msg}'
    if not data.get('refresh_token'):
        data['refresh_token'] = tok.get('refresh_token')
    return normalize_token(data, cid), None


# ------------------------- Google Drive -------------------------
# Client công khai của rclone (lấy từ backend/drive của rclone, dạng đã giải mã).
# Google đang ngừng client dùng chung này trong 2026; nên dùng OAuth client riêng
# (Desktop app) qua /gdrive → "🔑 OAuth client riêng".
# Có thể override bằng gdrive_client_id / gdrive_client_secret trong bot-config.json.
GDRIVE_CLIENT_ID = '202264815644.apps.googleusercontent.com'
GDRIVE_CLIENT_SECRET = 'X4Z3ca8xfWDb1Voo-F9a7ZxJ'
GDRIVE_TOKEN_URL = 'https://oauth2.googleapis.com/token'
GDRIVE_AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
# Loopback của client desktop: sau khi đồng ý, Google chuyển tới địa chỉ này
# (?code=…). Không có server trên máy người dùng nên trình duyệt báo lỗi —
# bình thường, chỉ cần copy nguyên địa chỉ dán lại vào chat.
GDRIVE_REDIRECT_URI = 'http://127.0.0.1:53682/'
GDRIVE_SCOPE = 'https://www.googleapis.com/auth/drive'


def gdrive_auth_url(client_id=None, redirect_uri=None):
    """Link đăng nhập Google Drive (authorization code + loopback)."""
    params = {
        'client_id': (client_id or GDRIVE_CLIENT_ID).strip(),
        'redirect_uri': redirect_uri or GDRIVE_REDIRECT_URI,
        'response_type': 'code',
        'scope': GDRIVE_SCOPE,
        'access_type': 'offline',
        'prompt': 'consent',
    }
    return GDRIVE_AUTH_URL + '?' + urllib.parse.urlencode(params)


def parse_gdrive_code(text):
    """Tách authorization code từ link redirect (hoặc mã dán trực tiếp).

    Trả (code, error) — chấp nhận cả URL đầy đủ và chuỗi `code=…`.
    """
    raw = (text or '').strip().strip('<>"\'')
    if not raw:
        return None, 'Chưa có nội dung — dán link redirect hoặc mã đăng nhập.'
    looks_like_url = raw.lower().startswith(('http://', 'https://')) or 'code=' in raw
    if looks_like_url:
        url = raw if '://' in raw else 'http://127.0.0.1/?' + raw.split('?', 1)[-1]
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        if q.get('error'):
            msg = (q.get('error_description') or q.get('error') or ['lỗi'])[0]
            return None, f'Google từ chối cấp quyền: {msg}'
        code = (q.get('code') or [''])[0]
        if not code:
            return None, ('Link không chứa mã <code>code=…</code>. Copy đúng địa chỉ '
                          'trang báo "không truy cập được" (http://127.0.0.1:53682/?code=…).')
        return code, None
    if re.fullmatch(r'[A-Za-z0-9_./\-]{20,}', raw):
        return raw, None
    return None, 'Mã đăng nhập không hợp lệ — dán lại link redirect đầy đủ.'


def gdrive_exchange_code(code, client_id=None, client_secret=None, redirect_uri=None):
    """Đổi authorization code thành token (access + refresh). Trả (token, error)."""
    code = (code or '').strip()
    if not code:
        return None, 'Thiếu mã đăng nhập.'
    cid = (client_id or GDRIVE_CLIENT_ID).strip()
    secret = (client_secret or GDRIVE_CLIENT_SECRET).strip()
    data, err = _post(GDRIVE_TOKEN_URL, {
        'grant_type': 'authorization_code',
        'code': code,
        'client_id': cid,
        'client_secret': secret,
        'redirect_uri': redirect_uri or GDRIVE_REDIRECT_URI,
    })
    if err:
        return None, err
    if not data or not data.get('access_token'):
        msg = (data or {}).get('error_description') or (data or {}).get('error') or 'không rõ'
        return None, f'Google không đổi được mã đăng nhập: {msg}'
    if not data.get('refresh_token'):
        return None, ('Google không trả <code>refresh_token</code>. Gỡ quyền bot tại '
                      'myaccount.google.com/permissions rồi thêm lại bằng link.')
    tok = normalize_token(data, cid)
    tok['_client_secret'] = secret
    return tok, None


def refresh_gdrive(token_json, client_id=None, client_secret=None):
    """Làm mới access_token Google Drive từ refresh_token. Trả (token, error)."""
    try:
        tok = json.loads(token_json or '{}')
    except ValueError:
        return None, 'Token không phải JSON'
    if not isinstance(tok, dict) or not tok.get('refresh_token'):
        return None, 'Token thiếu refresh_token — cần đăng nhập lại'
    cid = (client_id or tok.get('_client_id') or GDRIVE_CLIENT_ID).strip()
    secret = (client_secret or tok.get('_client_secret') or GDRIVE_CLIENT_SECRET).strip()
    data, err = _post(GDRIVE_TOKEN_URL, {
        'grant_type': 'refresh_token',
        'client_id': cid,
        'client_secret': secret,
        'refresh_token': str(tok.get('refresh_token')),
    })
    if err:
        return None, err
    if not data or not data.get('access_token'):
        msg = (data or {}).get('error_description') or (data or {}).get('error') or 'không rõ'
        return None, f'Không làm mới được token Google Drive: {msg}'
    if not data.get('refresh_token'):
        data['refresh_token'] = tok.get('refresh_token')
    new = normalize_token(data, cid)
    new['_client_secret'] = secret
    return new, None
