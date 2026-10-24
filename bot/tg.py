# -*- coding: utf-8 -*-
"""Telegram client tối giản (chỉ stdlib) + bản giả lập cho test local."""
import json
import os
import threading
import time
import urllib.error
import urllib.request
import uuid

API_BASE = 'https://api.telegram.org/bot{token}/{method}'
MAX_LEN = 3900


class TelegramError(Exception):
    def __init__(self, message, retry_after=0):
        super().__init__(message)
        self.retry_after = retry_after


def clamp(text, limit=MAX_LEN):
    text = str(text if text is not None else '')
    if len(text) <= limit:
        return text
    return text[:limit - 30] + f"\n…(cắt bớt {len(text) - limit + 30} ký tự)"


def kb(rows):
    """rows: list[list[(text, callback_data | url)]] -> reply_markup dict."""
    buttons = []
    for row in rows:
        r = []
        for item in row:
            text, data = item
            if isinstance(data, str) and (data.startswith('http://') or data.startswith('https://')):
                r.append({'text': text, 'url': data})
            else:
                r.append({'text': text, 'callback_data': data})
        buttons.append(r)
    return {'inline_keyboard': buttons}


class TelegramClient:
    """Client thật dùng Telegram Bot API qua HTTPS (long polling)."""

    def __init__(self, token):
        self.token = token

    def _call(self, method, params, timeout=60):
        url = API_BASE.format(token=self.token, method=method)
        data = json.dumps(params).encode('utf-8')
        req = urllib.request.Request(
            url, data=data,
            headers={'Content-Type': 'application/json; charset=utf-8'},
            method='POST')
        last_err = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    body = json.loads(resp.read().decode('utf-8'))
                if not body.get('ok'):
                    desc = body.get('description', 'unknown error')
                    code = body.get('error_code', 400)
                    retry = 0
                    if code == 429:
                        retry = body.get('parameters', {}).get('retry_after', 5)
                    raise TelegramError(desc, retry_after=retry)
                return body.get('result')
            except urllib.error.HTTPError as e:
                try:
                    body = json.loads(e.read().decode('utf-8'))
                    desc = body.get('description', str(e))
                    retry = body.get('parameters', {}).get('retry_after', 0)
                except Exception:
                    desc = str(e)
                    retry = 0
                last_err = TelegramError(desc, retry_after=retry or 0)
                if e.code == 429:
                    time.sleep(min(30, retry or 5))
                    continue
                if e.code >= 500:
                    time.sleep(2 + attempt * 2)
                    continue
                raise last_err
            except TelegramError as e:
                last_err = e
                if e.retry_after:
                    time.sleep(min(30, e.retry_after))
                    continue
                raise
            except Exception as e:  # lỗi mạng
                last_err = e
                time.sleep(2 + attempt * 2)
        raise TelegramError(f'Telegram API lỗi: {last_err}')

    def _call_multipart(self, method, fields, file_name, file_content, timeout=120):
        boundary = '----wm' + uuid.uuid4().hex
        chunks = []
        for k, v in fields.items():
            chunks.append(('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n'
                           % (boundary, k, v)).encode('utf-8'))
        chunks.append(('--%s\r\nContent-Disposition: form-data; name="document"; filename="%s"\r\n'
                       'Content-Type: application/octet-stream\r\n\r\n'
                       % (boundary, file_name)).encode('utf-8'))
        chunks.append(file_content)
        chunks.append(('\r\n--%s--\r\n' % boundary).encode('utf-8'))
        body = b''.join(chunks)
        url = API_BASE.format(token=self.token, method=method)
        req = urllib.request.Request(
            url, data=body,
            headers={'Content-Type': 'multipart/form-data; boundary=%s' % boundary},
            method='POST')
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            out = json.loads(resp.read().decode('utf-8'))
        if not out.get('ok'):
            raise TelegramError(out.get('description', 'send document failed'))
        return out.get('result')

    def get_updates(self, offset=None, timeout=50):
        params = {'timeout': timeout,
                  'allowed_updates': ['message', 'callback_query']}
        if offset is not None:
            params['offset'] = offset
        try:
            return self._call('getUpdates', params, timeout=timeout + 20) or []
        except TelegramError:
            time.sleep(3)
            return []

    def send_message(self, chat_id, text, buttons=None, disable_preview=True):
        params = {'chat_id': chat_id, 'text': clamp(text), 'parse_mode': 'HTML',
                  'disable_web_page_preview': disable_preview}
        if buttons:
            params['reply_markup'] = json.dumps(buttons)
        return self._call('sendMessage', params)

    def edit_message(self, chat_id, message_id, text, buttons=None):
        params = {'chat_id': chat_id, 'message_id': message_id, 'text': clamp(text),
                  'parse_mode': 'HTML', 'disable_web_page_preview': True}
        params['reply_markup'] = json.dumps(buttons if buttons else {'inline_keyboard': []})
        try:
            return self._call('editMessageText', params)
        except TelegramError as e:
            low = str(e).lower()
            if 'not modified' in low:
                return None
            if 'message to edit not found' in low or 'message can\'t be edited' in low:
                return self.send_message(chat_id, text, buttons=buttons)
            raise

    def delete_message(self, chat_id, message_id):
        try:
            return self._call('deleteMessage', {'chat_id': chat_id, 'message_id': message_id})
        except TelegramError:
            return None

    def send_document(self, chat_id, filename, content_bytes, caption=None):
        fields = {'chat_id': str(chat_id), 'parse_mode': 'HTML'}
        if caption:
            fields['caption'] = clamp(caption, 1000)
        return self._call_multipart('sendDocument', fields, filename, content_bytes)

    def answer_callback(self, callback_id, text=''):
        try:
            self._call('answerCallbackQuery',
                       {'callback_query_id': callback_id, 'text': text[:190]})
        except TelegramError:
            pass

    def send_chat_action(self, chat_id, action='typing'):
        try:
            self._call('sendChatAction', {'chat_id': chat_id, 'action': action})
        except TelegramError:
            pass

    def set_my_commands(self):
        cmds = [
            ('status', '📊 Trạng thái Codespace + VM'),
            ('backup', '💾 Backup VM lên OneDrive'),
            ('restore', '♻️ Khôi phục VM từ OneDrive'),
            ('setup', '⚙️ Cài ổ dữ liệu + rclone + OneDrive'),
            ('vm', '🖥 Bật/dừng/quản lý VM'),
            ('actions', '🖥 VM Windows tạm trên GitHub Actions (RDP)'),
            ('ssh', '📡 Chạy lệnh bash trong Codespace'),
            ('cleanup', '🧹 Dọn file tạm trên Codespace'),
            ('settings', '🔧 Token / Codespace / tuỳ chọn'),
            ('cancel', '✖️ Hủy việc đang chạy'),
            ('help', '❓ Trợ giúp'),
        ]
        try:
            self._call('setMyCommands', {
                'commands': [{'command': c, 'description': d} for c, d in cmds]})
        except TelegramError:
            pass


class FakeTelegram:
    """Bản giả lập cho test local: đọc update từ inbox.jsonl, ghi ra outbox.jsonl."""

    def __init__(self, inbox_path, outbox_path):
        self.inbox = inbox_path
        self.outbox = outbox_path
        self.lock = threading.RLock()
        self._counter = 0
        self._update_id = 0
        self._read_pos = 0
        open(self.inbox, 'a', encoding='utf-8').close()
        open(self.outbox, 'a', encoding='utf-8').close()

    def _emit(self, kind, payload):
        with self.lock:
            self._counter += 1
            rec = {'seq': self._counter, 'ts': time.time(), 'kind': kind}
            rec.update(payload)
            with open(self.outbox, 'a', encoding='utf-8') as f:
                f.write(json.dumps(rec, ensure_ascii=False) + '\n')
            return self._counter

    def get_updates(self, offset=None, timeout=50):
        deadline = time.time() + min(timeout, 2)
        while time.time() < deadline:
            with self.lock:
                if os.path.exists(self.inbox):
                    with open(self.inbox, 'r', encoding='utf-8') as f:
                        f.seek(self._read_pos)
                        lines = f.readlines()
                        self._read_pos = f.tell()
                else:
                    lines = []
            out = []
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    upd = json.loads(line)
                except ValueError:
                    continue
                self._update_id += 1
                upd['update_id'] = self._update_id
                if offset is None or upd['update_id'] >= offset:
                    out.append(upd)
            if out:
                return out
            time.sleep(0.15)
        return []

    def send_message(self, chat_id, text, buttons=None, disable_preview=True):
        mid = self._emit('send', {'chat_id': chat_id, 'text': clamp(text), 'buttons': buttons})
        return {'message_id': 1000000 + mid, 'chat': {'id': chat_id}}

    def edit_message(self, chat_id, message_id, text, buttons=None):
        self._emit('edit', {'chat_id': chat_id, 'message_id': message_id,
                            'text': clamp(text), 'buttons': buttons})
        return None

    def delete_message(self, chat_id, message_id):
        self._emit('delete', {'chat_id': chat_id, 'message_id': message_id})
        return True

    def send_document(self, chat_id, filename, content_bytes, caption=None):
        try:
            text = content_bytes.decode('utf-8', 'replace')
        except Exception:
            text = ''
        mid = self._emit('doc', {'chat_id': chat_id, 'filename': filename,
                                 'caption': caption, 'text': text})
        return {'message_id': 1000000 + mid}

    def answer_callback(self, callback_id, text=''):
        self._emit('callback_answer', {'callback_id': callback_id, 'text': text})

    def send_chat_action(self, chat_id, action='typing'):
        pass

    def set_my_commands(self):
        pass


def make_telegram(env):
    mode = (env.get('TELEGRAM_MODE') or 'real').strip().lower()
    if mode == 'fake':
        return FakeTelegram(env['TELEGRAM_FAKE_INBOX'], env['TELEGRAM_FAKE_OUTBOX'])
    return TelegramClient(env['TELEGRAM_BOT_TOKEN'])
