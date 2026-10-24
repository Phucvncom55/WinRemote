# -*- coding: utf-8 -*-
"""Test ConfigPusher/pull_text với GitHub API giả (HTTP local, không mạng)."""
import base64
import json
import logging
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import bot.sync as syncmod  # noqa: E402
from bot import cfg as cfgmod  # noqa: E402

LOG = logging.getLogger('test-sync')
FILES = {}
LOCK = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split('/contents/', 1)[-1]
        with LOCK:
            item = FILES.get(path)
        if item is None:
            self._send(404, {'message': 'Not Found'})
            return
        text, sha = item
        self._send(200, {
            'encoding': 'base64', 'sha': sha,
            'content': base64.b64encode(text.encode('utf-8')).decode('ascii'),
        })

    def do_PUT(self):
        path = self.path.split('/contents/', 1)[-1]
        n = int(self.headers.get('Content-Length') or 0)
        body = json.loads(self.rfile.read(n).decode('utf-8'))
        text = base64.b64decode(body['content']).decode('utf-8')
        sha = 'sha-%d' % (len(FILES) + 1)
        with LOCK:
            FILES[path] = (text, sha)
        self._send(201, {'content': {'sha': sha}})

    def log_message(self, *args):
        pass


def wait_for(pred, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def main():
    global FILES
    srv = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    old_api = syncmod.API
    syncmod.API = 'http://127.0.0.1:%d' % srv.server_address[1]
    tmp = tempfile.mkdtemp(prefix='wmsync_')
    try:
        cfg = cfgmod.Config(os.path.join(tmp, 'bot-config.json'))
        cfg.set('github_token', 'ghp_test')
        cfg.set('codespace', 'cs-1')
        pusher = syncmod.ConfigPusher(cfg, 'u/r', 'bot-config.json', LOG, debounce=0.1)
        cfg.on_save = pusher.schedule
        cfg.set('codespace', 'cs-2')

        assert wait_for(lambda: 'bot-config.json' in FILES), 'pusher chưa ghi file'
        text, sha = FILES['bot-config.json']
        obj = json.loads(text)
        assert obj['codespace'] == 'cs-2'
        assert obj['github_token'] == 'ghp_test'
        assert sha

        pulled, sha2 = syncmod.pull_text('u/r', 'bot-config.json', 'ghp_test')
        assert pulled and json.loads(pulled)['codespace'] == 'cs-2'
        assert sha2 == sha

        # Cập nhật lần 2 + flush đồng bộ
        cfg.set('codespace', 'cs-3')
        assert pusher.flush(timeout=5)
        assert json.loads(FILES['bot-config.json'][0])['codespace'] == 'cs-3'

        # Không có token -> không ghi
        cfg2 = cfgmod.Config(os.path.join(tmp, 'bot2.json'))
        pusher2 = syncmod.ConfigPusher(cfg2, 'u/r', 'other.json', LOG, debounce=0.1)
        cfg2.on_save = pusher2.schedule
        cfg2.set('codespace', 'cs-x')
        time.sleep(0.4)
        assert 'other.json' not in FILES

        # 404 -> pull_text trả None
        assert syncmod.pull_text('u/r', 'missing.json', 'ghp_test') == (None, '')
        print('PASS  test_sync')
        return 0
    finally:
        syncmod.API = old_api
        srv.shutdown()
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
