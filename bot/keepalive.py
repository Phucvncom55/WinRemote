# -*- coding: utf-8 -*-
"""Giữ Codespace không bị dừng vì idle timeout (tái hiện keepalive.ps1).

Mở 1 phiên SSH dài hạn, in 'date' mỗi N giây -> GitHub tính là hoạt động.
Lưu ý: KHÔNG vượt qua giới hạn cứng của GitHub (nếu có); xem README.
"""
import subprocess
import threading
import time


class Keepalive:
    def __init__(self, gh, cfg, log, notify=None):
        self.gh = gh
        self.cfg = cfg
        # log có thể là logging.Logger (dùng .info) hoặc hàm nhận 1 chuỗi
        self.log = getattr(log, 'info', log)
        self.notify = notify
        self.lock = threading.Lock()
        self.stopped = False
        self.proc = None
        self.state = 'off'
        self.last_line = ''
        self.started_ts = 0.0
        self.restarts = 0
        self.last_error = ''
        self.t = threading.Thread(target=self._loop, name='keepalive', daemon=True)

    def start(self):
        self.t.start()

    def stop(self):
        self.stopped = True
        self._kill_proc()

    def _kill_proc(self):
        with self.lock:
            p = self.proc
            self.proc = None
        if p and p.poll() is None:
            try:
                p.terminate()
                try:
                    p.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    p.kill()
            except Exception:
                pass

    def snapshot(self):
        with self.lock:
            return {'state': self.state, 'last_line': self.last_line,
                    'restarts': self.restarts, 'since': self.started_ts,
                    'last_error': self.last_error}

    def _set(self, s):
        with self.lock:
            self.state = s

    def _notify(self, text, key, min_interval=3600):
        if self.notify:
            try:
                self.notify(text, key, min_interval)
            except Exception:
                pass

    def _loop(self):
        while not self.stopped:
            try:
                if not self.cfg.get('keepalive_enabled'):
                    self._set('off')
                    time.sleep(5)
                    continue
                name = self.cfg.get('codespace') or ''
                if not (name and self.cfg.has_github):
                    self._set('chưa cấu hình')
                    time.sleep(15)
                    continue
                st = self.gh.codespace_state(name)
                if st != 'Available':
                    self._set(f'chờ codespace ({st})')
                    self._notify(f'⚠️ Codespace đang {st} — keepalive tạm nghỉ, bot sẽ tự thử lại.',
                                 'ka-' + str(st), 3600)
                    time.sleep(60)
                    continue
                # Đảm bảo SSH key đã đăng ký trước khi mở phiên (lần đầu trên tài khoản mới)
                try:
                    ok, msg = self.gh.ensure_ssh_registered()
                    if not ok:
                        self.last_error = f'SSH key: {msg}'
                        self._notify('⚠️ Keepalive chưa đăng ký được SSH key: %s' % str(msg)[:150],
                                     'ka-key', 3600)
                except Exception as e:
                    self.last_error = f'ensure ssh key: {e}'
                interval = max(15, min(300, int(self.cfg.get('keepalive_interval') or 60)))
                remote_cmd = f'script -qec "while true; do date; sleep {interval}; done" /dev/null'
                self.log('keepalive: mo phien ssh moi...')
                try:
                    p = self.gh.spawn(['codespace', 'ssh', '-c', name, '--', remote_cmd])
                except Exception as e:
                    self.last_error = str(e)
                    self._set('lỗi mở ssh')
                    time.sleep(15)
                    continue
                with self.lock:
                    self.proc = p
                    self.state = 'running'
                    self.started_ts = time.time()
                self.log(f'keepalive: dang chay (pid {p.pid})')
                out_tail = []
                try:
                    for line in iter(p.stdout.readline, ''):
                        if self.stopped:
                            break
                        line = line.strip()
                        if line:
                            self.last_line = line
                            out_tail.append(line)
                            if len(out_tail) > 8:
                                out_tail.pop(0)
                except Exception:
                    pass
                try:
                    p.wait(timeout=10)
                except Exception:
                    pass
                elapsed = time.time() - self.started_ts
                rc = p.returncode
                self._kill_proc()
                self.log(f'keepalive: phien ssh ket thuc rc={rc} sau {int(elapsed)}s')
                if elapsed < 30 and rc not in (0, None):
                    tail = ' | '.join(out_tail[-3:])[:280]
                    self.last_error = (f'gh codespace ssh rc={rc}: {tail}' if tail
                                       else f'gh codespace ssh thất bại (rc={rc})')
                    self._set('lỗi ssh')
                    # cho phép kiểm tra lại SSH key ở vòng sau (trường hợp key mới đăng ký)
                    try:
                        self.gh._ssh_ok_ts = 0.0
                    except Exception:
                        pass
                    self._notify('⚠️ Keepalive không mở được SSH (rc=%s). %s'
                                 % (rc, tail[:150] if tail else ''), 'ka-sshfail', 3600)
                    time.sleep(30)
                else:
                    time.sleep(5)
            except Exception as e:
                self.last_error = str(e)
                if not self.stopped:
                    time.sleep(15)
