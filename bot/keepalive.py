# -*- coding: utf-8 -*-
"""Giữ Codespace không bị dừng vì idle timeout (bản v2).

Mở 1 phiên SSH dài hạn tới ĐÚNG Codespace đang chọn, in một "nhịp tim" mỗi N
giây -> GitHub tính là đang hoạt động. Bản v2 sửa các lỗi của bản cũ:

* có thread đọc log riêng nên có thể đổi/tắt keepalive NGAY cả khi SSH đang mở;
* tự phát hiện phiên SSH treo (không in log quá lâu) rồi mở lại;
* ghi lại hostname thật của máy đích để đối chiếu với Codespace đang chọn;
* log có timestamp, số nhịp tim, mã thoát và lỗi gần nhất — xem được từ Telegram.
"""
import collections
import os
import queue
import subprocess
import threading
import time

# Thời gian tối đa chờ log trước khi coi phiên SSH là treo (giây).
STALL_MIN = 180


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
        self.connected_ts = 0.0
        self.target = ''
        self.remote_host = ''
        self.command = ''
        self.restarts = 0
        self.manual_restarts = 0
        self.ended_ts = 0.0
        self.last_session_sec = 0
        self.beats = 0
        self.attempts = 0
        self.last_rc = None
        self.last_error = ''
        self.retry_at = 0.0
        self.lines = collections.deque(maxlen=500)
        self._wake = threading.Event()
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

    def read_logfile(self, max_bytes=180000):
        """Đọc file log đầy đủ (dùng cho nút tải log trong Telegram)."""
        if not self.log_path:
            return ''
        try:
            with open(self.log_path, 'r', encoding='utf-8', errors='replace') as f:
                data = f.read()
        except OSError:
            return ''
        return data[-max_bytes:]

    def log_lines(self):
        """Toàn bộ log dạng dòng — ưu tiên file để không mất log sau khi bot restart.

        Dùng cho màn hình log inline trong Telegram (không gửi file).
        """
        if self.log_path:
            try:
                with open(self.log_path, 'r', encoding='utf-8', errors='replace') as f:
                    out = f.read().splitlines()
                if out:
                    return out
            except OSError:
                pass
        with self.lock:
            return list(self.lines)

    # ---------- vòng đời ----------
    def start(self):
        self._trim_logfile()
        self._log('keepalive: bot khởi động (chờ cấu hình)')
        self.t.start()

    def stop(self):
        self.stopped = True
        self._wake.set()
        self._log('keepalive: dừng theo yêu cầu')
        self._kill_proc()

    def restart(self):
        """Buộc mở lại phiên SSH (dùng sau khi đổi Codespace / sửa lỗi)."""
        self.manual_restarts += 1
        self._log('keepalive: yêu cầu mở lại phiên SSH')
        self._kill_proc()
        self._wake.set()

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
                    'restarts': self.restarts, 'manual_restarts': self.manual_restarts,
                    'since': self.started_ts, 'ended_ts': self.ended_ts,
                    'last_session_sec': self.last_session_sec,
                    'connected_at': self.connected_ts,
                    'target': self.target, 'host': self.remote_host,
                    'command': self.command, 'beats': self.beats,
                    'attempts': self.attempts, 'last_rc': self.last_rc,
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

    # ---------- supervisor ----------
    def _loop(self):
        while not self.stopped:
            try:
                if not self.cfg.get('keepalive_enabled'):
                    if self.proc:
                        self._kill_proc()
                    self._set('off')
                    self._wake.wait(3)
                    self._wake.clear()
                    continue
                name = self.cfg.get('codespace') or ''
                if not (name and self.cfg.has_github):
                    self._set('chưa cấu hình')
                    self._wake.wait(10)
                    self._wake.clear()
                    continue
                st = self.gh.codespace_state(name)
                if st != 'Available':
                    if self.proc:
                        self._kill_proc()
                    self._set(f'chờ codespace ({st})')
                    self._notify(f'⚠️ Codespace đang {st} — keepalive tạm nghỉ, bot sẽ tự thử lại.',
                                 'ka-' + str(st), 3600)
                    self._wake.wait(30)
                    self._wake.clear()
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
                self._run_session(name, interval)
            except Exception as e:
                self.last_error = str(e)
                self._record('keepalive: exception ' + str(e)[:200])
                if not self.stopped:
                    self._wake.wait(15)
                    self._wake.clear()

    def _run_session(self, name, interval):
        remote_cmd = (
            'echo KA_HOST="$(hostname)"; '
            'echo "KA_CODE=$PWD"; '
            'while true; do echo "KA_BEAT $(date -u +%Y-%m-%dT%H:%M:%SZ)"; '
            'sleep ' + str(interval) + '; done')
        self._log(f'keepalive: mở SSH -> {name} (nhịp {interval}s)')
        self.attempts += 1
        try:
            p = self.gh.spawn(['codespace', 'ssh', '-c', name, '--', remote_cmd])
        except Exception as e:
            self.last_error = str(e)
            self._set('lỗi mở ssh')
            self._log(f'keepalive: khong mo duoc ssh -> {name}: {e}')
            self._backoff(15)
            return

        now = time.time()
        with self.lock:
            self.proc = p
            self.state = 'đang kết nối…'
            self.started_ts = now
            self.connected_ts = 0.0
            self.ended_ts = 0.0
            self.last_session_sec = 0
            self.target = name
            self.remote_host = ''
            self.beats = 0
            self.last_rc = None
            self.command = f'gh codespace ssh -c {name} -- {remote_cmd[:120]}'
        self.restarts = max(0, self.attempts - 1)
        self._log(f'keepalive: SSH pid {p.pid} -> {name}')

        q = queue.Queue()
        reader = threading.Thread(target=self._pump, args=(p, q), daemon=True)
        reader.start()

        got_output = False
        last_alive_check = 0.0
        exit_reason = ''
        while True:
            if self.stopped:
                exit_reason = 'bot dừng'
                break
            if not self.cfg.get('keepalive_enabled'):
                exit_reason = 'đã tắt keepalive'
                break
            if (self.cfg.get('codespace') or '') != name:
                exit_reason = 'đổi Codespace'
                break
            try:
                line = q.get(timeout=1.0)
            except queue.Empty:
                line = None
            if line is None and not reader.is_alive():
                exit_reason = exit_reason or 'SSH kết thúc'
                break
            if line:
                got_output = True
                self._handle_line(line, name)
            now = time.time()
            if got_output:
                self._set('running')
            # phát hiện treo: không có log quá lâu
            limit = max(STALL_MIN, interval * 3 + 60)
            if self.connected_ts and now - self.last_output_ts > limit:
                exit_reason = f'không có log > {int(limit)}s (treo)'
                self.last_error = 'Phiên SSH không in log — sẽ mở lại.'
                self._log('keepalive: ' + self.last_error)
                self._kill_proc()
                break
            if now - last_alive_check > 5:
                last_alive_check = now
                if p.poll() is not None:
                    exit_reason = 'SSH kết thúc'
                    break

        rc = None
        try:
            if p.poll() is None:
                try:
                    p.terminate()
                except Exception:
                    pass
            try:
                p.wait(timeout=5)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
            rc = p.returncode
        except Exception:
            rc = None
        elapsed = time.time() - self.connected_ts if self.connected_ts else 0
        with self.lock:
            if self.proc is p:
                self.proc = None
            self.last_rc = rc
            self.ended_ts = time.time()
            self.last_session_sec = int(elapsed) if elapsed else 0
        msg = f'keepalive: phiên SSH kết thúc ({exit_reason}, rc={rc}) sau {int(elapsed)}s'
        self._log(msg)

        # Thoát do người dùng đổi cấu hình: không coi là lỗi, mở phiên mới ngay.
        if exit_reason in ('đã tắt keepalive', 'đổi Codespace', 'bot dừng'):
            self.last_error = ''
            self._set('off' if exit_reason == 'đã tắt keepalive'
                      else ('đang chuyển Codespace' if exit_reason == 'đổi Codespace'
                            else 'off'))
            if exit_reason != 'bot dừng':
                self._wake.wait(2)
                self._wake.clear()
            return

        # SSH lỗi ngay (<30s) => log lỗi + thử đăng ký lại key ở vòng sau
        if self.connected_ts == 0 or elapsed < 30:
            tail = ' | '.join([str(x) for x in list(self.lines)[-3:]])[:280]
            self.last_error = (f'gh codespace ssh rc={rc}: {tail}' if tail
                               else f'gh codespace ssh thất bại (rc={rc})')
            self._set('lỗi ssh')
            try:
                self.gh._ssh_ok_ts = 0.0
            except Exception:
                pass
            self._notify('⚠️ Keepalive không mở được SSH (rc=%s). %s'
                         % (rc, tail[:150] if tail else ''), 'ka-sshfail', 3600)
            self._backoff(30)
        else:
            self._backoff(5)

    def _pump(self, p, q):
        try:
            for line in iter(p.stdout.readline, ''):
                q.put(line.rstrip('\r\n'))
        except Exception:
            pass
        finally:
            q.put(None)

    def _handle_line(self, line, name):
        line = str(line or '').strip()
        if not line:
            return
        now = time.time()
        with self.lock:
            self.last_line = line
            self.last_output_ts = now
            if line.startswith('KA_HOST='):
                self.remote_host = line.split('=', 1)[1].strip()
                self.connected_ts = now
                self.state = 'running'
            elif line.startswith('KA_BEAT'):
                self.beats += 1
                self.connected_ts = self.connected_ts or now
                self.state = 'running'
        self._record('ssh> ' + line)

    def _backoff(self, seconds):
        self.retry_at = time.time() + seconds
        self._wake.wait(seconds)
        self._wake.clear()


class FakeKeepalive:
    """Bản giả cho test: không mở SSH, chỉ trả snapshot cố định."""

    def __init__(self, snap=None):
        self.snap = snap or {}

    def snapshot(self):
        return dict(self.snap)

    def clear_log(self):
        return None

    def restart(self):
        return None

    def read_logfile(self, max_bytes=180000):
        return ''

    def log_lines(self):
        return list(self.snap.get('lines') or [])
