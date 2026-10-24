# -*- coding: utf-8 -*-
"""Giữ Codespace không bị dừng vì idle timeout (tái hiện keepalive.ps1).

Mở 1 phiên SSH dài hạn, in 'date' mỗi N giây -> GitHub tính là hoạt động.
Lưu ý: KHÔNG vượt qua giới hạn cứng của GitHub (nếu có); xem README.
"""
import collections
import os
import subprocess
import threading
import time


class Keepalive:
    def __init__(self, gh, cfg, log, notify=None, log_path=None):
        self.gh = gh
        self.cfg = cfg
        # log có thể là logging.Logger (dùng .info) hoặc hàm nhận 1 chuỗi
        self.log = getattr(log, 'info', log)
        self.notify = notify
        self.log_path = log_path or ''
        self.lock = threading.Lock()
        self.stopped = False
        self.proc = None
        self.state = 'off'
        self.last_line = ''
        self.last_output_ts = 0.0
        self.started_ts = 0.0
        self.target = ''
        self.command = ''
        self.restarts = 0
        self.last_error = ''
        self.lines = collections.deque(maxlen=200)
        self.t = threading.Thread(target=self._loop, name='keepalive', daemon=True)

    # ---------- log ----------
    def _record(self, text):
        line = time.strftime('%H:%M:%S') + ' ' + str(text)
        with self.lock:
            self.lines.append(line)
        if not self.log_path:
            return
        try:
            d = os.path.dirname(self.log_path)
            if d:
                os.makedirs(d, exist_ok=True)
            with open(self.log_path, 'a', encoding='utf-8') as f:
                f.write(time.strftime('%Y-%m-%d ') + line + '\n')
        except Exception:
            pass

    def _log(self, text):
        try:
            self.log(text)
        except Exception:
            pass
        self._record(text)

    def _trim_logfile(self):
        if not self.log_path:
            return
        try:
            if os.path.getsize(self.log_path) > 512 * 1024:
                os.replace(self.log_path, self.log_path + '.old')
        except OSError:
            pass

    def clear_log(self):
        with self.lock:
            self.lines.clear()
        if self.log_path:
            try:
                open(self.log_path, 'w', encoding='utf-8').close()
            except OSError:
                pass
        self._record('keepalive: đã xóa log theo yêu cầu')


    def start(self):
        self._trim_logfile()
        self._log('keepalive: bot khởi động (chờ cấu hình)')
        self.t.start()

    def stop(self):
        self.stopped = True
        self._log('keepalive: dừng theo yêu cầu')
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
                    'target': self.target, 'command': self.command,
                    'pid': (self.proc.pid if self.proc else 0),
                    'last_output_ts': self.last_output_ts,
                    'log_path': self.log_path,
                    'lines': list(self.lines),
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
                        self._log('keepalive: loi SSH key: ' + str(msg)[:200])
                        self._notify('⚠️ Keepalive chưa đăng ký được SSH key: %s' % str(msg)[:150],
                                     'ka-key', 3600)
                except Exception as e:
                    self.last_error = f'ensure ssh key: {e}'
                    self._log('keepalive: ensure ssh key loi: ' + str(e)[:200])
                interval = max(15, min(300, int(self.cfg.get('keepalive_interval') or 60)))
                remote_cmd = f'script -qec "while true; do date; touch /workspaces/.ka 2>/dev/null; sleep {interval}; done" /dev/null'
                self.log(f'keepalive: mo phien ssh moi -> {name}')
                try:
                    p = self.gh.spawn(['codespace', 'ssh', '-c', name, '--', remote_cmd])
                except Exception as e:
                    self.last_error = str(e)
                    self._set('lỗi mở ssh')
                    self._log(f'keepalive: khong mo duoc ssh -> {name}: {e}')
                    time.sleep(15)
                    continue
                with self.lock:
                    self.proc = p
                    self.state = 'running'
                    self.started_ts = time.time()
                    self.target = name
                    self.command = f'gh codespace ssh -c {name} -- {remote_cmd}'
                self._log(f'keepalive: SSH pid {p.pid} -> {name}')
                out_tail = []
                try:
                    for line in iter(p.stdout.readline, ''):
                        if self.stopped:
                            break
                        line = line.strip()
                        if line:
                            self.last_line = line
                            self.last_output_ts = time.time()
                            out_tail.append(line)
                            self._record('ssh> ' + line)
                            if len(out_tail) > 8:
                                out_tail.pop(0)
                except Exception as e:
                    self._log('keepalive: doc stdout loi: ' + str(e)[:150])
                try:
                    p.wait(timeout=10)
                except Exception:
                    pass
                elapsed = time.time() - self.started_ts
                rc = p.returncode
                self._kill_proc()
                self.log(f'keepalive: phien ssh ket thuc rc={rc} sau {int(elapsed)}s')
                self._record(f'keepalive: phien ssh ket thuc rc={rc} sau {int(elapsed)}s')
                if elapsed < 30 and rc not in (0, None):
                    tail = ' | '.join(out_tail[-3:])[:280]
                    self.last_error = (f'gh codespace ssh rc={rc}: {tail}' if tail
                                       else f'gh codespace ssh thất bại (rc={rc})')
                    self._set('lỗi ssh')
                    self._record('keepalive: LOI ' + self.last_error)
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
                self._record('keepalive: exception ' + str(e)[:200])
                if not self.stopped:
                    time.sleep(15)
