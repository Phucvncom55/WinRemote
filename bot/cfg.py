# -*- coding: utf-8 -*-
"""Lưu cấu hình bot vào bot-config.json (thư mục DATA_DIR, mặc định ./data)."""
import json
import os
import threading
import time

DEFAULTS = {
    'version': 2,
    'owner_chat_id': 0,
    'github_token': '',
    'github_login': '',
    'onedrive_token': '',
    'od_client_id': '',
    'od_owner': '',
    'od_total': 0,
    'od_used': 0,
    # Google Drive: gom nhiều tài khoản thành 1 remote union (ổ lớn)
    'gdrive_accounts': [],
    'gdrive_client_id': '',
    'gdrive_client_secret': '',
    'gdrive_remote': 'gdrive',
    'backup_backend': 'onedrive',
    'codespace': '',
    'cs_repo': '',
    'cs_machine': 'standardLinux32gb',
    'tailscale_key': '',
    'actions_windows': 'windows-2025',
    'actions_minutes': 350,
    'keepalive_enabled': True,
    'keepalive_interval': 60,
    'quota_core_hours': 120,
    'quota_storage_gb_month': 15,
    'quota_used_core_hours': None,
    'quota_used_storage_gb_month': None,
    'quota_cache_sec': 300,
    'vm': {
        'cores': 4,
        'ram_gb': 12,
        'disk_gb': 450,
        'user': 'AISTV',
        'password': 'AISTVCloudVM2026',
        'windows_version': '11',
    },
    'updated': 0,
}


class Config:
    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        self.data = json.loads(json.dumps(DEFAULTS))
        # Callback tùy chọn, được gọi sau mỗi lần ghi file (dùng cho sync GitHub).
        self.on_save = None
        self.load()

    def load(self):
        with self.lock:
            try:
                with open(self.path, 'r', encoding='utf-8') as f:
                    raw = json.load(f)
                self._merge(raw)
            except FileNotFoundError:
                pass
            except Exception:
                pass  # file hỏng -> giữ mặc định

    def _merge(self, raw):
        if not isinstance(raw, dict):
            return
        for k, v in raw.items():
            if k == 'vm' and isinstance(v, dict):
                self.data['vm'].update({kk: vv for kk, vv in v.items() if kk in self.data['vm']})
            elif k in self.data:
                self.data[k] = v

    def save(self):
        with self.lock:
            self.data['updated'] = int(time.time())
            tmp = self.path + '.tmp'
            d = os.path.dirname(self.path)
            if d:
                os.makedirs(d, exist_ok=True)
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        cb = self.on_save
        if cb:
            try:
                cb()
            except Exception:
                pass

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value, save=True):
        self.data[key] = value
        if save:
            self.save()

    def vm(self):
        return self.data['vm']

    @property
    def has_github(self):
        return bool(self.data.get('github_token'))

    @property
    def has_onedrive(self):
        return bool(self.data.get('onedrive_token'))

    @property
    def has_gdrive(self):
        return any((a or {}).get('token') for a in (self.data.get('gdrive_accounts') or []))

    @property
    def gdrive_accounts(self):
        return [a for a in (self.data.get('gdrive_accounts') or [])
                if isinstance(a, dict) and a.get('token')]

    def gdrive_total(self):
        total = used = 0
        for a in self.gdrive_accounts:
            total += int(a.get('total') or 0)
            used += int(a.get('used') or 0)
        return total, used

    @property
    def has_codespace(self):
        return bool(self.data.get('codespace'))


def mask(tok, keep=6):
    tok = str(tok or '')
    if not tok:
        return '⛔ chưa có'
    if len(tok) <= keep * 2:
        return '••••••'
    return tok[:keep] + '…' + tok[-4:]
