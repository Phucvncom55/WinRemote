# -*- coding: utf-8 -*-
"""Engine: định tuyến lệnh Telegram + các job setup/backup/restore/VM/Actions."""
import base64
import json
import os
import re
import time

from bot import ui
from bot.tg import kb
from bot.jobs import JobError, JobManager
from bot.remote import CONF_PATH, WD_DIR
from bot.util import esc, fmt_bytes, fmt_elapsed, fmt_age, vn_time

PROBE_BACKUP = (
    'echo MARK_P; cat /tmp/vm_backup_progress 2>/dev/null || true; '
    'echo MARK_B; cat /tmp/vm_backup_busy 2>/dev/null || true; '
    'echo MARK_L; tail -3 /tmp/vm_backup_run.log 2>/dev/null || true; '
    'echo MARK_U; tail -1 /tmp/vm_upload.log 2>/dev/null || true; '
    'echo MARK_A; stat -c %s /mnt/data/backup/windows-backup.tar.zst 2>/dev/null || echo 0; '
    'echo MARK_C; ls /mnt/data/backup/parts 2>/dev/null | wc -l; '
    'echo MARK_R; grep -m1 "^RESULT=" /tmp/vm_backup_run.log 2>/dev/null || true; '
    'echo MARK_AL; pgrep -f "backup_on[c]e\\.sh" 2>/dev/null | wc -l; '
    'echo MARK_END'
)
BACKUP_MARKERS = ['MARK_P', 'MARK_B', 'MARK_L', 'MARK_U', 'MARK_A', 'MARK_C',
                  'MARK_R', 'MARK_AL', 'MARK_END']

RX_FOLDER = re.compile(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+-?\d+\s+(data_backup\S*)\s*$')
RX_STAGE = re.compile(r'^([a-z_]+)\|(\d+)\|(\d+)\|(\d+)$')
RX_SPD = re.compile(r'([\d.]+)\s*(Gi|Mi|Ki|B)B?/s')
RX_ETA = re.compile(r'ETA\s+(\S+)')
RX_UP_PCT = re.compile(r',\s*(\d+)%')


def marked_blocks(text, markers):
    out = {m: [] for m in markers}
    cur = None
    for ln in str(text or '').splitlines():
        s = ln.strip()
        if s in out:
            cur = s
            continue
        if cur is not None:
            out[cur].append(ln.rstrip())
    return out


def _first(blocks, key):
    v = blocks.get(key) or []
    return v[0].strip() if v else ''


class Engine:
    def __init__(self, cfg, tg, gh, remote, ka, log):
        self.cfg = cfg
        self.tg = tg
        self.gh = gh
        self.remote = remote
        self.ka = ka
        self.log = log
        self.jobs = JobManager(self)
        self.poll_sec = float(os.environ.get('WM_POLL_SEC', '15') or 15)
        self.pending = None
        self._rst_choices = []
        self._cs_choices = []
        self._repo_choices = []
        self._probe_cache = (0.0, None)
        self._act_cache = (0.0, None)
        self._quota_cache = (0.0, None)
        self._bk_opts = {'keep_local': False, 'restart': True}
        self._notify_gate = {}

    # ================= tiện ích chung =================
    def _owner(self):
        try:
            return int(self.cfg.get('owner_chat_id') or 0)
        except (TypeError, ValueError):
            return 0

    def _is_owner(self, chat_id):
        return self._owner() != 0 and int(chat_id) == self._owner()

    def _try_delete(self, chat_id, mid):
        if mid:
            try:
                self.tg.delete_message(chat_id, mid)
            except Exception:
                pass

    def notify_owner(self, text, key=None, min_interval=3600):
        owner = self._owner()
        if not owner:
            return
        if key:
            now = time.time()
            if now - self._notify_gate.get(key, 0) < min_interval:
                return
            self._notify_gate[key] = now
        try:
            self.tg.send_message(owner, text)
        except Exception:
            pass

    def get_probe(self, max_age=8.0):
        ts, probe = self._probe_cache
        if probe is not None and (time.time() - ts) < max_age:
            return probe
        probe = self.remote.probe()
        self._probe_cache = (time.time(), probe)
        return probe

    # ================= tiếp nhận update =================
    def handle_update(self, upd):
        try:
            if 'message' in upd:
                self._handle_message(upd['message'])
            elif 'callback_query' in upd:
                self._handle_callback(upd['callback_query'])
        except Exception as e:  # noqa
            self.log.error('handle_update loi: %r', e, exc_info=True)

    def _handle_message(self, m):
        chat_id = m['chat']['id']
        mid = m.get('message_id')
        text = (m.get('text') or '').strip()
        if not text:
            return
        if self.pending and self.pending.get('chat_id') == chat_id and self._is_owner(chat_id):
            kind = self.pending['kind']
            self.pending = None
            self._consume_input(chat_id, kind, text, mid)
            return
        if text.startswith('/'):
            head = text.split()[0][1:]
            cmd = head.split('@')[0].lower()
            args = text[len(text.split()[0]):].strip()
            self._dispatch(chat_id, cmd, args)
        elif self._is_owner(chat_id):
            self.tg.send_message(chat_id, 'Gõ /help để xem danh sách lệnh nhé. 🙂')

    def _dispatch(self, chat_id, cmd, args):
        owner = self._owner()
        if owner == 0:
            if cmd == 'start':
                self._claim(chat_id)
            else:
                self.tg.send_message(chat_id, '👋 Gõ /start để nhận quyền sở hữu bot.')
            return
        if not self._is_owner(chat_id):
            if cmd == 'start':
                self.tg.send_message(chat_id, '🔒 Bot riêng tư — chỉ chủ sở hữu sử dụng được.')
            return
        fn = getattr(self, 'cmd_' + cmd, None)
        if fn is None:
            self.tg.send_message(chat_id, 'Lệnh không rõ. Gõ /help.')
            return
        fn(chat_id, args)

    def _claim(self, chat_id):
        self.cfg.set('owner_chat_id', int(chat_id))
        self.log.info('owner chat = %s', chat_id)
        self.tg.send_message(chat_id, ui.h_start(self.cfg, claimed=True))
        self.show_status(chat_id)

    # ================= lệnh =================
    def cmd_start(self, chat_id, args):
        self.tg.send_message(chat_id, ui.h_start(self.cfg))
        self.show_status(chat_id)

    def cmd_help(self, chat_id, args):
        self.tg.send_message(chat_id, ui.h_help(),
                             buttons=kb(
                                 [[('🏠 Trang chính', 'home')]]))

    def cmd_status(self, chat_id, args):
        self.show_status(chat_id)

    def cmd_quota(self, chat_id, args):
        a = (args or '').strip().lower()
        if a.startswith('set'):
            if self._set_quota_manual(chat_id, args.strip()[3:].strip()):
                self.show_quota(chat_id, force=True)
            return
        if a in ('reset', 'clear', 'xoa', 'xóa'):
            self.cfg.data['quota_used_core_hours'] = None
            self.cfg.data['quota_used_storage_gb_month'] = None
            self.cfg.save()
            self._quota_cache = (0.0, None)
            self.tg.send_message(chat_id, '✅ Đã xóa số liệu quota nhập tay.')
        self.show_quota(chat_id, force=True)

    def _set_quota_manual(self, chat_id, text):
        parts = [x for x in text.replace(',', '.').split() if x]
        if not parts:
            self.tg.send_message(chat_id, '📊 Cú pháp: <code>/quota set &lt;giờ nhân lõi&gt; '
                                          '[GB-tháng]</code>\n'
                                          'Ví dụ: <code>/quota set 6.17 0.15</code>')
            return False
        try:
            core = float(parts[0])
            st = float(parts[1]) if len(parts) > 1 else None
        except ValueError:
            self.tg.send_message(chat_id, '❌ Không phải số. Ví dụ: <code>/quota set 6.17 0.15</code>')
            return False
        if core < 0 or (st is not None and st < 0):
            self.tg.send_message(chat_id, '❌ Giá trị phải >= 0.')
            return False
        self.cfg.data['quota_used_core_hours'] = core
        self.cfg.data['quota_used_storage_gb_month'] = st
        self.cfg.save()
        self._quota_cache = (0.0, None)
        self.tg.send_message(chat_id, '✅ Đã lưu số liệu quota nhập tay '
                                      f'({core:.2f} giờ nhân lõi' +
                                      (f', {st:.2f} GB-tháng).' if st is not None else ').'))
        return True

    def show_quota(self, chat_id, mid=None, force=False):
        try:
            self.tg.send_chat_action(chat_id)
            q = self.get_quota(force=force)
            text = ui.quota_text(q)
            k = ui.quota_kb(q)
            if mid:
                self.tg.edit_message(chat_id, mid, text, buttons=k)
            else:
                self.tg.send_message(chat_id, text, buttons=k)
        except Exception as e:  # noqa
            self.log.error('quota loi: %r', e, exc_info=True)
            self.tg.send_message(chat_id, '❌ Không tính được quota: ' + esc(str(e)[:250]))

    @staticmethod
    def _parse_codespace_usage(summary):
        """Tách giờ nhân lõi + GB-tháng đã dùng từ billing usage summary."""
        core = None
        storage = None
        for it in (summary or {}).get('usageItems') or []:
            if not isinstance(it, dict):
                continue
            prod = str(it.get('product') or '').lower()
            sku = str(it.get('sku') or '').lower()
            unit = str(it.get('unitType') or '').lower()
            if 'codespace' not in prod and 'codespace' not in sku:
                continue
            try:
                qty = float(it.get('grossQuantity') or 0)
            except (TypeError, ValueError):
                qty = 0.0
            if qty <= 0:
                try:
                    qty = float(it.get('netQuantity') or 0)
                except (TypeError, ValueError):
                    qty = 0.0
            is_storage = ('storage' in sku or 'storage' in unit
                          or 'gb-month' in unit or 'gb month' in unit
                          or 'gb_month' in unit)
            if is_storage:
                if 'hour' in unit:
                    qty = qty / 730.0
                storage = (storage or 0.0) + qty
                continue
            is_compute = ('compute' in sku or 'core' in unit or 'minute' in unit
                          or unit in ('hour', 'hours', 'minute', 'minutes'))
            if is_compute:
                if 'minute' in unit:
                    qty = qty / 60.0
                elif 'second' in unit:
                    qty = qty / 3600.0
                core = (core or 0.0) + qty
        return core, storage

    def get_quota(self, force=False):
        now = time.time()
        ttl = max(30, self._int_safe(self.cfg.get('quota_cache_sec'), 300))
        if not force and self._quota_cache[1] and now - self._quota_cache[0] < ttl:
            return self._quota_cache[1]
        login = self.cfg.get('github_login') or ''
        if not login and self.cfg.has_github:
            login = self.gh.api_user()
            if login:
                self.cfg.set('github_login', login)
        q = {
            'login': login,
            'plan': 'Free',
            'cores': 0, 'ram_gb': 0, 'disk_gb': 0, 'machine': '',
            'included_core_hours': float(self.cfg.get('quota_core_hours') or 120),
            'included_storage_gb_month': float(self.cfg.get('quota_storage_gb_month') or 15),
            'used_core_hours': None, 'used_storage_gb_month': None,
            'run_hours_left': None, 'storage_days_left': None,
            'source': 'none', 'error': '', 'updated': vn_time(fmt='%H:%M %d/%m/%Y'),
        }
        if not self.cfg.has_github:
            q['error'] = 'Chưa có GitHub token — mở /settings.'
            return q
        cs = self.cfg.get('codespace') or ''
        detail = None
        if cs:
            try:
                detail = self.gh.codespace_detail(cs) or {}
            except Exception as e:  # noqa
                detail = {'_error': str(e)}
        machine = (detail or {}).get('machine') or {}
        cores = self._int_safe(machine.get('cpus'))
        ram = self._int_safe(machine.get('memory_in_bytes')) // (1024 ** 3)
        disk = self._int_safe(machine.get('storage_in_bytes')) // (1024 ** 3)
        if not cores:
            cores = self._int_safe(self.cfg.vm().get('cores'), 4)
        if not ram:
            ram = self._int_safe(self.cfg.vm().get('ram_gb'), 16)
        if not disk:
            disk = self._int_safe(self.cfg.vm().get('disk_gb'), 32)
        q.update({'cores': cores, 'ram_gb': ram, 'disk_gb': disk,
                  'machine': str(machine.get('name') or machine.get('display_name') or cs)})

        summary = self.gh.billing_usage_summary(login) if login else {}
        if summary and not summary.get('_error'):
            q['source'] = 'api'
            used_core, used_st = self._parse_codespace_usage(summary)
            if used_core is not None:
                q['used_core_hours'] = used_core
            if used_st is not None:
                q['used_storage_gb_month'] = used_st
        else:
            q['error'] = (summary or {}).get('_error', '') or 'Không gọi được billing API.'
            # dùng số nhập tay nếu có
            mc = self.cfg.get('quota_used_core_hours')
            ms = self.cfg.get('quota_used_storage_gb_month')
            if mc is not None:
                q['used_core_hours'] = float(mc)
                q['source'] = 'manual'
            if ms is not None:
                q['used_storage_gb_month'] = float(ms)
                q['source'] = 'manual'

        if q['used_core_hours'] is None and q['source'] == 'none':
            # chưa có API lẫn số nhập tay: không bịa số đã dùng
            q['run_hours_left'] = None
        elif q['used_core_hours'] is not None:
            left = max(0.0, q['included_core_hours'] - q['used_core_hours'])
            q['run_hours_left'] = left / cores if cores else None
        if q['used_storage_gb_month'] is not None and disk:
            left_st = max(0.0, q['included_storage_gb_month'] - q['used_storage_gb_month'])
            q['storage_days_left'] = left_st / disk * 30.0
        self._quota_cache = (now, q)
        return q

    def cmd_settings(self, chat_id, args):
        self.show_settings(chat_id)

    def cmd_repo(self, chat_id, args):
        self.show_repo(chat_id)

    def cmd_setup(self, chat_id, args):
        self.start_job('Cài môi trường (setup)', chat_id, self.worker_setup)

    def cmd_backup(self, chat_id, args):
        self.show_backup_confirm(chat_id)

    def cmd_restore(self, chat_id, args):
        self.show_restore_list(chat_id)

    def cmd_vm(self, chat_id, args):
        self.show_vm(chat_id)

    def cmd_actions(self, chat_id, args):
        self.show_actions(chat_id)

    def cmd_ports(self, chat_id, args):
        self.start_job('Mở cổng public', chat_id, self.worker_ports)

    def cmd_cleanup(self, chat_id, args):
        self.show_cleanup(chat_id)

    def cmd_whoami(self, chat_id, args):
        text, k = ui.account_text(self.cfg, self.ka.snapshot() if self.ka else None)
        self.tg.send_message(chat_id, text, buttons=k)

    def cmd_kalog(self, chat_id, args):
        self.show_keepalive(chat_id)

    def show_keepalive(self, chat_id, mid=None):
        snap = self.ka.snapshot() if self.ka else {}
        text = ui.keepalive_text(self.cfg, snap)
        k = ui.keepalive_kb(self.cfg)
        if mid:
            self.tg.edit_message(chat_id, mid, text, buttons=k)
        else:
            self.tg.send_message(chat_id, text, buttons=k)

    def cmd_onedrive(self, chat_id, args):
        if args.strip():
            self._apply_od_token(chat_id, args.strip(), None)
        else:
            self.tg.send_message(
                chat_id, '☁️ Gửi token theo cú pháp:\n<code>/onedrive {"access_token":"…",'
                         '"refresh_token":"…"}</code>\n(tạo bằng <code>rclone authorize onedrive</code> '
                         'trên máy có trình duyệt)')

    def cmd_ssh(self, chat_id, args):
        if not args.strip():
            self.tg.send_message(chat_id, '📡 Cú pháp: <code>/ssh &lt;lệnh bash&gt;</code>\n'
                                          'Ví dụ: <code>/ssh df -h /mnt/data</code>')
            return
        self.start_job('SSH: ' + args.strip()[:40], chat_id,
                       lambda ctx: self.worker_ssh(ctx, args.strip()))

    def cmd_cancel(self, chat_id, args):
        if self.pending and self.pending.get('chat_id') == chat_id:
            self.pending = None
            self.tg.send_message(chat_id, '✖️ Đã hủy thao tác nhập liệu.')
            return
        msg = self.jobs.cancel(chat_id)
        self.tg.send_message(chat_id, esc(msg))

    # ================= màn hình trạng thái =================
    def show_status(self, chat_id, mid=None):
        try:
            self.tg.send_chat_action(chat_id)
            cs_state = self.remote.state() if self.cfg.has_codespace else 'NoConfig'
            probe = None
            if cs_state == 'Available':
                try:
                    probe = self.get_probe(max_age=4.0)
                except Exception as e:  # noqa
                    self.log.warn('probe loi: %r', e)
            act = self.get_actions_snapshot()
            text = ui.status_text(self.cfg, cs_state, probe,
                                  self.ka.snapshot() if self.ka else None, self.jobs.info(), act)
            k = ui.status_kb(self.cfg, cs_state, probe, act)
            if mid:
                self.tg.edit_message(chat_id, mid, text, buttons=k)
            else:
                self.tg.send_message(chat_id, text, buttons=k)
        except Exception as e:
            self.log.error('show_status loi: %r', e, exc_info=True)
            if mid:
                self.tg.edit_message(chat_id, mid, '❌ Không đọc được trạng thái: ' + esc(str(e)[:200]))
            else:
                self.tg.send_message(chat_id, '❌ Không đọc được trạng thái: ' + esc(str(e)[:200]))

    # ================= màn hình cài đặt =================
    def show_settings(self, chat_id, mid=None):
        text = ui.settings_text(self.cfg)
        k = ui.settings_kb(self.cfg)
        if mid:
            self.tg.edit_message(chat_id, mid, text, buttons=k)
        else:
            self.tg.send_message(chat_id, text, buttons=k)

    def show_repo(self, chat_id, mid=None):
        text = ui.repo_text(self.cfg)
        k = ui.repo_kb(self.cfg)
        if mid:
            self.tg.edit_message(chat_id, mid, text, buttons=k)
        else:
            self.tg.send_message(chat_id, text, buttons=k)

    def show_repo_list(self, chat_id, mid=None):
        if not self.cfg.has_github:
            self.tg.send_message(chat_id, '❌ Cần GitHub token trước (/settings).')
            return
        self.tg.edit_message(chat_id, mid, '🔍 Đang lấy danh sách repo…')
        repos = self.gh.repo_list(30)
        self._repo_choices = repos
        if not repos:
            self.tg.edit_message(
                chat_id, mid,
                '❌ Không lấy được repo nào.\n'
                '• Kiểm tra GitHub token còn hạn và có scope <code>repo</code>.\n'
                '• Hoặc dùng "🆕 Tạo repo mới" / "✏️ Nhập owner/repo".',
                buttons=ui.repo_kb(self.cfg))
            return
        self.tg.edit_message(chat_id, mid, ui.repo_list_text(repos, self.cfg),
                             buttons=ui.repo_list_kb(repos))

    def _repo_full(self, text):
        """Chuẩn hóa owner/repo hoặc tên repo ngắn (thêm login)."""
        repo = (text or '').strip().strip('`').strip()
        repo = re.sub(r'^https?://github\.com/', '', repo).strip('/')
        if repo.endswith('.git'):
            repo = repo[:-4]
        if not repo:
            return ''
        if '/' not in repo:
            login = self.cfg.get('github_login') or self.gh.api_user() or ''
            if not login:
                return ''
            repo = f'{login}/{repo}'
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
            return ''
        return repo

    def _apply_repo(self, chat_id, text, mid, create=False):
        repo = self._repo_full(text)
        if not repo:
            self.tg.send_message(chat_id, '❌ Repo không hợp lệ. Gửi dạng <code>owner/repo</code> '
                                          'hoặc tên repo (bot tự thêm tài khoản).')
            return
        if create and not self.gh.repo_exists(repo):
            self.tg.send_message(chat_id, f'📦 Đang tạo repo private <code>{esc(repo)}</code>…')
            r = self.gh.repo_create(repo, private=True)
            if not r.ok:
                self.tg.send_message(chat_id, '❌ Không tạo được repo: '
                                     + esc((r.err or r.out or 'không rõ')[-300:]))
                return
        self.cfg.set('cs_repo', repo)
        self._try_delete(chat_id, mid)
        self.tg.send_message(chat_id, f'✅ Đã đặt repo: <code>{esc(repo)}</code>\n'
                                      'Dùng repo này cho Codespace và VM Actions.')
        self.show_repo(chat_id)

    def show_vm(self, chat_id, mid=None):
        probe = None
        try:
            if self.cfg.has_codespace and self.remote.state() == 'Available':
                probe = self.get_probe(max_age=4.0)
        except Exception:
            pass
        web_url = ''
        if probe and str(probe.get('web') or '') in ('200', '302', '401', '403'):
            web_url = ui.cs_port_url(self.cfg.get('codespace'), 8006)
        text = ui.vm_text(self.cfg, probe, web_url)
        k = ui.vm_kb(web_url)
        if mid:
            self.tg.edit_message(chat_id, mid, text, buttons=k)
        else:
            self.tg.send_message(chat_id, text, buttons=k)

    def show_actions(self, chat_id, mid=None):
        act = self.get_actions_snapshot(max_age=20.0)
        text = ui.actions_text(self.cfg, act)
        k = ui.actions_kb()
        if mid:
            self.tg.edit_message(chat_id, mid, text, buttons=k)
        else:
            self.tg.send_message(chat_id, text, buttons=k)

    def get_actions_snapshot(self, max_age=60.0):
        """Run Actions gần nhất (có cache ngắn) — dùng cho /status và /actions."""
        if not self.cfg.has_github:
            return None
        repo = (self.cfg.get('cs_repo') or '').strip()
        login = self.cfg.get('github_login') or ''
        if not repo or repo.lower() in ('auto', 'tu dong', 'tự động', 'tu-dong', 'tudong', '-'):
            if not login:
                return None
            repo = f'{login}/winvm-codespace'
        elif '/' not in repo and login:
            repo = f'{login}/{repo}'
        now = time.time()
        if self._act_cache and now - self._act_cache[0] < max_age:
            return self._act_cache[1]
        snap = None
        try:
            runs = self.gh.run_list(repo, 'winvm-actions.yml', 1)
            if runs:
                run = runs[0]
                snap = {'id': str(run.get('databaseId') or ''),
                        'status': str(run.get('status') or ''),
                        'conclusion': str(run.get('conclusion') or ''),
                        'created': str(run.get('createdAt') or ''),
                        'url': str(run.get('url') or ''), 'ip': ''}
                if snap['status'] in ('queued', 'requested', 'waiting', 'pending', 'in_progress'):
                    ip_txt = ''
                    try:
                        ip_txt = self.gh.repo_get_file(repo, 'winvm-actions.ip')
                    except Exception:
                        ip_txt = ''
                    m = re.search(r'TS_IP=(100\.\d{1,3}\.\d{1,3}\.\d{1,3})', ip_txt or '')
                    mrun = re.search(r'run=(\d+)', ip_txt or '')
                    if m and mrun and mrun.group(1) == snap['id']:
                        snap['ip'] = m.group(1)
        except Exception as e:  # noqa
            self.log.warn('actions snapshot loi: %r', e)
            snap = None
        self._act_cache = (now, snap)
        return snap

    def show_backup_confirm(self, chat_id, mid=None):
        probe = None
        try:
            if self.cfg.has_codespace and self.remote.state() == 'Available':
                probe = self.get_probe(max_age=4.0)
        except Exception:
            pass
        text = ui.backup_confirm_text(self.cfg, self._bk_opts['keep_local'],
                                      self._bk_opts['restart'], probe)
        k = ui.backup_confirm_kb(self._bk_opts['keep_local'], self._bk_opts['restart'])
        if mid:
            self.tg.edit_message(chat_id, mid, text, buttons=k)
        else:
            self.tg.send_message(chat_id, text, buttons=k)

    def show_cleanup(self, chat_id, mid=None):
        probe = None
        try:
            if self.cfg.has_codespace and self.remote.state() == 'Available':
                probe = self.get_probe(max_age=4.0)
        except Exception:
            pass
        text = ui.cleanup_confirm_text(self.cfg, probe)
        k = ui.cleanup_kb()
        if mid:
            self.tg.edit_message(chat_id, mid, text, buttons=k)
        else:
            self.tg.send_message(chat_id, text, buttons=k)

    def show_restore_list(self, chat_id, mid=None):
        try:
            self.tg.send_chat_action(chat_id)
            if not self.cfg.has_codespace or not self.cfg.has_onedrive:
                rows = []
                if self.cfg.has_github and not self.cfg.has_codespace:
                    rows.append([('🆕 Tạo Codespace mới', 'set:csnew')])
                rows.append([('⚙️ Cài đặt', 'set:menu')])
                self.tg.send_message(chat_id, '⛔ Cần cấu hình Codespace + OneDrive trước (/settings).',
                                     buttons=kb(rows))
                return
            st = self.remote.state()
            if st != 'Available':
                text0 = ui.status_text(self.cfg, st, None, None, None)
                k0 = kb([[('▶️ Bật Codespace', 'st:csstart'), ('🏠 Trang chính', 'home')]])
                if mid:
                    self.tg.edit_message(chat_id, mid, text0, buttons=k0)
                else:
                    self.tg.send_message(chat_id, text0, buttons=k0)
                return
            folders = self._scan_backups()
            self._rst_choices = folders
            text = ui.restore_list_text(folders)
            k = ui.restore_list_kb(folders)
            if mid:
                self.tg.edit_message(chat_id, mid, text, buttons=k)
            else:
                self.tg.send_message(chat_id, text, buttons=k)
        except JobError as e:
            if mid:
                self.tg.edit_message(chat_id, mid, '❌ ' + esc(str(e)))
            else:
                self.tg.send_message(chat_id, '❌ ' + esc(str(e)))
        except Exception as e:
            self.log.error('restore scan loi: %r', e, exc_info=True)
            self.tg.send_message(chat_id, '❌ Lỗi quét OneDrive: ' + esc(str(e)[:250]))

    def _scan_backups(self):
        out = ''
        for attempt in (1, 2):
            try:
                out = self.remote.run_text(
                    f"rclone lsd --config {CONF_PATH} onedrive: 2>&1", timeout=120,
                    allow_fail=True)
            except Exception:
                out = ''
            found = []
            for line in out.splitlines():
                m = RX_FOLDER.search(line)
                if m:
                    found.append(m.group(2))
            if found:
                return sorted(set(found), reverse=True)
            if attempt == 1:
                time.sleep(2)
        raise JobError('Không thấy thư mục data_backup* nào trên OneDrive '
                       '(kiểm tra token Onedrive / rclone conf).')

    # ================= callback =================
    def _handle_callback(self, cb):
        data = cb.get('data') or ''
        qid = cb.get('id') or ''
        msg = cb.get('message') or {}
        chat_id = (msg.get('chat') or {}).get('id')
        mid = msg.get('message_id')
        if chat_id is None:
            return
        if not self._is_owner(chat_id):
            self.tg.answer_callback(qid, '🔒')
            return
        self.tg.answer_callback(qid, '')
        p = data.split(':')
        head = p[0]
        try:
            if head == 'home':
                self.show_status(chat_id, mid=mid)
            elif head == 'help':
                self.tg.edit_message(chat_id, mid, ui.h_help(),
                                     buttons=kb(
                                         [[('🏠 Trang chính', 'home')]]))
            elif head == 'st':
                self._cb_status(chat_id, mid, p)
            elif head == 'set':
                self._cb_settings(chat_id, mid, p)
            elif head == 'vm':
                self._cb_vm(chat_id, mid, p)
            elif head == 'act':
                self._cb_actions(chat_id, mid, p)
            elif head == 'rst':
                self._cb_restore(chat_id, mid, p)
            elif head == 'bk':
                self._cb_backup(chat_id, mid, p)
            elif head == 'cl':
                if len(p) > 1 and p[1] == 'go':
                    self.start_job('Dọn rác', chat_id, self.worker_cleanup)
                else:
                    self.show_cleanup(chat_id, mid=mid)
            elif head == 'ka':
                self._cb_keepalive(chat_id, mid, p)
            elif head == 'q':
                if len(p) > 1 and p[1] == 'help':
                    self.tg.edit_message(chat_id, mid, ui.quota_help_text(self.cfg),
                                         buttons=kb(
                                             [[('⬅️ Quota', 'q:show'), ('🏠 Trang chính', 'home')]]))
                else:
                    self.show_quota(chat_id, mid=mid, force=True)
            elif head == 'job':
                msg2 = self.jobs.cancel(chat_id)
                self.tg.answer_callback(qid, msg2[:190])
                self.tg.send_message(chat_id, esc(msg2))
        except Exception as e:  # noqa
            self.log.error('callback %s loi: %r', data, e, exc_info=True)
            try:
                self.tg.answer_callback(qid, 'Lỗi: ' + str(e)[:150])
            except Exception:
                pass

    def _cb_status(self, chat_id, mid, p):
        act = p[1] if len(p) > 1 else 'r'
        if act == 'r':
            self.show_status(chat_id, mid=mid)
        elif act == 'csstart':
            self.start_job('Bật Codespace', chat_id, self.worker_cs_start)
        elif act == 'csstop':
            self.start_job('Dừng Codespace', chat_id, self.worker_cs_stop)

    def _cb_settings(self, chat_id, mid, p):
        act = p[1] if len(p) > 1 else 'menu'
        if act == 'menu' or act == 'back':
            self.show_settings(chat_id, mid=mid)
        elif act == 'gh':
            self.pending = {'chat_id': chat_id, 'kind': 'gh'}
            self.tg.edit_message(
                chat_id, mid,
                '🔑 <b>Gửi GitHub token</b> (tin nhắn tiếp theo).\n\n'
                '• Tạo tại https://github.com/settings/tokens (classic — chọn scope: '
                '<code>codespace</code>, <code>repo</code>, <code>admin:public_key</code>, '
                '<code>workflow</code>, <code>read:user</code>).\n'
                '• Gửi xong nên xóa tin nhắn chứa token của bạn.\n'
                '• Gõ /cancel để bỏ qua.',
                buttons=kb([[('❌ Bỏ qua', 'set:menu')]]))
        elif act == 'od':
            self.pending = {'chat_id': chat_id, 'kind': 'od'}
            self.tg.edit_message(
                chat_id, mid,
                '☁️ <b>Gửi OneDrive token JSON</b> (tin nhắn tiếp theo).\n\n'
                'Tạo trên máy có trình duyệt: chạy <code>rclone authorize onedrive</code> '
                '→ copy nguyên chuỗi JSON (có access_token + refresh_token) → dán vào đây.\n'
                '• Gõ /cancel để bỏ qua.',
                buttons=kb([[('❌ Bỏ qua', 'set:menu')]]))
        elif act == 'odpush':
            self.start_job('Đồng bộ OneDrive (setup)', chat_id, self.worker_setup)
        elif act == 'repo':
            self.show_repo(chat_id, mid=mid)
        elif act == 'repol':
            self.show_repo_list(chat_id, mid=mid)
        elif act == 'repon':
            self.pending = {'chat_id': chat_id, 'kind': 'repon'}
            self.tg.edit_message(
                chat_id, mid,
                '🆕 <b>Tạo repo mới</b> (private) — gửi <b>tên repo</b> hoặc <code>owner/repo</code>.\n'
                'Ví dụ: <code>winvm-tools</code> → tạo trong tài khoản của bạn.\n'
                '• Gõ /cancel để bỏ qua.',
                buttons=kb([[('❌ Bỏ qua', 'set:repo')]]))
        elif act == 'repoi':
            self.pending = {'chat_id': chat_id, 'kind': 'repo'}
            self.tg.edit_message(
                chat_id, mid,
                '✏️ Gửi <b>owner/repo</b> (hoặc tên repo ngắn) để dùng cho Codespace/Actions.\n'
                '• Gõ /cancel để bỏ qua.',
                buttons=kb([[('❌ Bỏ qua', 'set:repo')]]))
        elif act == 'repop':
            idx = int(p[2]) if len(p) > 2 else -1
            if 0 <= idx < len(self._repo_choices):
                row = self._repo_choices[idx]
                name = str(row.get('nameWithOwner') or row.get('name') or '')
                if name:
                    self.cfg.set('cs_repo', name)
                    self.tg.send_message(chat_id, f'✅ Đã đặt repo: <code>{esc(name)}</code>')
            self.show_repo(chat_id, mid=mid)
        elif act in ('cs', 'csl'):
            self.tg.edit_message(chat_id, mid, '🔍 Đang lấy danh sách codespace…')
            lst = self.gh.codespace_list()
            if not lst:
                self.tg.edit_message(
                    chat_id, mid,
                    '❌ Không lấy được danh sách codespace (kiểm tra GitHub token).',
                    buttons=kb(
                        [[('🔑 GitHub token', 'set:gh'), ('⬅️ Cài đặt', 'set:menu')]]))
                return
            self._cs_choices = lst
            rows = []
            for i, row in enumerate(lst[:10]):
                name = str(row.get('name') or '?')
                state = str(row.get('state') or '?')
                mark = '✅' if state == 'Available' else ('⏸' if state in ('Shutdown', 'Stopped') else '❓')
                rows.append([(f"{mark} {ui.shorten(name, 30)}", f'set:csp:{i}')])
            rows.append([('⬅️ Cài đặt', 'set:menu')])
            self.tg.edit_message(chat_id, mid, '🧩 <b>Chọn Codespace:</b>',
                                 buttons=kb(rows))
        elif act == 'csp':
            idx = int(p[2]) if len(p) > 2 else -1
            if 0 <= idx < len(self._cs_choices):
                row = self._cs_choices[idx]
                self.cfg.set('codespace', str(row.get('name') or ''))
                self.tg.send_message(
                    chat_id, f"✅ Đã chọn Codespace: <code>{esc(row.get('name'))}</code> "
                             f"({esc(row.get('state'))})")
            self.show_settings(chat_id, mid=mid)
        elif act == 'csnew':
            self.pending = {'chat_id': chat_id, 'kind': 'csrepo'}
            self.tg.edit_message(
                chat_id, mid,
                '🆕 <b>Tạo Codespace mới</b> — máy chuẩn <b>4 core • 16 GB RAM • 32 GB disk</b>.\n\n'
                'Gửi <b>owner/repo</b> để tạo Codespace từ repo đó '
                '(repo chưa có sẽ được tạo <b>private</b>).\n'
                'Hoặc gõ <code>auto</code> để bot tự tạo repo <code>winvm-codespace</code>.\n\n'
                'Tạo xong bot tự: đăng ký SSH key → mount ổ <code>/mnt/data</code>.\n'
                '• Gõ /cancel để bỏ qua.',
                buttons=kb([[('❌ Bỏ qua', 'set:menu')]]))
        elif act == 'kaint':
            self.pending = {'chat_id': chat_id, 'kind': 'kaint'}
            self.tg.edit_message(
                chat_id, mid,
                '⏱ Gửi <b>chu kỳ keepalive (giây)</b> — 15..300, khuyến nghị 60.',
                buttons=kb([[('❌ Bỏ qua', 'set:menu')]]))
        elif act == 'ka':
            self.cfg.set('keepalive_enabled', not self.cfg.get('keepalive_enabled'))
            self.show_settings(chat_id, mid=mid)
        elif act == 'vmmenu':
            self.tg.edit_message(chat_id, mid, ui.vmconf_text(self.cfg),
                                 buttons=ui.vmconf_kb(self.cfg))
        elif act == 'vmf':
            field = p[2] if len(p) > 2 else ''
            self.pending = {'chat_id': chat_id, 'kind': 'vmf:' + field}
            self.tg.edit_message(
                chat_id, mid,
                f'✏️ Gửi giá trị mới cho <b>{esc(field)}</b>.\n'
                '(cores 1-16 • ram 2-64 GB • disk 32-2048 GB • user chữ/số • '
                'pass ≥ 8 ký tự • windows: 10 hoặc 11)',
                buttons=kb([[('❌ Bỏ qua', 'set:vmmenu')]]))
        elif act == 'acc':
            text, k = ui.account_text(self.cfg, self.ka.snapshot() if self.ka else None)
            self.tg.edit_message(chat_id, mid, text, buttons=k)

    def _cb_vm(self, chat_id, mid, p):
        act = p[1] if len(p) > 1 else 'menu'
        if act == 'menu':
            self.show_vm(chat_id, mid=mid)
        elif act == 'start':
            self.start_job('Bật VM', chat_id, self.worker_vm_start)
        elif act == 'stop':
            self.start_job('Dừng VM', chat_id, self.worker_vm_stop)
        elif act == 'restart':
            self.start_job('Khởi động lại VM', chat_id, self.worker_vm_restart)
        elif act == 'recreate':
            self.tg.edit_message(chat_id, mid, ui.vm_confirm_text('recreate', self.cfg),
                                 buttons=ui.vm_confirm_kb('recreate'))
        elif act == 'recgo':
            self.start_job('Tạo lại container VM', chat_id, self.worker_vm_recreate)
        elif act == 'rm':
            self.tg.edit_message(chat_id, mid, ui.vm_confirm_text('rm', self.cfg),
                                 buttons=ui.vm_confirm_kb('rm'))
        elif act == 'rmgo':
            self.start_job('Xóa container VM', chat_id, self.worker_vm_rm)
        elif act == 'ports':
            self.start_job('Mở cổng public', chat_id, self.worker_ports)

    def _cb_keepalive(self, chat_id, mid, p):
        act = p[1] if len(p) > 1 else 'log'
        if act in ('log', 'r'):
            self.show_keepalive(chat_id, mid=mid)
        elif act == 'test':
            self.start_job('Test keepalive', chat_id, self.worker_ka_test)
        elif act == 'clear':
            if self.ka:
                try:
                    self.ka.clear_log()
                except Exception:
                    pass
            self.show_keepalive(chat_id, mid=mid)

    def worker_ka_test(self, ctx):
        snap = self.ka.snapshot() if self.ka else {}
        cs = self.cfg.get('codespace') or ''
        ctx.edit('🧪 <b>TEST KEEPALIVE</b> — đang mở SSH tới Codespace…', force=True)
        if not cs:
            raise JobError('Chưa chọn Codespace — mở /settings trước.')
        if not self.cfg.has_github:
            raise JobError('Chưa có GitHub token — mở /settings trước.')
        try:
            st = self.remote.state()
        except Exception as e:  # noqa
            st = f'lỗi: {e}'
        if st != 'Available':
            raise JobError(f'Codespace đang <b>{esc(st)}</b> — cần Available để test. '
                           'Bấm "Bật Codespace" ở /status rồi thử lại.')
        try:
            self.gh.ensure_ssh_registered()
        except Exception:
            pass
        cmd = ('echo "KA_TARGET=$(hostname)"; '
               'echo "KA_DATE=$(date -u +%Y-%m-%dT%H:%M:%SZ)"; '
               'echo "KA_WORKSPACE=$(pwd)"; '
               'echo "KA_CS_NAME=' + cs.replace('"', '') + '"')
        r = self.gh.ssh(cs, cmd, timeout=120)
        out = ((r.out or '') + '\n' + (r.err or '')).strip()
        ok = r.ok and 'KA_TARGET=' in out
        snap2 = self.ka.snapshot() if self.ka else snap
        res = {'ok': ok, 'rc': r.rc, 'out': out[-1500:],
               'error': '' if ok else ('SSH không trả về dữ liệu — kiểm tra log keepalive / '
                                       'token / trạng thái Codespace.')}
        ctx.edit(ui.keepalive_test_text(self.cfg, snap2, res),
                 buttons=ui.keepalive_test_kb(), force=True)

    def _cb_actions(self, chat_id, mid, p):
        act = p[1] if len(p) > 1 else 'menu'
        if act in ('menu', 'r'):
            self.show_actions(chat_id, mid=mid)
        elif act == 'go':
            if not self.cfg.get('tailscale_key'):
                self.pending = {'chat_id': chat_id, 'kind': 'tskey'}
                self.tg.edit_message(
                    chat_id, mid,
                    '🔑 <b>Gửi Tailscale auth key</b> (tin nhắn tiếp theo).\n\n'
                    '• Tạo tại https://login.tailscale.com/admin/settings/keys '
                    '(chọn <b>reusable</b>).\n'
                    '• Bot sẽ lưu key, đặt thành secret <code>TAILSCALE_AUTHKEY</code> '
                    'cho repo rồi chạy workflow VM.\n'
                    '• Gõ /cancel để bỏ qua.',
                    buttons=kb([[('❌ Bỏ qua', 'act:menu')]]))
            else:
                self.start_job('Chạy VM Actions', chat_id, self.worker_actions_start)
        elif act == 'st':
            self.start_job('Trạng thái VM Actions', chat_id, self.worker_actions_status)
        elif act == 'cancel':
            self.start_job('Hủy VM Actions', chat_id, self.worker_actions_cancel)

    def _cb_restore(self, chat_id, mid, p):
        act = p[1] if len(p) > 1 else 'scan'
        if act == 'scan':
            self.show_restore_list(chat_id, mid=mid)
        elif act == 'pk':
            idx = int(p[2]) if len(p) > 2 else -1
            if not (0 <= idx < len(self._rst_choices)):
                self.tg.send_message(chat_id, '⚠️ Danh sách đã cũ — quét lại OneDrive…')
                self.show_restore_list(chat_id, mid=mid)
                return
            folder = self._rst_choices[idx]
            try:
                probe = self.get_probe(max_age=4.0)
                img = probe.get('img_bytes') or 0
            except Exception:
                img = 0
            self.tg.edit_message(chat_id, mid, ui.restore_confirm_text(folder, img),
                                 buttons=ui.restore_confirm_kb(idx))
        elif act == 'go':
            idx = int(p[2]) if len(p) > 2 else -1
            if not (0 <= idx < len(self._rst_choices)):
                self.tg.send_message(chat_id, '❌ Danh sách đã cũ — bấm /restore để quét lại.')
                return
            folder = self._rst_choices[idx]
            self.start_job('Khôi phục ' + folder[:30], chat_id,
                           lambda ctx: self.worker_restore(ctx, folder))

    def _cb_backup(self, chat_id, mid, p):
        act = p[1] if len(p) > 1 else 'new'
        if act == 'new':
            self.show_backup_confirm(chat_id, mid=mid)
        elif act == 'l':
            self._bk_opts['keep_local'] = (p[2] == '1')
            self.show_backup_confirm(chat_id, mid=mid)
        elif act == 'r':
            self._bk_opts['restart'] = (p[2] == '1')
            self.show_backup_confirm(chat_id, mid=mid)
        elif act == 'go':
            self.start_job('Backup', chat_id, self.worker_backup)

    # ================= nhập liệu =================
    def _consume_input(self, chat_id, kind, text, mid):
        if kind == 'gh':
            self._apply_gh_token(chat_id, text, mid)
        elif kind == 'od':
            self._apply_od_token(chat_id, text, mid)
        elif kind == 'csrepo':
            repo = text.strip()
            if repo.lower() in ('auto', 'tu dong', 'tự động', 'tu-dong', 'tudong', '-'):
                repo = 'auto'
            self.cfg.set('cs_repo', repo)
            self._try_delete(chat_id, mid)
            self.start_job('Tạo Codespace', chat_id, self.worker_cs_create)
        elif kind == 'repo':
            self._apply_repo(chat_id, text, mid, create=False)
        elif kind == 'repon':
            self._apply_repo(chat_id, text, mid, create=True)
        elif kind == 'tskey':
            key = text.strip().strip('"').strip("'")
            if not key.startswith('tskey-'):
                self.tg.send_message(chat_id, '❌ Key không hợp lệ — phải bắt đầu bằng '
                                              '<code>tskey-</code>. Bấm "🚀 Khởi động VM Actions" để thử lại.')
                return
            self.cfg.set('tailscale_key', key)
            self._try_delete(chat_id, mid)
            self.tg.send_message(chat_id, '✅ Đã lưu Tailscale key. Đang khởi động VM Actions…')
            self.start_job('Chạy VM Actions', chat_id, self.worker_actions_start)
        elif kind == 'kaint':
            try:
                v = int(text)
            except ValueError:
                self.tg.send_message(chat_id, '❌ Không phải số nguyên.')
                return
            if not (15 <= v <= 300):
                self.tg.send_message(chat_id, '❌ Giá trị 15–300 giây.')
                return
            self.cfg.set('keepalive_interval', v)
            self.tg.send_message(chat_id, f'✅ Keepalive mỗi <b>{v}s</b>.')
            self.show_settings(chat_id)
        elif kind.startswith('vmf:'):
            self._apply_vmf(chat_id, kind[4:], text)

    def _apply_gh_token(self, chat_id, text, mid):
        tok = text.strip().strip('"').strip("'")
        if not tok:
            self.tg.send_message(chat_id, '❌ Token trống.')
            return
        ok, info, scopes = self.gh.check_token(tok)
        if not ok:
            self.tg.send_message(chat_id, '❌ Token GitHub không hợp lệ.\n<code>%s</code>' % esc(info))
            return
        self.cfg.set('github_token', tok, save=False)
        try:
            self.gh.login_with_token(tok)  # lưu bền vào GH_CONFIG_DIR (best effort)
        except Exception:
            pass
        self.cfg.data['github_login'] = info
        self.cfg.save()
        warn = ''
        sc = [s.strip().lower() for s in (scopes or '').split(',') if s.strip()]
        if sc and 'codespace' not in sc:
            warn += ('\n⚠️ Token thiếu quyền <code>codespace</code> — lệnh Codespace sẽ lỗi. '
                     'Nên tạo token classic kèm scope này.')
        if sc and 'workflow' not in sc:
            warn += ('\n⚠️ Token thiếu scope <code>workflow</code> — tính năng /actions '
                     '(VM Actions) sẽ lỗi khi chạy. Nên tạo token kèm scope này.')
        self._try_delete(chat_id, mid)
        self.tg.send_message(chat_id, f'✅ GitHub OK: <b>{esc(info)}</b>. '
                                      f'Nhớ xóa tin nhắn chứa token.{warn}')
        self.show_settings(chat_id)

    def _apply_od_token(self, chat_id, text, mid):
        tok = text.strip()
        try:
            parsed = json.loads(tok)
        except ValueError as e:
            self.tg.send_message(chat_id, f'❌ Không phải JSON: {esc(str(e))}. '
                                          'Hãy copy nguyên chuỗi từ <code>rclone authorize onedrive</code>.')
            return
        if not isinstance(parsed, dict) or not parsed.get('access_token'):
            self.tg.send_message(chat_id, '❌ JSON thiếu <code>access_token</code>.')
            return
        import bot.gh as ghmod
        info, err = ghmod.graph_drive_info(tok)
        if err:
            self.cfg.set('onedrive_token', tok)
            self._try_delete(chat_id, mid)
            self.tg.send_message(chat_id, '⚠️ Đã lưu token nhưng chưa xác minh được: ' + esc(err) +
                                 '\nSẽ kiểm tra lại khi chạy /setup.')
        else:
            self.cfg.data['onedrive_token'] = tok
            self.cfg.data['od_owner'] = info.get('owner') or ''
            self.cfg.data['od_total'] = info.get('total') or 0
            self.cfg.data['od_used'] = info.get('used') or 0
            self.cfg.save()
            self._try_delete(chat_id, mid)
            self.tg.send_message(
                chat_id,
                f"✅ OneDrive OK: <b>{esc(info.get('owner'))}</b> — "
                f"{fmt_bytes(info.get('used') or 0)}/{fmt_bytes(info.get('total') or 0)}. "
                'Nhớ xóa tin nhắn chứa token.')
        kb_ = kb(
            [[('☁️ Đồng bộ lên Codespace ngay', 'set:odpush'), ('⚙️ Cài đặt', 'set:menu')]])
        self.tg.send_message(chat_id, 'Bấm để ghi rclone.conf vào Codespace:', buttons=kb_)

    def _apply_vmf(self, chat_id, field, text):
        v = text.strip()
        vm = self.cfg.vm()
        try:
            if field == 'cores':
                iv = int(v)
                assert 1 <= iv <= 16
                vm['cores'] = iv
            elif field == 'ram':
                iv = int(v)
                assert 2 <= iv <= 64
                vm['ram_gb'] = iv
            elif field == 'disk':
                iv = int(v)
                assert 32 <= iv <= 2048
                vm['disk_gb'] = iv
            elif field == 'ver':
                assert v in ('10', '11', '2022', '2025', 'ltsc10', 'ltsc11')
                vm['windows_version'] = v
            elif field == 'user':
                assert re.match(r'^[A-Za-z0-9_.-]{1,20}$', v)
                vm['user'] = v
            elif field == 'pass':
                assert 8 <= len(v) <= 64
                vm['password'] = v
            else:
                raise ValueError('field lạ')
        except (ValueError, AssertionError):
            self.tg.send_message(chat_id, '❌ Giá trị không hợp lệ, thử lại.')
            return
        self.cfg.save()
        self.tg.send_message(chat_id, f'✅ Đã lưu <b>{esc(field)}</b>.')
        self.tg.send_message(chat_id, ui.vmconf_text(self.cfg), buttons=ui.vmconf_kb(self.cfg))

    # ================= job helpers =================
    def start_job(self, name, chat_id, worker):
        if self.jobs.busy():
            self.tg.send_message(chat_id, ui.busy_text(self.jobs.info()), buttons=ui.job_kb())
            return False
        if not self.jobs.start(name, chat_id, worker):
            self.tg.send_message(chat_id, ui.busy_text(self.jobs.info()), buttons=ui.job_kb())
            return False
        return True

    def _wait_marker(self, ctx, logpath, done, ok, timeout, interval, label):
        t0 = time.time()
        while True:
            out = self.remote.run_text(
                f"grep -qF '{done}' {logpath} 2>/dev/null && echo D1 || echo D0; "
                f"grep -qF '{ok}' {logpath} 2>/dev/null && echo K1 || echo K0; "
                f"tail -2 {logpath} 2>/dev/null || true", timeout=60, allow_fail=True)
            el = int(time.time() - t0)
            lines = [l.strip() for l in out.splitlines()]
            d_done = 'D1' in lines
            k_ok = 'K1' in lines
            detail = ' | '.join([l for l in lines if l and l not in ('D1', 'D0', 'K1', 'K0')][-2:])
            if d_done:
                return k_ok
            if ctx.cancelled:
                raise JobError('Đã hủy theo yêu cầu.')
            ctx.edit(f'⚙️ <b>{esc(label)}</b> — {fmt_elapsed(el)}' +
                     (f"\n<i>{esc(detail[:200])}</i>" if detail else ''), buttons=ui.job_kb())
            if el > timeout:
                return False
            ctx.sleep(interval)

    def _install_watchdog_files(self, ctx, start=True):
        cmds = (f"mkdir -p {WD_DIR} && chmod 700 {WD_DIR}")
        self.remote.run_text(cmds, allow_fail=True)
        self.remote.push_script('90_ensure.sh', WD_DIR + '/ensure.sh')
        self.remote.push_script('91_watchdog.sh', WD_DIR + '/watchdog.sh')
        self.remote.push_script('92_backup_once.sh', WD_DIR + '/backup_once.sh')

    # ================= worker: Codespace =================
    def worker_cs_start(self, ctx):
        ctx.edit('⏳ Đang bật Codespace…', force=True)
        r = self.gh.codespace_start(self.cfg.get('codespace') or '')
        if not r.ok:
            raise JobError('Không bật được Codespace: ' + esc((r.err or r.out)[-300:]))
        t0 = time.time()
        while time.time() - t0 < 600:
            st = self.remote.state()
            if st == 'Available':
                ctx.edit(f'✅ Codespace <b>Available</b> ({fmt_elapsed(time.time() - t0)}).',
                         buttons=ui.status_kb(self.cfg, st, None), force=True)
                return
            ctx.edit(f'⏳ Chờ Codespace Available… ({esc(st)}, {int(time.time() - t0)}s)',
                     buttons=ui.job_kb())
            ctx.sleep(10)
        raise JobError('Codespace không Available sau 10 phút.')

    def worker_cs_stop(self, ctx):
        ctx.edit('⏳ Đang dừng Codespace…', force=True)
        r = self.gh.codespace_stop(self.cfg.get('codespace') or '')
        if not r.ok:
            raise JobError('Không dừng được Codespace: ' + esc((r.err or r.out)[-300:]))
        ctx.edit('✅ Đã gửi lệnh dừng Codespace.', force=True)

    # ================= worker: tạo codespace =================
    def worker_cs_create(self, ctx):
        ctx.edit('🆕 <b>TẠO CODESPACE</b> — kiểm tra tài khoản…', force=True)
        if not self.cfg.has_github:
            raise JobError('Chưa có GitHub token — mở /settings → "🔑 GitHub token" trước.')
        ok_ssh, msg_ssh = self.gh.ensure_ssh_registered()
        if not ok_ssh:
            ctx.edit('⚠️ Không đăng ký được SSH key (' + esc(msg_ssh) + ') — vẫn tiếp tục tạo…', force=True)
        repo = self._actions_repo(ctx)
        machine = (self.cfg.get('cs_machine') or 'standardLinux32gb').strip()
        before = {str(r0.get('name') or '') for r0 in self.gh.codespace_list()}
        ctx.edit(f'🆕 Đang tạo Codespace từ <code>{esc(repo)}</code>\n'
                 f'• Máy: <b>4 core • 16 GB RAM • 32 GB disk</b> (<code>{esc(machine)}</code>)\n'
                 f'• Có thể mất 3–10 phút…', force=True)
        r = self.gh.codespace_create(repo, machine)
        if not r.ok:
            err = ((r.err or '') + '\n' + (r.out or '')).strip()
            low = err.lower()
            if 'billing' in low or 'quota' in low or 'payment' in low:
                raise JobError('Tài khoản chưa bật Codespaces/billing hoặc hết quota — '
                               'mở https://github.com/settings/billing kiểm tra.\n' + esc(err[-300:]))
            raise JobError('Tạo Codespace thất bại.\n<code>' + esc(err[-400:]) + '</code>')
        name = ''
        st = ''
        t0 = time.time()
        while True:
            if not name:
                for row in self.gh.codespace_list():
                    nm = str(row.get('name') or '')
                    if nm and nm not in before:
                        name = nm
                        break
                if not name:
                    for line in reversed((r.out or '').splitlines()):
                        s = line.strip()
                        if re.fullmatch(r'[a-z0-9][a-z0-9-]{2,60}', s) and s not in before:
                            name = s
                            break
            if name:
                st = self.gh.codespace_state(name)
                if st == 'Available':
                    break
            el = int(time.time() - t0)
            if el > 1200:
                break
            if ctx.cancelled:
                raise JobError('Đã hủy theo yêu cầu.')
            ctx.edit(f'⏳ Chờ Codespace sẵn sàng… ({st or "đang tạo"}, {el}s)', buttons=ui.job_kb())
            ctx.sleep(15)
        if not name:
            raise JobError('Đã tạo nhưng không đọc được tên Codespace — mở /settings → "🧩 Codespace" '
                           'để chọn thủ công.\n<code>' + esc((r.out or '')[-300:]) + '</code>')
        if st != 'Available':
            raise JobError(f'Codespace <code>{esc(name)}</code> chưa Available sau 20 phút (đang {st}). '
                           'Thử /status lại sau ít phút.')
        self.cfg.set('codespace', name)
        ctx.edit('🔐 Kiểm tra SSH → mount ổ dữ liệu…', force=True)
        try:
            self.remote.run_text('echo SSH_OK', timeout=180, allow_fail=False)
        except Exception as e:
            raise JobError('SSH tới Codespace mới thất bại — thử /status rồi /setup lại.\n'
                           + esc(str(e)[-250:]))
        self._setup_remote(ctx, '⚙️ Mount ổ dữ liệu cho Codespace mới…',
                           final_note=f'\n🆕 Codespace: <code>{esc(name)}</code>')

    # ================= worker: setup =================
    def worker_setup(self, ctx):
        self._setup_remote(ctx, '⚙️ <b>SETUP</b> — kiểm tra Codespace…')

    def _setup_remote(self, ctx, lead, final_note=''):
        """Chuẩn bị Codespace: chờ Available → mount /mnt/data + rclone → (nếu có token) nối OneDrive."""
        ctx.edit(lead, force=True)
        self.remote.ensure_available(progress=lambda t: ctx.edit('⚙️ ' + esc(t), buttons=ui.job_kb()))
        ctx.edit('📦 Mount ổ dữ liệu + cài rclone…', force=True)
        self.remote.start_script('01_setup.sh', '/tmp/wm_01_setup.sh', '/tmp/wm_setup.log')
        ok = self._wait_marker(ctx, '/tmp/wm_setup.log', 'ST_ALL_DONE', 'ST_OK',
                               600, 6, 'mount ổ dữ liệu + cài rclone')
        if not ok:
            tail = self.remote.tail('/tmp/wm_setup.log', 5)
            raise JobError('Setup mount/rclone thất bại. Log: ' + esc(tail[-300:]))

        note = ''
        od_line = ''
        if not self.cfg.has_onedrive:
            note = '\n⚠️ Chưa nối OneDrive (thiếu token) — nhập ở /settings rồi chạy /setup lại.'
        else:
            ctx.edit('☁️ Ghi rclone.conf + xác minh OneDrive…', force=True)
            b64 = base64.b64encode(self.cfg.get('onedrive_token').encode('utf-8')).decode('ascii')
            self.remote.start_script('02_rclone.sh', '/tmp/wm_02_rclone.sh', '/tmp/wm_rclone.log',
                                     repl={'__TOKB64__': b64})
            ok2 = self._wait_marker(ctx, '/tmp/wm_rclone.log', 'RCL_ALL_DONE', 'RCL_OK',
                                    300, 6, 'nối OneDrive + xác minh')
            logtxt = self.remote.tail('/tmp/wm_rclone.log', 14)
            m = re.search(r'OD_OWNER=(.+)', logtxt)
            if m and m.group(1).strip():
                self.cfg.data['od_owner'] = m.group(1).strip()
            m = re.search(r'OD_TOTAL=(\d+)', logtxt)
            if m:
                self.cfg.data['od_total'] = int(m.group(1))
            m = re.search(r'OD_USED=(\d+)', logtxt)
            if m:
                self.cfg.data['od_used'] = int(m.group(1))
            self.cfg.save()
            if 'WARN_NO_REFRESH_TOKEN' in logtxt:
                note = ('\n⚠️ Token thiếu refresh_token — rclone sẽ hết hạn sau ~1 giờ. '
                        'Nên tạo token mới.')
            if not ok2:
                if 'NO_TOKEN' in logtxt:
                    raise JobError('Thiếu token OneDrive — vào /settings nhập lại.')
                if 'NO_DRIVE_ID' in logtxt:
                    raise JobError('Token OneDrive hết hạn và chưa có drive_id cũ — tạo token mới '
                                   '(rclone authorize onedrive) rồi nhập lại vào /settings.')
                if 'about rc=' in logtxt:
                    raise JobError('rclone không truy cập được OneDrive (token sai/hết hạn). '
                                   'Log: ' + esc(logtxt[-300:]))
                raise JobError('Nối OneDrive thất bại. Log: ' + esc(logtxt[-300:]))
            if self.cfg.get('od_owner'):
                od_line = (f"\n☁️ OneDrive: <b>{esc(self.cfg.get('od_owner'))}</b> — "
                           f"{fmt_bytes(self.cfg.get('od_used') or 0)}/"
                           f"{fmt_bytes(self.cfg.get('od_total') or 0)}")

        ctx.edit('✅ <b>Môi trường Codespace sẵn sàng</b> (ổ dữ liệu đã mount).'
                 + od_line + note + final_note,
                 buttons=kb([
                     [('▶️ Bật VM', 'vm:start')],
                     [('📥 Restore từ OneDrive', 'rst:scan'), ('📊 Trạng thái', 'home')]]),
                 force=True)

    # ================= helper: repo cho Codespace / Actions =================
    def _actions_repo(self, ctx, create=True):
        """Xác định repo dùng cho Codespace + VM Actions (tạo private nếu chưa có)."""
        login = self.cfg.get('github_login') or self.gh.api_user() or ''
        if login and not self.cfg.get('github_login'):
            self.cfg.set('github_login', login)
        repo = (self.cfg.get('cs_repo') or '').strip()
        if repo.lower() in ('auto', 'tu dong', 'tự động', 'tu-dong', 'tudong', '-', ''):
            if not login:
                raise JobError('Không đọc được tên tài khoản GitHub — nhập lại token ở /settings.')
            repo = f'{login}/winvm-codespace'
        elif '/' not in repo and login:
            repo = f'{login}/{repo}'
        if create and not self.gh.repo_exists(repo):
            ctx.edit(f'📦 Repo <code>{esc(repo)}</code> chưa có — tạo repo private…', force=True)
            r = self.gh.repo_create(repo, private=True)
            if not r.ok:
                raise JobError('Không tạo được repo: ' + esc((r.err or r.out or 'không rõ')[-300:]))
        self.cfg.set('cs_repo', repo)
        return repo

    # ================= worker: VM Actions (GitHub Actions) =================
    def worker_actions_start(self, ctx):
        ctx.edit('🖥 <b>VM ACTIONS</b> — chuẩn bị repo…', force=True)
        if not self.cfg.has_github:
            raise JobError('Chưa có GitHub token — mở /settings → "🔑 GitHub token" trước.')
        key = (self.cfg.get('tailscale_key') or '').strip()
        if not key:
            raise JobError('Chưa có Tailscale key — bấm "🚀 Khởi động VM Actions" để nhập.')
        repo = self._actions_repo(ctx)
        wf_name = 'winvm-actions.yml'
        wf_path = '.github/workflows/' + wf_name
        try:
            content = self.remote.script('winvm_actions.yml')
        except OSError as e:
            raise JobError('Bot thiếu template workflow: ' + esc(str(e)))
        ctx.edit(f'📤 Ghi workflow + secret vào <code>{esc(repo)}</code>…', force=True)
        r = self.gh.repo_put_file(repo, wf_path, content, 'winremote-bot: workflow VM Actions')
        if not r.ok:
            raise JobError('Không ghi được workflow: ' + esc((r.err or r.out or '')[-300:]))
        r = self.gh.secret_set(repo, 'TAILSCALE_AUTHKEY', key)
        if not r.ok:
            err = ((r.err or '') + ' ' + (r.out or '')).strip()
            if 'scope' in err.lower():
                raise JobError('Token GitHub thiếu quyền — cần tạo token có scope '
                               '<code>workflow</code> + <code>admin:public_key</code> rồi nhập lại /settings. '
                               + esc(err[-200:]))
            raise JobError('Không đặt được secret: ' + esc(err[-300:]))
        ctx.edit('🚀 Đang gửi lệnh chạy workflow…', force=True)
        r = None
        for _ in range(5):
            r = self.gh.workflow_run(repo, wf_name)
            if r.ok:
                break
            ctx.sleep(10)
        if not r.ok:
            err = ((r.err or '') + ' ' + (r.out or '')).strip()
            low = err.lower()
            if 'scope' in low or '403' in low:
                raise JobError('Token GitHub thiếu scope <code>workflow</code> — tạo token mới '
                               '(thêm scope này) rồi nhập lại /settings. ' + esc(err[-200:]))
            if 'disabled' in low or 'billing' in low:
                raise JobError('Actions của repo đang bị tắt hoặc thiếu billing — mở '
                               f'https://github.com/{esc(repo)}/actions bật lại. ' + esc(err[-150:]))
            raise JobError('Không chạy được workflow: ' + esc(err[-300:]))
        ctx.sleep(8)
        run = None
        try:
            runs = self.gh.run_list(repo, wf_name, 1)
            if runs:
                run = runs[0]
        except Exception:
            run = None
        self._act_cache = (0.0, None)  # để /status hiện run mới ngay
        run_line = ''
        url = ''
        if run:
            st = str(run.get('status') or '?')
            url = str(run.get('url') or '')
            run_line = f"\n• Trạng thái run: <b>{esc(ui.ACT_STATE_VN.get(st, st))}</b>"
        rows = []
        if url:
            rows.append([('🌐 Mở run trên GitHub', url)])
        rows.append([('🔄 Trạng thái run', 'act:st'), ('🕘 Hủy run', 'act:cancel')])
        rows.append([('🖥 Quản lý VM', 'vm:menu'), ('🏠 Trang chính', 'home')])
        ctx.edit('✅ <b>ĐÃ KHỞI ĐỘNG VM ACTIONS</b>\n'
                 f"• Repo: <code>{esc(repo)}</code>" + run_line +
                 '\n• Máy: <code>windows-latest</code> — chạy tối đa ~6 tiếng (350 phút)\n'
                 '• RDP: user <b>trongphuc</b> • mật khẩu <code>VmSecure@2026#</code>\n'
                 '• Kết nối qua Tailscale: thiết bị <b>winvm-actions</b> — bấm "Trạng thái run" '
                 'sau ~1-2 phút để lấy IP (bot tự đọc), hoặc xem trong admin console Tailscale.',
                 buttons=kb(rows), force=True)

    def worker_actions_status(self, ctx):
        ctx.edit('🔄 Đang lấy trạng thái run…', force=True)
        if not self.cfg.has_github:
            raise JobError('Chưa có GitHub token — /settings.')
        repo = self._actions_repo(ctx, create=False)
        runs = self.gh.run_list(repo, 'winvm-actions.yml', 1)
        if not runs:
            raise JobError('Chưa thấy run nào của workflow VM Actions trên repo.')
        run = runs[0]
        st = str(run.get('status') or '?')
        conc = str(run.get('conclusion') or '')
        rid = str(run.get('databaseId') or '')
        url = str(run.get('url') or '')
        created = str(run.get('createdAt') or '')[:19].replace('T', ' ')
        state_txt = ui.ACT_STATE_VN.get(st, st)
        if st == 'completed':
            state_txt += f" ({esc(conc or 'không rõ kết quả')})"
        # IP Tailscale: ưu tiên file winvm-actions.ip (workflow ghi ngay khi máy lên),
        # dự phòng log run (chỉ tải được sau khi run kết thúc)
        ip_line = ''
        ip_re = r'TS_IP=(100\.\d{1,3}\.\d{1,3}\.\d{1,3})'
        if rid:
            ip_txt = ''
            try:
                ip_txt = self.gh.repo_get_file(repo, 'winvm-actions.ip')
            except Exception:
                ip_txt = ''
            m = re.search(ip_re, ip_txt or '')
            mrun = re.search(r'run=(\d+)', ip_txt or '')
            if not (m and mrun and mrun.group(1) == rid):
                m = None
            if not m and st in ('in_progress', 'completed') and conc in ('', 'success'):
                try:
                    log = self.gh.run_log_text(repo, rid, timeout=150)
                except Exception:
                    log = ''
                m = re.search(ip_re, log or '')
            if m:
                ip_line = (f"\n• IP Tailscale: <code>{esc(m.group(1))}</code> — RDP tới IP này "
                           '(user trongphuc)')
            elif st == 'in_progress':
                ip_line = '\n• ⏳ Chưa thấy IP — máy đang khởi động, thử lại sau ~1-2 phút.'
        rows = []
        if url:
            rows.append([('🌐 Mở run trên GitHub', url)])
        rows.append([('🔄 Làm mới', 'act:st'), ('🚀 Chạy mới', 'act:go')])
        rows.append([('🖥 Quản lý VM', 'vm:menu'), ('🏠 Trang chính', 'home')])
        ctx.edit(f'🖥 <b>VM ACTIONS — {esc(state_txt)}</b>\n'
                 f"• Repo: <code>{esc(repo)}</code>\n"
                 f"• Run #{esc(rid)} • tạo lúc {esc(created or '?')}" + ip_line,
                 buttons=kb(rows), force=True)

    def worker_actions_cancel(self, ctx):
        ctx.edit('🕘 Đang hủy run…', force=True)
        if not self.cfg.has_github:
            raise JobError('Chưa có GitHub token — /settings.')
        repo = self._actions_repo(ctx, create=False)
        runs = self.gh.run_list(repo, 'winvm-actions.yml', 1)
        if not runs:
            raise JobError('Không thấy run nào để hủy.')
        run = runs[0]
        st = str(run.get('status') or '')
        rid = run.get('databaseId')
        if st not in ('queued', 'in_progress', 'requested', 'waiting', 'pending'):
            ctx.edit(f"ℹ️ Run gần nhất đã <b>{esc(ui.ACT_STATE_VN.get(st, st))}</b> — không cần hủy.",
                     buttons=kb([[('🔄 Trạng thái', 'act:st'), ('🏠 Trang chính', 'home')]]),
                     force=True)
            return
        r = self.gh.run_cancel(repo, rid)
        if not r.ok:
            raise JobError('Không hủy được run: ' + esc((r.err or r.out or '')[-250:]))
        self._act_cache = (0.0, None)
        ctx.edit('✅ Đã gửi lệnh hủy run — máy Actions sẽ tắt trong ít phút.',
                 buttons=kb([[('🔄 Trạng thái', 'act:st'), ('🏠 Trang chính', 'home')]]),
                 force=True)

    # ================= worker: backup =================
    def worker_backup(self, ctx):
        ctx.edit('💾 <b>BACKUP</b> — kiểm tra điều kiện…', force=True)
        self.remote.ensure_available(progress=lambda t: ctx.edit('💾 ' + esc(t), buttons=ui.job_kb()))
        probe = self.remote.probe()
        if probe['container'] == 'none':
            raise JobError('Chưa có container windows — bật VM (/vm → Bật VM) hoặc Restore trước.')
        if (probe['img_bytes'] or 0) <= 0:
            raise JobError('Chưa có data.img trên Codespace — hãy Restore hoặc bật VM tạo đĩa trước.')
        if probe['lock_busy']:
            raise JobError('Đang có backup khác chạy — chờ xong rồi thử lại.')
        if not probe['conf']:
            raise JobError('Chưa cấu hình OneDrive (rclone) — chạy /setup trước.')

        chk = self.remote.run_text(f"test -f {WD_DIR}/backup_once.sh && echo YES || echo NO")
        if 'YES' not in chk:
            ctx.edit('📤 Cài bộ script backup…', force=True)
            self._install_watchdog_files(ctx)

        env_parts = []
        if self._bk_opts['keep_local']:
            env_parts.append('VM_KEEP_LOCAL=1')
        if not self._bk_opts['restart']:
            env_parts.append('VM_FORCE_NO_RESTART=1')
        env_str = ' '.join(env_parts)
        pre = ('rm -f /tmp/vm_backup_busy /tmp/vm_backup_run.log /tmp/vm_backup_progress '
               '/tmp/vm_upload.log /tmp/vm_backup_cancel')
        guard = ('if command -v flock >/dev/null 2>&1 && ! flock -n /tmp/vm_backup.lock -c true '
                 '2>/dev/null; then echo LOCK_BUSY; else ')
        body = (f"setsid nohup env {env_str} bash {WD_DIR}/backup_once.sh "
                f">> /tmp/vm_backup_run.log 2>&1 < /dev/null & echo LAUNCHED; fi")
        r = self.remote.run(f'{pre}; {guard} {body}', timeout=90, allow_fail=True)
        txt = r.out + '\n' + r.err
        if 'LOCK_BUSY' in txt:
            raise JobError('Có backup khác đang chạy — chờ xong rồi thử lại.')
        if 'LAUNCHED' not in txt:
            raise JobError('Không khởi chạy được backup: ' + esc(txt[-300:]))

        def _cancel_remote():
            try:
                self.remote.run_text('touch /tmp/vm_backup_cancel && echo OK', allow_fail=True)
            except Exception:
                pass
            return 'Sẽ dừng ở ranh giới bước kế tiếp (VM được bật lại an toàn).'

        ctx.remote_cancel = _cancel_remote
        t0 = time.time()
        while True:
            out = self.remote.run_text(PROBE_BACKUP, timeout=90, allow_fail=True)
            blocks = marked_blocks(out, BACKUP_MARKERS)
            prog = _first(blocks, 'MARK_P')
            busy_raw = _first(blocks, 'MARK_B')
            marker = _first(blocks, 'MARK_R')
            alive = self._int_safe(_first(blocks, 'MARK_AL'))
            stage, pbytes, parts = '', 0, 0
            mp = RX_STAGE.match(prog)
            if mp:
                stage, pbytes, parts = mp.group(1), self._int_safe(mp.group(2)), self._int_safe(mp.group(3))
            archive = self._int_safe(''.join(_first(blocks, 'MARK_A').split()) or '0')
            part_count = self._int_safe(''.join(_first(blocks, 'MARK_C').split()) or '0')
            upload_line = _first(blocks, 'MARK_U')
            log_tail = [l for l in (blocks.get('MARK_L') or []) if l.strip()]
            el = time.time() - t0
            if not marker and busy_raw and alive <= 0:
                marker = 'RESULT=busy'

            title = '💾 <b>BACKUP ĐANG CHẠY</b>'
            if ctx.cancelled:
                title = '💾 <b>BACKUP ĐANG HỦY</b> (chờ ranh giới bước)'
            stage_txt = ui.STAGE_VN.get(stage, stage or 'chuẩn bị backup…')
            extra = []
            cur = archive if archive > 0 else pbytes
            if cur > 0:
                extra.append('archive ' + fmt_bytes(cur))
            cnt = part_count or parts
            if cnt > 0:
                extra.append(f'{cnt} part')
            if stage == 'upload' and upload_line:
                mpct = RX_UP_PCT.search(upload_line)
                if mpct:
                    extra.append(mpct.group(1) + '%')
                me = RX_ETA.search(upload_line)
                if me:
                    extra.append('còn ' + me.group(1))
            detail = ''
            if upload_line and stage == 'upload':
                detail = upload_line
            elif log_tail:
                detail = log_tail[-1]
            detail = re.sub(r'^\[[^]]+\]\s*\[backup\]\s*', '', detail)
            detail = re.sub(r'^\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2} NOTICE:\s*', '', detail)
            msg = (f"{title} ({fmt_elapsed(el)})\n• {esc(stage_txt)}" +
                   (f"\n• {' • '.join(extra)}" if extra else '') +
                   (f"\n<i>{esc(detail[:180])}</i>" if detail else ''))
            ctx.edit(msg, buttons=ui.job_kb())

            if marker:
                self._finish_backup(ctx, marker, el)
                return
            if not marker and alive <= 0 and el > 150:
                raise JobError('Backup dừng bất thường (tiến trình biến mất). '
                               'Xem /tmp/vm_backup_run.log trên Codespace.')
            if el > 86400:
                raise JobError('Backup quá 24h — dừng theo dõi.')
            ctx.sleep(self.poll_sec)

    def _finish_backup(self, ctx, marker, el):
        if marker == 'RESULT=cancelled':
            ctx.edit(f'⛔ Đã hủy backup an toàn sau {fmt_elapsed(el)} '
                     '(VM đã được bật lại nếu trước đó đang chạy).',
                     buttons=kb(
                         [[('📊 Trạng thái', 'home')]]), force=True)
            return
        if marker == 'RESULT=busy':
            raise JobError('Có backup khác đang chạy.')
        if marker != 'RESULT=ok':
            tail = self.remote.tail('/tmp/vm_backup_run.log', 10)
            raise JobError(f'Backup lỗi ({esc(marker)}). Log: ' + esc(tail[-350:]))
        final_raw = self.remote.run_text(
            'grep -E "^(BACKUP_FOLDER|ARCHIVE_BYTES|PARTS)=" /tmp/vm_backup_run.log 2>/dev/null',
            allow_fail=True)
        folder = ''
        arc = 0
        parts = 0
        m = re.search(r'BACKUP_FOLDER=(\S+)', final_raw)
        if m:
            folder = m.group(1)
        m = re.search(r'ARCHIVE_BYTES=(\d+)', final_raw)
        if m:
            arc = self._int_safe(m.group(1))
        m = re.search(r'PARTS=(\d+)', final_raw)
        if m:
            parts = self._int_safe(m.group(1))
        try:
            self.remote.refetch_onedrive_token()
        except Exception:
            pass
        note = ('\n🗄 Bản local đã được dọn (backup cũ trên OneDrive không bị xóa).'
                if not self._bk_opts['keep_local'] else '\n🗄 Bản local được giữ trong /mnt/data/backup.')
        ctx.edit(
            f'✅ <b>HOÀN TẤT BACKUP</b> ({fmt_elapsed(el)})\n'
            f"• Đích: <code>onedrive:{esc(folder)}/parts/</code>\n"
            f'• Archive: <b>{fmt_bytes(arc)}</b> • {parts} part (1GB/part)' + note,
            buttons=kb([
                [('📥 Restore bản này', 'rst:scan'), ('📊 Trạng thái', 'home')]]),
            force=True)

    # ================= worker: restore =================
    def worker_restore(self, ctx, folder):
        ctx.edit('📥 <b>KHÔI PHỤC</b> — kiểm tra Codespace…', force=True)
        self.remote.ensure_available(progress=lambda t: ctx.edit('📥 ' + esc(t), buttons=ui.job_kb()))
        if not self.cfg.has_onedrive:
            raise JobError('Chưa có token OneDrive — /settings.')
        probe = self.remote.probe()
        if not probe['mount'] or not probe['conf']:
            ctx.edit('🧰 Chưa có ổ dữ liệu/rclone — chạy setup trước…', force=True)
            self._setup_inline(ctx)
            probe = self.remote.probe()

        ctx.edit(f'📏 Đo dung lượng backup <code>{esc(folder)}</code>…', force=True)
        self.remote.push_script('03_size.sh', '/tmp/wm_03_size.sh', repl={'__RF__': folder})
        sz_out = self.remote.run_text('bash /tmp/wm_03_size.sh', timeout=180, allow_fail=True)
        total_bytes, total_count = 0, 0
        m = re.search(r'\{[^{}]*"bytes"[^{}]*\}', sz_out)
        if m:
            try:
                o = json.loads(m.group(0))
                total_bytes = self._int_safe(o.get('bytes'))
                total_count = self._int_safe(o.get('count'))
            except ValueError:
                pass
        if total_bytes <= 0:
            raise JobError(f'Thư mục {esc(folder)} không có dữ liệu parts/ (hoặc rclone lỗi). '
                           + esc(sz_out[-200:]))
        ctx.edit(f'📦 Tải backup: {fmt_bytes(total_bytes)} ({total_count} file)…', force=True)

        self.remote.start_script('04_dl.sh', '/tmp/wm_04_dl.sh', '/tmp/wm_dl.log',
                                 repl={'__RF__': folder})
        cancel_sent = [False]

        def _cancel_remote():
            if not cancel_sent[0]:
                cancel_sent[0] = True
                try:
                    self.remote.run_text("pkill -f 'rclone cop[y]' 2>/dev/null; echo OK",
                                         allow_fail=True)
                except Exception:
                    pass
            return 'Đã dừng rclone tải về.'

        ctx.remote_cancel = _cancel_remote
        dl_cmd = ('echo MARK_COUNT; ls /mnt/data/backup/parts 2>/dev/null | wc -l; '
                  'echo MARK_SIZE; du -sb /mnt/data/backup/parts 2>/dev/null | cut -f1; '
                  'echo MARK_LOG; tail -2 /tmp/restore_dl.log 2>/dev/null; '
                  'echo MARK_PROC; pgrep -c rclone 2>/dev/null || true; '
                  'echo M_D; (grep -q DL_ALL_DONE /tmp/wm_dl.log 2>/dev/null && echo 1) || echo 0; '
                  'echo M_OK; (grep -q DL_OK /tmp/wm_dl.log 2>/dev/null && echo 1) || echo 0')
        t0 = time.time()
        stall = 0
        dl_ok = False
        while True:
            out = self.remote.run_text(dl_cmd, timeout=90, allow_fail=True)
            b = marked_blocks(out, ['MARK_COUNT', 'MARK_SIZE', 'MARK_LOG', 'MARK_PROC',
                                    'M_D', 'M_OK'])
            cnt = self._int_safe(_first(b, 'MARK_COUNT'))
            byt = self._int_safe(_first(b, 'MARK_SIZE'))
            proc = self._int_safe(_first(b, 'MARK_PROC'))
            done = _first(b, 'M_D') == '1'
            ok = _first(b, 'M_OK') == '1'
            lastlog = ' '.join([l.strip() for l in (b.get('MARK_LOG') or []) if l.strip()])
            el = time.time() - t0
            pct = -1
            if total_bytes > 0:
                pct = min(int(round(byt / total_bytes * 100)), 100)
            title = '📥 <b>TẢI BACKUP TỪ ONEDRIVE</b>'
            if ctx.cancelled:
                title = '📥 <b>ĐANG HỦY TẢI…</b>' if done or proc <= 0 else '📥 <b>ĐANG DỪNG RCLONE…</b>'
            line1 = f"{fmt_bytes(byt)} / {fmt_bytes(total_bytes)}"
            if pct >= 0:
                line1 += f" ({pct}%)"
            line1 += f" • {cnt}/{total_count} file"
            ms = RX_SPD.search(lastlog)
            me = RX_ETA.search(lastlog)
            line2 = ' • '.join([x for x in (
                f"⏱ đã chạy {fmt_elapsed(el)}",
                ms.group(0) if ms else '',
                ('còn ' + me.group(1)) if me else '') if x])
            ctx.edit(f"{title}\n• {line1}\n{line2}", buttons=ui.job_kb())
            if done:
                if ok:
                    dl_ok = True
                    break
                if ctx.cancelled or cancel_sent[0]:
                    ctx.edit('⛔ Đã hủy tải về (hủy theo yêu cầu). Có thể chạy lại /restore để tiếp tục.',
                             force=True)
                    return
                tail = self.remote.tail('/tmp/restore_dl.log', 4)
                raise JobError('Tải về THẤT BẠI. Log: ' + esc(tail[-250:]))
            if not done and proc <= 0 and cnt <= 0 and el > 180:
                stall += 1
            else:
                stall = 0
            if stall >= 3:
                raise JobError('Tải về treo (rclone không chạy). Xem /tmp/restore_dl.log')
            if el > 21600:
                raise JobError('Tải về quá 6 giờ — dừng.')
            ctx.sleep(self.poll_sec)
        ctx.edit(f"✅ Tải xong {fmt_bytes(total_bytes)} ({total_count} file) "
                 f'trong {fmt_elapsed(time.time() - t0)}.', force=True)

        # --- ghép parts ---
        self.remote.start_script('05_assemble.sh', '/tmp/wm_05_assemble.sh', '/tmp/wm_assemble.log')
        ctx.edit('🧩 Đang ghép các part thành archive…', force=True)
        t0 = time.time()
        while True:
            out = self.remote.run_text(
                'echo MARK_SZ; stat -c %s /mnt/data/backup/windows-backup.tar.zst 2>/dev/null '
                '|| echo 0; echo M_D; (grep -qF AS_ALL_DONE /tmp/wm_assemble.log 2>/dev/null '
                '&& echo 1) || echo 0; echo M_OK; (grep -qF AS_OK /tmp/wm_assemble.log '
                '2>/dev/null && echo 1) || echo 0', timeout=60, allow_fail=True)
            b = marked_blocks(out, ['MARK_SZ', 'M_D', 'M_OK'])
            byt = self._int_safe(_first(b, 'MARK_SZ'))
            done = _first(b, 'M_D') == '1'
            ok = _first(b, 'M_OK') == '1'
            el = time.time() - t0
            ctx.edit(f'🧩 Ghép parts… {fmt_bytes(byt)}/{fmt_bytes(total_bytes)} ({fmt_elapsed(el)})',
                     buttons=ui.job_kb())
            if done:
                if not ok:
                    raise JobError('Ghép part THẤT BẠI — xem /tmp/wm_assemble.log')
                break
            if el > 7200:
                raise JobError('Ghép part quá 2h — dừng.')
            ctx.sleep(self.poll_sec)

        # --- verify ---
        self.remote.start_script('06_verify_arc.sh', '/tmp/wm_06_verify_arc.sh', '/tmp/wm_verify.log')
        ctx.edit('🔍 Kiểm tra toàn vẹn archive (zstd -t)…', force=True)
        ok = self._wait_marker(ctx, '/tmp/wm_verify.log', 'VA_ALL_DONE', 'VA_OK',
                               3600, self.poll_sec, 'kiểm tra archive (zstd -t)')
        if not ok:
            raise JobError('Archive bị lỗi (zstd -t thất bại).')
        if ctx.cancelled:
            ctx.edit('⛔ Bạn đã yêu cầu hủy, nhưng bước GIẢI NÉN không thể dừng an toàn — '
                     'sẽ chạy tiếp cho tới khi xong.', buttons=ui.job_kb(), force=True)

        # --- extract ---
        self.remote.start_script('07_extract.sh', '/tmp/wm_07_extract.sh', '/tmp/wm_extract.log',
                                 repl={'__RF__': folder})
        j = self.cfg.vm()
        target = max(1, int(j.get('disk_gb') or 450)) * (1024 ** 3)
        t0 = time.time()
        while True:
            out = self.remote.run_text(
                'echo MARK_SZ; stat -c %s /mnt/data/data.img 2>/dev/null || echo 0; '
                'echo M_D; (grep -qF EX_ALL_DONE /tmp/wm_extract.log 2>/dev/null && echo 1) || echo 0; '
                'echo M_OK; (grep -qF EX_OK /tmp/wm_extract.log 2>/dev/null && echo 1) || echo 0',
                timeout=60, allow_fail=True)
            b = marked_blocks(out, ['MARK_SZ', 'M_D', 'M_OK'])
            byt = self._int_safe(_first(b, 'MARK_SZ'))
            done = _first(b, 'M_D') == '1'
            ok = _first(b, 'M_OK') == '1'
            el = time.time() - t0
            pct = min(int(round(byt / target * 100)), 99)
            ctx.edit(f'📂 Giải nén vào /mnt/data… data.img {fmt_bytes(byt)} / ~{fmt_bytes(target)} '
                     f'({pct}%, {fmt_elapsed(el)})', buttons=ui.job_kb())
            if done:
                if not ok:
                    tail = self.remote.tail('/tmp/extract_err.log', 5)
                    raise JobError('Giải nén THẤT BẠI. Log: ' + esc(tail[-250:]))
                break
            if el > 14400:
                raise JobError('Giải nén quá 4h — dừng.')
            ctx.sleep(self.poll_sec)

        self.remote.run_text('sudo rm -rf /mnt/data/backup; echo OK', timeout=240, allow_fail=True)
        img = self.remote.get_int("stat -c %s /mnt/data/data.img 2>/dev/null || echo 0")
        ctx.edit(
            '✅ <b>HOÀN TẤT KHÔI PHỤC</b>\n'
            f"• Nguồn: <code>onedrive:{esc(folder)}</code>\n"
            f'• data.img: <b>{fmt_bytes(img)}</b> • đã dọn file tạm (~/mnt/data/backup)',
            buttons=kb([
                [('▶️ Bật VM ngay', 'vm:start'), ('💾 Backup ngay', 'bk:new')],
                [('📊 Trạng thái', 'home')]]),
            force=True)

    def _setup_inline(self, ctx):
        """Chạy 01+02 không cần job riêng (dùng trong restore)."""
        self.remote.start_script('01_setup.sh', '/tmp/wm_01_setup.sh', '/tmp/wm_setup.log')
        ok = self._wait_marker(ctx, '/tmp/wm_setup.log', 'ST_ALL_DONE', 'ST_OK',
                               600, 6, 'mount ổ dữ liệu + cài rclone')
        if not ok:
            raise JobError('Setup mount/rclone thất bại khi chuẩn bị restore.')
        b64 = base64.b64encode(self.cfg.get('onedrive_token').encode('utf-8')).decode('ascii')
        self.remote.start_script('02_rclone.sh', '/tmp/wm_02_rclone.sh', '/tmp/wm_rclone.log',
                                 repl={'__TOKB64__': b64})
        ok2 = self._wait_marker(ctx, '/tmp/wm_rclone.log', 'RCL_ALL_DONE', 'RCL_OK',
                                300, 6, 'nối OneDrive')
        if not ok2:
            raise JobError('Nối OneDrive thất bại khi chuẩn bị restore.')

    # ================= worker: VM =================
    def worker_vm_start(self, ctx):
        ctx.edit('▶️ <b>BẬT VM</b>…', force=True)
        self.remote.ensure_available(progress=lambda t: ctx.edit('▶️ ' + esc(t), buttons=ui.job_kb()))
        probe = self.remote.probe()
        cont = probe['container']
        if cont == 'running':
            ctx.edit('✅ VM đang chạy sẵn.', buttons=ui.job_kb(), force=True)
        elif cont in ('exited', 'created', 'paused'):
            ctx.edit('▶️ docker start windows…', force=True)
            self.remote.run_text('docker start windows 2>&1', timeout=120, allow_fail=True)
            t0 = time.time()
            while time.time() - t0 < 150:
                st = self.remote.run_text(
                    "docker inspect -f '{{.State.Running}}' windows 2>/dev/null || echo false").strip()
                if st == 'true':
                    break
                ctx.sleep(5)
            if st != 'true':
                ctx.edit('⚠️ Không start được container cũ — tạo lại container mới '
                         '(dữ liệu trong /mnt/data vẫn giữ nguyên)…', force=True)
                self._vm_recreate_inline(ctx)
        else:
            self._vm_recreate_inline(ctx)
        self._post_vm_up(ctx)

    def _vm_recreate_inline(self, ctx):
        j = self.cfg.vm()
        repl = {
            '__CORES__': str(j['cores']),
            '__RAM__': f"{j['ram_gb']}G",
            '__DISK__': f"{j['disk_gb']}G",
            '__USER__': str(j['user']).replace('\\', '\\\\').replace('"', '\\"')
                        .replace('$', '\\$').replace('`', '\\`'),
            '__PASS__': str(j['password']).replace('\\', '\\\\').replace('"', '\\"')
                        .replace('$', '\\$').replace('`', '\\`'),
        }
        self.remote.start_script('08_start_vm.sh', '/tmp/wm_08_start_vm.sh', '/tmp/wm_startvm.log',
                                 repl=repl)
        ok = self._wait_marker(ctx, '/tmp/wm_startvm.log', 'SV_ALL_DONE', 'SV_OK',
                               900, 10, 'tạo swap + docker run dockurr/windows')
        if not ok:
            tail = self.remote.tail('/tmp/wm_startvm.log', 6)
            raise JobError('Khởi động VM không hoàn tất. Log: ' + esc(tail[-350:]))

    def _post_vm_up(self, ctx):
        cs = self.cfg.get('codespace') or ''
        notes = []
        for port in ('8006', '3389'):
            done = False
            for _ in range(4):
                r = self.gh.ports_visibility(cs, f'{port}:public')
                if r.ok:
                    done = True
                    break
                ctx.sleep(8)
            notes.append(f'cổng {port}: ' + ('public ✅' if done else 'chưa mở được (thử lại bằng /ports)'))
        t0 = time.time()
        web = '000'
        while time.time() - t0 < 150:
            try:
                p = self.remote.probe()
                web = str(p.get('web') or '000')
            except Exception:
                web = '000'
            if web != '000':
                break
            ctx.sleep(6)
        web_link = f'https://{cs}-8006.app.github.dev' if cs else ''
        text = ('✅ <b>VM đã khởi động</b>\n• ' + ' • '.join(notes) +
                f'\n• Web 8006: <b>{esc(web)}</b>')
        rows = []
        if web_link and web != '000':
            rows.append([('🌐 Mở Web UI (8006)', web_link)])
        rows.append([('💾 Backup ngay', 'bk:new'), ('📊 Trạng thái', 'home')])
        ctx.edit(text, buttons=kb(rows), force=True)

    def worker_vm_stop(self, ctx):
        ctx.edit('⏸ <b>DỪNG VM</b>…', force=True)
        self.remote.ensure_available(progress=lambda t: ctx.edit(esc(t), buttons=ui.job_kb()))
        probe = self.remote.probe()
        if probe['lock_busy']:
            raise JobError('Đang backup — không nên dừng VM lúc này. Chờ backup xong.')
        if probe['container'] != 'running':
            ctx.edit('ℹ️ VM đang không chạy.', buttons=kb(
                [[('🖥 Quản lý VM', 'vm:menu')]]))
            return
        self.remote.run_text('docker stop -t 300 windows 2>&1', timeout=420, allow_fail=True)
        t0 = time.time()
        while time.time() - t0 < 420:
            st = self.remote.run_text(
                "docker inspect -f '{{.State.Status}}' windows 2>/dev/null || echo none").strip()
            if st not in ('running', 'restarting'):
                break
            ctx.sleep(5)
        ctx.edit('✅ Đã dừng VM an toàn (ACPI).', buttons=kb(
            [[('▶️ Bật lại', 'vm:start'), ('🖥 Quản lý VM', 'vm:menu')]]), force=True)

    def worker_vm_restart(self, ctx):
        ctx.edit('🔄 Khởi động lại container windows…', force=True)
        self.remote.ensure_available(progress=lambda t: ctx.edit(esc(t), buttons=ui.job_kb()))
        self.remote.run_text('docker restart -t 300 windows 2>&1', timeout=420, allow_fail=True)
        ctx.sleep(5)
        ctx.edit('✅ Đã restart container windows.', buttons=kb(
            [[('📊 Trạng thái', 'home')]]), force=True)

    def worker_vm_recreate(self, ctx):
        ctx.edit('♻️ Tạo lại container (giữ data.img)…', force=True)
        self.remote.ensure_available(progress=lambda t: ctx.edit(esc(t), buttons=ui.job_kb()))
        self._vm_recreate_inline(ctx)
        self._post_vm_up(ctx)

    def worker_vm_rm(self, ctx):
        ctx.edit('🗑 Xóa container windows…', force=True)
        self.remote.ensure_available(progress=lambda t: ctx.edit(esc(t), buttons=ui.job_kb()))
        self.remote.run_text('docker rm -f windows 2>&1 || true', timeout=180, allow_fail=True)
        ctx.edit('✅ Đã xóa container. Dùng /vm → "Tạo lại container" khi cần.',
                 buttons=kb(
                     [[('🖥 Quản lý VM', 'vm:menu')]]), force=True)

    def worker_ports(self, ctx):
        ctx.edit('🌐 Mở cổng public 8006 / 3389…', force=True)
        cs = self.cfg.get('codespace') or ''
        if not cs:
            raise JobError('Chưa chọn Codespace (/settings).')
        notes = []
        for port in ('8006', '3389'):
            ok = False
            for i in range(12):
                r = self.gh.ports_visibility(cs, f'{port}:public')
                if r.ok:
                    ok = True
                    break
                ctx.sleep(10)
            notes.append(f'cổng {port}: ' + ('✅ public' if ok else '❌ ' + esc((r.err or r.out)[-120:])))
        ctx.edit(f'🌐 Kết quả:\n• ' + '\n• '.join(notes) +
                 f'\n\nWeb UI: <code>https://{esc(cs)}-8006.app.github.dev</code>',
                 buttons=kb(
                     [[('🖥 Quản lý VM', 'vm:menu'), ('📊 Trạng thái', 'home')]]),
                 force=True)

    # ================= worker: cleanup / ssh =================
    def worker_cleanup(self, ctx):
        ctx.edit('🧹 <b>DỌN FILE TẠM</b>…', force=True)
        self.remote.ensure_available(progress=lambda t: ctx.edit(esc(t), buttons=ui.job_kb()))
        before = self.remote.probe().get('df_used') or 0
        out = self.remote.run_text(
            'sudo rm -rf /mnt/data/backup; '
            'rm -f /tmp/vm_backup_run.log /tmp/vm_backup_progress /tmp/vm_backup_busy '
            '/tmp/vm_backup_cancel /tmp/vm_upload.log /tmp/vm_check.log /tmp/tar_warn.log '
            '/tmp/extract_err.log /tmp/zstd_arc.log /tmp/restore_dl.log /tmp/rclone_about.log '
            '/tmp/wm_*.log /tmp/wm_*.sh 2>/dev/null; sync; df -h /mnt/data | tail -1',
            timeout=240, allow_fail=True)
        after = self.remote.probe().get('df_used') or 0
        freed = max(0, before - after)
        ctx.edit(f'🧹 Đã dọn xong. Giải phóng ~<b>{fmt_bytes(freed)}</b>.\n'
                 + (f'Ổ data: <code>{esc(out[-160:])}</code>' if out else ''),
                 buttons=kb(
                     [[('📊 Trạng thái', 'home')]]),
                 force=True)

    def worker_ssh(self, ctx, cmd):
        ctx.edit('📡 Đang chạy: <code>' + esc(cmd[:120]) + '</code>…', force=True)
        self.remote.ensure_available(progress=lambda t: ctx.edit(esc(t), buttons=ui.job_kb()))
        b64 = base64.b64encode(cmd.encode('utf-8')).decode('ascii')
        r = self.remote.run(f'echo {b64} | base64 -d | bash', timeout=240, allow_fail=True)
        out = (r.out or '') + (('\n' + r.err) if (r.err or '').strip() else '')
        out = out.strip()
        head = f'📡 <b>SSH</b> (exit {r.rc}) — <code>{esc(cmd[:80])}</code>'
        if not out:
            ctx.edit(head + '\n<i>(không có output)</i>', buttons=kb(
                [[('📊 Trạng thái', 'home')]]), force=True)
            return
        if len(out) <= 3200:
            ctx.edit(head + '\n<pre>' + esc(out[:3200]) + '</pre>',
                     buttons=kb(
                         [[('📊 Trạng thái', 'home')]]), force=True)
        else:
            ctx.edit(head + '\n(output dài — xem file bên dưới)',
                     buttons=kb(
                         [[('📊 Trạng thái', 'home')]]), force=True)
            self.tg.send_document(ctx.chat_id, 'ssh_output.txt', out.encode('utf-8'),
                                  caption=head)

    @staticmethod
    def _int_safe(v, default=0):
        try:
            return int(float(str(v).strip()))
        except (TypeError, ValueError):
            return default
