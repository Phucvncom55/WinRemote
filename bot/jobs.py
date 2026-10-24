# -*- coding: utf-8 -*-
"""Quản lý job nền (backup/restore/setup...) — mỗi lúc 1 job, có nút HỦY."""
import threading
import time

from bot.remote import RemoteError


class JobError(Exception):
    """Lỗi nghiệp vụ có thông điệp thân thiện hiển thị cho user."""


class JobCtx:
    def __init__(self, engine, name, chat_id):
        self.engine = engine
        self.tg = engine.tg
        self.name = name
        self.chat_id = chat_id
        self.message_id = None
        self.last_edit = 0.0
        self.cancel_event = threading.Event()
        self.remote_cancel = None  # callable() -> str (kết quả thông báo)
        self._sent_any = False

    def ensure(self, text, buttons=None):
        if self.message_id is None:
            msg = self.tg.send_message(self.chat_id, text, buttons=buttons)
            self.message_id = msg['message_id'] if msg else None
            self._sent_any = True
        else:
            self.edit(text, buttons=buttons, force=True)

    def edit(self, text, buttons=None, force=False):
        now = time.time()
        if not force and now - self.last_edit < 2.5:
            return
        self.last_edit = now
        if self.message_id is None:
            self.ensure(text, buttons)
            return
        self.tg.edit_message(self.chat_id, self.message_id, text, buttons=buttons)

    def send(self, text, buttons=None):
        return self.tg.send_message(self.chat_id, text, buttons=buttons)

    @property
    def cancelled(self):
        return self.cancel_event.is_set()

    def sleep(self, sec):
        """Ngủ có thể ngắt giữa chừng khi bị hủy."""
        end = time.time() + sec
        while time.time() < end:
            if self.cancelled:
                return
            time.sleep(min(0.5, end - time.time()))


class JobManager:
    def __init__(self, engine):
        self.engine = engine
        self.lock = threading.Lock()
        self.current = None  # dict(ctx=..., started=...)

    def busy(self):
        with self.lock:
            return self.current is not None

    def info(self):
        with self.lock:
            if not self.current:
                return None
            c = self.current
            return {'name': c['ctx'].name, 'chat_id': c['ctx'].chat_id,
                    'elapsed': int(time.time() - c['started'])}

    def start(self, name, chat_id, worker):
        with self.lock:
            if self.current is not None:
                return False
            ctx = JobCtx(self.engine, name, chat_id)
            self.current = {'ctx': ctx, 'started': time.time()}
        t = threading.Thread(target=self._run, args=(ctx, worker), name='job-' + name, daemon=True)
        t.start()
        return True

    def _run(self, ctx, worker):
        try:
            worker(ctx)
        except (JobError, RemoteError) as e:
            try:
                detail = getattr(e, 'detail', '') or ''
                msg = '❌ ' + str(e)
                if detail:
                    msg += '\n<code>' + detail + '</code>'
                ctx.ensure(msg)
            except Exception:
                pass
        except Exception as e:  # noqa
            self.engine.log.error('job %s loi: %r', ctx.name, e, exc_info=True)
            try:
                detail = str(e)[:300]
                ctx.ensure('❌ Lỗi không mong đợi khi %s: %s' % (ctx.name, detail))
            except Exception:
                pass
        finally:
            with self.lock:
                self.current = None

    def cancel(self, chat_id):
        with self.lock:
            cur = self.current
        if not cur:
            return 'Không có việc nào đang chạy.'
        ctx = cur['ctx']
        ctx.cancel_event.set()
        msg = 'Đã yêu cầu HỦY — chờ bước hiện tại kết thúc an toàn.'
        if ctx.remote_cancel:
            try:
                note = ctx.remote_cancel()
                if note:
                    msg += ' ' + str(note)
            except Exception as e:  # noqa
                self.engine.log.warn('remote_cancel loi: %r', e)
        return msg
