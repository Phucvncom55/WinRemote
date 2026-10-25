# -*- coding: utf-8 -*-
"""Dựng nội dung tin nhắn HTML cho bot (thuần hiển thị, không gọi mạng)."""
import datetime

from bot.tg import kb
from bot.util import esc, fmt_bytes, fmt_elapsed, vn_time

DISCLAIMER = ('⚠️ <i>Lưu ý: chạy VM liên tục trong GitHub Codespaces có thể không đúng '
              'mục đích sử dụng của GitHub — tài khoản có nguy cơ bị hạn chế.</i>')

CS_STATE_VN = {
    'Available': 'Available ✅', 'Shutdown': 'Shutdown ⏸', 'Stopped': 'Stopped ⏸',
    'Starting': 'đang khởi động ⏳', 'Archived': 'Archived 📦',
    'NotFound': 'không tồn tại ❓', 'NoConfig': 'chưa cấu hình ⛔',
}

STAGE_VN = {
    'stop_vm': 'đang dừng VM an toàn (ACPI)',
    'tar': 'đang nén /mnt/data (tar + zstd)',
    'verify': 'đang kiểm tra archive (zstd -t)',
    'split': 'đang chia 1 GB/part',
    'upload': 'đang upload lên OneDrive',
    'check': 'đang xác minh remote (rclone check)',
    'restart': 'đang khởi động lại VM',
    'done': 'hoàn tất',
}

VM_STATE_VN = {'running': 'đang chạy 🟢', 'exited': 'đã dừng 🔴',
               'created': 'mới tạo ⚪', 'paused': 'tạm dừng ⏸', 'none': 'chưa tạo ⚪'}

ACT_STATE_VN = {'queued': 'đang xếp hàng ⏳', 'requested': 'đang xếp hàng ⏳',
                'waiting': 'đang xếp hàng ⏳', 'pending': 'đang xếp hàng ⏳',
                'in_progress': 'đang chạy 🟢', 'completed': 'đã kết thúc 🏁'}


def shorten(s, n=24):
    s = str(s)
    return s if len(s) <= n else s[:n - 1] + '…'


def usage_bar(used, total, width=10):
    """Thanh dung lượng một dòng: '▰▰▰▱▱▱▱▱▱▱ 32%'."""
    try:
        used = max(0, int(used or 0))
        total = max(0, int(total or 0))
    except (TypeError, ValueError):
        used, total = 0, 0
    if total <= 0:
        return '▱' * width + ' —'
    pct = min(100.0, used * 100.0 / total)
    filled = min(width, max(0, int(round(pct * width / 100.0))))
    return '▰' * filled + '▱' * (width - filled) + f' {pct:.0f}%'


def usage_line(used, total):
    """ '▰▰▰▱▱▱▱▱▱▱ 32% · 4.8 GB / 15 GB' """
    return (f'{usage_bar(used, total)} · '
            f'{fmt_bytes(used or 0)} / {fmt_bytes(total or 0)}')


def cs_port_url(cs, port):
    """URL cổng public của Codespace (https://<tên>-<cổng>.app.github.dev)."""
    cs = str(cs or '').strip()
    return f'https://{cs}-{port}.app.github.dev' if cs else ''


def _clock_hms(line):
    """Lấy 'HH:MM:SS' từ dòng timestamp kiểu `date` (UTC)."""
    for p in str(line or '').split():
        if len(p) == 8 and p.count(':') == 2:
            return p
    return ''


def act_remaining_txt(act, budget_min=350):
    """Chuỗi 'còn ~Xh Ym' cho run Actions (dựa vào createdAt, giờ UTC)."""
    try:
        t = datetime.datetime.strptime(str(act.get('created'))[:19], '%Y-%m-%dT%H:%M:%S')
    except Exception:
        return ''
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    left = budget_min - max(0.0, (now - t).total_seconds() / 60.0)
    if left <= 0:
        return 'sắp tới hạn ~6h'
    h, m = int(left // 60), int(left % 60)
    return f'còn ~{h}h{m:02d}m' if h else f'còn ~{max(1, m)} phút'


ACT_ACTIVE = ('queued', 'requested', 'waiting', 'pending', 'in_progress')


# ============================ màn hình chính ============================

def h_start(cfg, claimed=False):
    gh_ok = '✅' if cfg.has_github else '⛔'
    od_ok = '✅' if cfg.has_onedrive else '⛔'
    gd_ok = (f'✅ {len(cfg.gdrive_accounts)} tk') if cfg.gdrive_accounts else '⛔'
    cs_txt = shorten(cfg.get('codespace') or 'chưa chọn', 22)
    lines = ['🤖 <b>VM WinRemote Bot</b>',
             '<i>Windows VM trong GitHub Codespaces — điều khiển qua Telegram</i>', '',
             '📋 <b>TỔNG QUAN</b>',
             f'🐙 GitHub {gh_ok}　☁️ OneDrive {od_ok}　☁️ Drive {gd_ok}',
             f'🧩 Codespace: <code>{esc(cs_txt)}</code>']
    if cfg.has_onedrive and cfg.get('od_total'):
        lines.append('　└ ' + usage_line(cfg.get('od_used'), cfg.get('od_total')))
    if cfg.gdrive_accounts:
        tot, usd = cfg.gdrive_total()
        lines.append('　└ ' + usage_line(usd, tot) + ' · ổ gộp')
    lines += ['', '🚀 <b>LỆNH CHÍNH</b>',
              '/status · /backup · /restore · /setup · /quota · /repo',
              '/vm · /actions · /gdrive · /kalog · /ssh · /cleanup · /settings']
    if claimed:
        lines += ['', '👑 Bạn là <b>chủ sở hữu</b> bot này.']
    if not (cfg.has_github and cfg.has_onedrive and cfg.has_codespace):
        missing = []
        if not cfg.has_github:
            missing.append('GitHub token')
        if not cfg.has_onedrive:
            missing.append('OneDrive token')
        if not cfg.has_codespace:
            missing.append('chọn Codespace')
        lines += ['', '👉 Còn thiếu: <b>%s</b> — mở /settings.' % esc(', '.join(missing))]
    lines += ['', DISCLAIMER]
    return '\n'.join(lines)


def h_help():
    return ('📖 <b>HƯỚNG DẪN NHANH</b>\n'
            '1️⃣ /settings — nhập GitHub token (quyền codespace) + đăng nhập OneDrive tự động + chọn Codespace.\n'
            '2️⃣ /setup — mount ổ dữ liệu + cài rclone + nối OneDrive trên Codespace.\n'
            '3️⃣ /restore — tải backup từ OneDrive về (hoặc bỏ qua nếu đã có data.img).\n'
            '4️⃣ /vm — bật/dừng VM (Web UI 8006 + RDP 3389 tự mở khi máy chạy).\n'
            '\n'
            '💾 /backup — backup thủ công (dừng VM an toàn → nén → chia part 1GB → upload).\n'
            '♻️ /restore — chọn thư mục data_backup* mới nhất để khôi phục.\n'
            '📦 /repo — chọn repo có sẵn, tạo repo mới, hoặc nhập owner/repo.\n'
            '☁️ /gdrive — gom nhiều tài khoản Google Drive thành 1 ổ lớn (backup/restore).\n'
            '📡 /ssh &lt;lệnh&gt; — chạy bash trong Codespace.\n'
            '🧹 /cleanup — dọn file tạm (~88GB sau restore).\n'
            '🖥 /actions — mở máy Windows tạm trên GitHub Actions (RDP qua Tailscale, ~6 tiếng).\n'
            '🔄 /kalog — xem log keepalive + test SSH đúng Codespace.\n'
            '✖️ /cancel — hủy việc đang chạy.\n'
            '\n' + DISCLAIMER)


def account_text(cfg, ka_snap):
    gh_tok = cfg.get('github_token') or ''
    od_tok = cfg.get('onedrive_token') or ''
    vm = cfg.vm()
    lines = ['👤 <b>TÀI KHOẢN & CẤU HÌNH</b>', '']
    lines.append(f"👑 Chat chủ sở hữu: <code>{esc(cfg.get('owner_chat_id'))}</code>")
    lines.append(f"🐙 GitHub: <b>{esc(cfg.get('github_login') or '(chưa xác minh)')}</b> "
                 f"— token {'✅' if gh_tok else '⛔'} <code>{esc((gh_tok[:6] + '…') if gh_tok else '—')}</code>")
    if cfg.get('od_owner'):
        lines.append(f"☁️ OneDrive: <b>{esc(cfg.get('od_owner'))}</b>")
        lines.append('　└ ' + usage_line(cfg.get('od_used'), cfg.get('od_total')))
    else:
        lines.append(f"☁️ OneDrive: {'✅ token đã lưu' if od_tok else '⛔ chưa có token'}"
                     f" — <i>{'/settings → OneDrive' if not od_tok else 'chưa xác minh dung lượng'}</i>")
    if cfg.gdrive_accounts:
        tot, usd = cfg.gdrive_total()
        lines.append(f"☁️ Google Drive: <b>{len(cfg.gdrive_accounts)} tài khoản</b> · ổ gộp")
        lines.append('　└ ' + usage_line(usd, tot))
    lines.append(f"🧩 Codespace: <code>{esc(cfg.get('codespace') or '(chưa chọn)')}</code>")
    lines.append(f"🖥 VM: {vm['cores']} cores • {vm['ram_gb']}G RAM • {vm['disk_gb']}G disk • "
                 f"Windows {esc(vm['windows_version'])} • user <b>{esc(vm['user'])}</b>")
    ka_state = ka_snap.get('state') if ka_snap else 'off'
    lines.append(f"🔄 Keepalive: <b>{esc(ka_state)}</b> (mỗi {cfg.get('keepalive_interval')}s)")
    return '\n'.join(lines), kb([
        [('🔑 GitHub token', 'set:gh'), ('☁️ OneDrive', 'set:od')],
        [('☁️ Google Drive', 'set:gdrive'), ('🏠 Trang chính', 'home')],
    ])


# ============================ trạng thái ============================

def status_text(cfg, cs_state, probe, ka_snap, job_info, act=None, quota=None):
    lines = [f"📊 <b>TRẠNG THÁI HỆ THỐNG</b> — {vn_time(fmt='%H:%M %d/%m')}"]
    if not cfg.has_codespace:
        lines.append('🧩 Codespace: <b>chưa chọn</b> — vào /settings trước.')
        return '\n'.join(lines)
    lines.append(f"🧩 Codespace: <code>{esc(cfg.get('codespace'))}</code> — "
                 f"<b>{CS_STATE_VN.get(cs_state, cs_state)}</b>")
    qb = quota_brief(quota)
    if qb:
        lines.append(qb)
    if probe:
        cont = VM_STATE_VN.get(probe.get('container', 'none'), probe.get('container'))
        web = str(probe.get('web') or '000')
        web_ok = '✅' if web in ('200', '302', '401', '403') else ('⌛' + web if web == '000' else '⚠️ ' + web)
        lines.append(f"🖥 VM: <b>{esc(cont)}</b> • restart {probe.get('restarts', 0)} • "
                     f"Web <b>{web_ok}</b> • RDP {'✅' if probe.get('rdp') else '❌'}")
        lines.append(f"💽 data.img: <b>{fmt_bytes(probe.get('img_bytes') or 0)}</b>")
        df = str(probe.get('df') or '').strip()
        if df:
            lines.append(f"🗄 Ổ /mnt/data: <code>{esc(df)}</code>")
        lines.append(f"🔗 rclone: {'✅' if probe.get('conf') else '❌ chưa cấu hình (chạy /setup)'} • "
                     f"mount data: {'✅' if probe.get('mount') else '❌'}"
                     + (' • Drive gộp ✅' if probe.get('gdrive') else '')
                     + (' • ⏳ <b>đang backup</b>' if probe.get('lock_busy') else ''))
    if cfg.get('od_owner'):
        lines.append(f"☁️ OneDrive: {esc(cfg.get('od_owner'))}")
        lines.append('　└ ' + usage_line(cfg.get('od_used'), cfg.get('od_total')))
    if cfg.gdrive_accounts:
        tot, usd = cfg.gdrive_total()
        lines.append(f"☁️ Google Drive: {len(cfg.gdrive_accounts)} tk"
                     + (' • <b>đích backup</b>' if (cfg.get('backup_backend') or '') == 'gdrive' else ''))
        lines.append('　└ ' + usage_line(usd, tot))
    if act and act.get('status') in ACT_ACTIVE:
        st = str(act.get('status'))
        st_txt = ACT_STATE_VN.get(st, st)
        if st == 'in_progress':
            r = act_remaining_txt(act)
            extra = (' • ' + esc(r)) if r else ''
        else:
            extra = ' • chờ máy nhận việc…'
        lines.append(f"☁️ Máy Actions: <b>{esc(st_txt)}</b>{extra}")
        if act.get('ip'):
            lines.append(f"　└ IP Tailscale: <code>{esc(act.get('ip'))}</code> (RDP user trongphuc)")
    if ka_snap:
        kst = ka_snap.get('state')
        tm = _clock_hms(ka_snap.get('last_line'))
        extra = f" • cập nhật {tm}" if tm else ''
        lines.append(f"🔄 Keepalive: <b>{esc(kst)}</b>{extra}")
        kt = ka_snap.get('target') or ''
        if kt and cfg.get('codespace') and kt != cfg.get('codespace'):
            lines.append(f"　└ ⚠️ đang trỏ <code>{esc(kt)}</code> — bấm 📜 KA log → "
                         'Mở lại phiên SSH')
        if ka_snap.get('last_error'):
            lines.append(f"　└ ⚠️ {esc(str(ka_snap.get('last_error'))[:130])}")
    if job_info:
        lines.append(f"⚙️ Đang chạy: <b>{esc(job_info['name'])}</b> ({job_info['elapsed']}s) — /cancel để hủy")
    return '\n'.join(lines)


def status_kb(cfg, cs_state, probe, act=None):
    alt = '☁️ Máy Actions'
    if act and act.get('status') in ACT_ACTIVE:
        alt += ' 🟢'
    rows = [[('🔄 Làm mới', 'st:r'), ('📊 Quota', 'q:show'), ('📦 Repo', 'set:repo')],
            [('⚙️ Cài đặt', 'set:menu'), ('📜 KA log', 'ka:log')]]
    if cfg.has_codespace:
        if cs_state == 'Available':
            rows.append([('⏹ Dừng Codespace', 'st:csstop')])
            rows.append([('💾 Backup', 'bk:new'), ('📥 Restore', 'rst:scan'), ('🧹 Dọn rác', 'cl:menu')])
            rows.append([('🖥 Quản lý VM', 'vm:menu'), (alt, 'act:menu')])
        else:
            rows.append([('▶️ Bật Codespace', 'st:csstart')])
            rows.append([('💾 Backup', 'bk:new'), ('📥 Restore', 'rst:scan')])
            rows.append([('🧹 Dọn rác', 'cl:menu'), (alt, 'act:menu')])
    if cfg.has_github and not cfg.has_codespace:
        rows.append([('🆕 Tạo Codespace mới', 'set:csnew')])
    if not (cfg.has_github and cfg.has_onedrive):
        rows.append([('🔑 Nhập token còn thiếu', 'set:menu')])
    return kb(rows)


# ============================ cài đặt ============================

def settings_text(cfg):
    vm = cfg.vm()
    ka = 'BẬT 🟢' if cfg.get('keepalive_enabled') else 'TẮT ⚪'
    backend = 'Google Drive' if (cfg.get('backup_backend') or '') == 'gdrive' else 'OneDrive'
    lines = ['🔧 <b>CÀI ĐẶT</b>', '', '<b>KẾT NỐI</b>']
    gh = '✅ đã lưu' if cfg.has_github else '⛔ chưa có'
    if cfg.get('github_login'):
        gh = f"✅ <b>{esc(cfg.get('github_login'))}</b>"
    lines.append('🐙 GitHub　' + gh)
    if cfg.has_onedrive:
        od = '✅ đã kết nối'
        if cfg.get('od_owner'):
            od = f"✅ <b>{esc(shorten(cfg.get('od_owner'), 28))}</b>"
        lines.append('☁️ OneDrive　' + od)
        if cfg.get('od_total'):
            lines.append('　└ ' + usage_line(cfg.get('od_used'), cfg.get('od_total')))
    else:
        lines.append('☁️ OneDrive　⛔ chưa kết nối')
    n_gd = len(cfg.gdrive_accounts)
    if n_gd:
        tot, usd = cfg.gdrive_total()
        lines.append(f'☁️ Google Drive　✅ <b>{n_gd} tài khoản</b> · ổ gộp')
        lines.append('　└ ' + usage_line(usd, tot))
    else:
        lines.append('☁️ Google Drive　⛔ chưa có tài khoản')
    lines.append(f'📦 Đích backup　<b>{backend}</b>')
    lines.append('')
    lines.append('<b>HẠ TẦNG</b>')
    lines.append(f"🧩 Codespace　<code>{esc(shorten(cfg.get('codespace') or '(chưa chọn)', 34))}</code>")
    if cfg.get('cs_repo'):
        lines.append(f"📁 Repo　<code>{esc(shorten(cfg.get('cs_repo'), 34))}</code>"
                     + (' · Tailscale ✅' if cfg.get('tailscale_key') else ''))
    lines.append(f"🔄 Keepalive　<b>{ka}</b> · mỗi {cfg.get('keepalive_interval')}s")
    lines.append(f"🖥 VM　{vm['cores']} cores · {vm['ram_gb']}G RAM · {vm['disk_gb']}G · "
                 f"Windows {esc(vm['windows_version'])}")
    return '\n'.join(lines)


def settings_kb(cfg):
    vm = cfg.vm()
    ka = 'BẬT' if cfg.get('keepalive_enabled') else 'TẮT'
    return kb([
        [('🔑 GitHub token', 'set:gh'), ('☁️ OneDrive', 'set:od')],
        [(f"☁️ Google Drive ({len(cfg.gdrive_accounts)} tk)", 'set:gdrive')],
        [(f"🧩 Codespace: {shorten(cfg.get('codespace') or 'chọn…', 18)}", 'set:cs'),
         ('📁 Repo', 'set:repo')],
        [('🆕 Tạo Codespace mới (4c/16G/32G)', 'set:csnew')],
        [(f"🔄 Keepalive: {ka}", 'set:ka'),
         (f"⏱ {cfg.get('keepalive_interval')}s", 'set:kaint')],
        [('📜 Log keepalive', 'ka:log'), ('🧪 Test keepalive', 'ka:test')],
        [(f"🖥 VM: {vm['cores']}c/{vm['ram_gb']}G/{vm['disk_gb']}G", 'set:vmmenu'),
         ('📊 Quota', 'q:show')],
        [('👤 Tài khoản', 'set:acc'), ('🖥 VM Actions', 'act:menu'), ('🏠 Trang chính', 'home')],
    ])


def repo_text(cfg):
    repo = cfg.get('cs_repo') or ''
    login = cfg.get('github_login') or ''
    lines = ['📦 <b>REPO CHO CODESPACE & ACTIONS</b>', '']
    if repo:
        lines.append(f"• Đang dùng: <code>{esc(repo)}</code>")
    else:
        lines.append('• Đang dùng: <b>chưa đặt</b> (bot tự tạo <code>winvm-codespace</code> khi cần)')
    if login:
        lines.append(f"• Tài khoản GitHub: <b>{esc(login)}</b>")
    lines.append('• Repo private được dùng để tạo Codespace và chạy workflow Windows Actions.')
    lines.append('• Đổi repo <b>không</b> xóa Codespace cũ — chỉ đổi đích cho lần tạo/chạy sau.')
    lines.append('')
    lines.append('Chọn thao tác bên dưới:')
    return '\n'.join(lines)


def repo_kb(cfg):
    return kb([
        [('🔍 Chọn repo có sẵn', 'set:repol')],
        [('🆕 Tạo repo mới (private)', 'set:repon'), ('✏️ Nhập owner/repo', 'set:repoi')],
        [('🖥 VM Actions', 'act:menu'), ('⚙️ Cài đặt', 'set:menu')],
        [('🏠 Trang chính', 'home')],
    ])


def repo_list_text(repos, cfg):
    lines = ['📦 <b>CHỌN REPO</b>', '']
    if not repos:
        lines.append('Không lấy được danh sách repo (kiểm tra GitHub token).')
        return '\n'.join(lines)
    cur = (cfg.get('cs_repo') or '').lower()
    for i, row in enumerate(repos[:10]):
        name = str(row.get('nameWithOwner') or row.get('name') or '?')
        vis = str(row.get('visibility') or '').lower()
        mark = '✅' if name.lower() == cur else ('🔒' if vis == 'private' else '🌐')
        lines.append(f"　{i + 1}. {mark} <code>{esc(name)}</code>")
    lines.append('')
    lines.append('Chọn repo bên dưới:')
    return '\n'.join(lines)


def repo_list_kb(repos):
    rows = []
    for i, row in enumerate(repos[:10]):
        name = str(row.get('nameWithOwner') or row.get('name') or '?')
        rows.append([(f"{i + 1}. {shorten(name, 34)}", f'set:repop:{i}')])
    rows.append([('⬅️ Repo', 'set:repo'), ('🏠 Trang chính', 'home')])
    return kb(rows)


# ============================ OneDrive ============================

def onedrive_text(cfg):
    lines = ['☁️ <b>ONEDRIVE</b>', '']
    if cfg.has_onedrive:
        if cfg.get('od_owner'):
            lines.append(f'✅ Đã kết nối · <b>{esc(cfg.get("od_owner"))}</b>')
            if cfg.get('od_total'):
                lines.append('　└ ' + usage_line(cfg.get('od_used'), cfg.get('od_total')))
        else:
            lines.append('✅ Token đã lưu (chưa xác minh dung lượng)')
    else:
        lines.append('⛔ Chưa kết nối')
    lines += ['', '🔗 <b>Đăng nhập bằng link</b> — không cần cài gì trên máy bạn:',
              '1️⃣ Bấm nút bên dưới → mở trang đăng nhập Microsoft.',
              '2️⃣ Nhập mã 9 ký tự bot đưa và đăng nhập tài khoản.',
              '3️⃣ Bấm <b>Đồng ý / Accept</b> để cấp quyền OneDrive.',
              '　 Bot tự nhận token và lưu lại (có refresh_token, dùng lâu dài).',
              '',
              '<i>📋 Tùy chọn nâng cao: dán JSON từ</i> <code>rclone authorize onedrive</code> '
              '<i>nếu tài khoản của bạn bị tổ chức chặn đăng nhập thiết bị.</i>']
    return '\n'.join(lines)


def onedrive_kb(cfg):
    rows = [[('🔗 Đăng nhập OneDrive (link + mã)', 'set:odlogin')]]
    if cfg.has_onedrive:
        rows.append([('🔄 Kiểm tra / làm mới token', 'set:odcheck'),
                     ('📤 Ghi lên Codespace', 'set:odpush')])
    rows.append([('📋 Dán token JSON (nâng cao)', 'set:odmanual')])
    rows.append([('⚙️ Cài đặt', 'set:menu'), ('🏠 Trang chính', 'home')])
    return kb(rows)


def onedrive_login_text(start, note=''):
    start = start or {}
    uri = start.get('verification_uri') or start.get('verification_uri_complete') or \
        'https://login.microsoftonline.com/device'
    code = start.get('user_code') or ''
    mins = int((start.get('expires_in') or 900) / 60)
    lines = ['🔗 <b>ĐĂNG NHẬP ONEDRIVE BẰNG LINK</b>', '']
    lines.append('1️⃣ Bấm nút <b>Mở trang đăng nhập Microsoft</b> bên dưới')
    lines.append(f'　 hoặc mở: {esc(uri)}')
    lines.append('')
    lines.append('2️⃣ Nhập mã:')
    lines.append(f'<code>{esc(code)}</code>')
    lines.append('')
    lines.append('3️⃣ Đăng nhập và bấm <b>Đồng ý / Accept</b> để cấp quyền '
                 'OneDrive (Files.ReadWrite.All).')
    lines.append('')
    lines.append(f'⏳ Bot đang tự chờ tối đa <b>{mins} phút</b> — xong là lưu token ngay. '
                 '<i>/cancel để hủy.</i>')
    if note:
        lines.append('')
        lines.append(note)
    return '\n'.join(lines)


def onedrive_login_kb(uri):
    rows = []
    if uri:
        rows.append([('🌐 Mở trang đăng nhập Microsoft', uri)])
    rows.append([('⛔ Hủy đăng nhập', 'job:cancel')])
    return kb(rows)


# ============================ Google Drive (nhiều tài khoản) ============================

def gdrive_used_label(cfg):
    return 'Google Drive' if (cfg.get('backup_backend') or 'onedrive') == 'gdrive' \
        and cfg.has_gdrive else 'OneDrive'


def gdrive_text(cfg):
    accs = cfg.gdrive_accounts
    total, used = cfg.gdrive_total()
    backend = (cfg.get('backup_backend') or 'onedrive')
    lines = ['☁️ <b>GOOGLE DRIVE — GỘP NHIỀU TÀI KHOẢN</b>', '']
    lines.append(f'• Số tài khoản: <b>{len(accs)}</b>')
    if accs:
        lines.append('• Ổ gộp: <code>%s:</code> — một ổ lớn cho backup/restore'
                     % esc(cfg.get('gdrive_remote') or 'gdrive'))
        lines.append('• Tổng dung lượng: ' + usage_line(used, total))
    lines.append(f"• Đang dùng để backup: <b>{'Google Drive' if backend == 'gdrive' else 'OneDrive'}</b>")
    lines.append('• OAuth client: ' + ('riêng ✅' if cfg.get('gdrive_client_id')
                                      else 'mặc định (rclone) ⚠️'))
    lines.append('')
    if accs:
        lines.append('📋 <b>Danh sách tài khoản</b>')
        for i, a in enumerate(accs[:10], 1):
            lines.append(f"　{i}. {esc(shorten(a.get('owner') or a.get('name') or '?', 36))}")
            lines.append('　　└ ' + usage_line(a.get('used'), a.get('total')))
        if len(accs) > 10:
            lines.append(f'　… và {len(accs) - 10} tài khoản khác')
        lines.append('')
        lines.append('💡 Mỗi tài khoản là một remote riêng (gdrive1, gdrive2, …); bot gộp thành '
                     '<code>gdrive:</code>. File backup được chia lên tài khoản còn nhiều chỗ '
                     'trống, restore đọc lại xuyên suốt các tài khoản.')
    else:
        lines.append('<i>Chưa có tài khoản nào.</i>')
        lines.append('')
        lines.append('🔗 Bấm <b>Thêm tài khoản bằng link</b>: bot đưa link đăng nhập Google, '
                     'bạn đồng ý quyền rồi dán lại link redirect — không cần cài rclone.')
        lines.append('')
        lines.append('🎯 Nhiều tài khoản 15 GB được gộp thành một ổ lớn để chứa backup VM.')
    return '\n'.join(lines)


def gdrive_kb(cfg):
    rows = [[('🔗 Thêm tài khoản bằng link', 'gd:add')],
            [('🔑 OAuth client riêng', 'gd:client')]]
    if cfg.has_gdrive:
        rows.append([('🧪 Kiểm tra & ghi cấu hình', 'gd:test')])
        cur = (cfg.get('backup_backend') or 'onedrive')
        target = 'OneDrive' if cur == 'gdrive' else 'Google Drive'
        rows.append([(f'☁️ Dùng {target} để backup', 'gd:use')])
        del_row = []
        for i, a in enumerate(cfg.gdrive_accounts[:6]):
            del_row.append((f"🗑 {i + 1}", f'gd:del:{i}'))
        if del_row:
            rows.append(del_row)
    rows.append([('📋 Dán token JSON', 'gd:paste'), ('❓ Hướng dẫn', 'gd:help')])
    rows.append([('⚙️ Cài đặt', 'set:menu'), ('🏠 Trang chính', 'home')])
    return kb(rows)


def gdrive_link_text(url):
    lines = ['🔗 <b>THÊM TÀI KHOẢN GOOGLE DRIVE</b>', '',
             '1️⃣ Bấm nút <b>Mở trang đăng nhập Google</b> bên dưới.',
             '2️⃣ Chọn đúng tài khoản Google → bấm <b>Cho phép / Allow</b>.',
             '3️⃣ Trình duyệt chuyển tới địa chỉ dạng',
             '　 <code>http://127.0.0.1:53682/?code=…</code>',
             '　 và báo <i>không truy cập được</i> — bình thường, bot không chạy web trên máy bạn.',
             '4️⃣ Copy <b>toàn bộ địa chỉ</b> trên thanh địa chỉ (hoặc phần <code>code=…</code>) '
             'rồi dán vào chat.',
             '',
             '⏳ Bot chờ tối đa 10 phút · /cancel để hủy.',
             '🔁 Lặp lại cho từng tài khoản muốn gộp.',
             '',
             '<i>Bot chỉ dùng quyền Drive để sao lưu; token được lưu trong cấu hình riêng tư '
             'và tự làm mới.</i>']
    if url and len(url) > 60:
        lines += ['', '<i>Nếu nút không mở được, dùng lệnh /gdrive → "📋 Dán token JSON" '
                      'hoặc mở link trên máy có trình duyệt.</i>']
    return '\n'.join(lines)


def gdrive_link_kb(url):
    rows = []
    if url:
        rows.append([('🌐 Mở trang đăng nhập Google', url)])
    rows.append([('📋 Dán token JSON (nâng cao)', 'gd:paste')])
    rows.append([('❌ Hủy', 'set:gdrive')])
    return kb(rows)


def gdrive_help_text():
    return ('🔑 <b>THÊM TÀI KHOẢN GOOGLE DRIVE</b>\n\n'
            '<b>Cách 1 — bằng link (khuyến nghị)</b>\n'
            '1. /gdrive → <b>🔗 Thêm tài khoản bằng link</b>.\n'
            '2. Bấm nút mở trang đăng nhập → chọn tài khoản → <b>Cho phép</b>.\n'
            '3. Trình duyệt chuyển tới <code>http://127.0.0.1:53682/?code=…</code> và báo '
            'không truy cập được (bình thường).\n'
            '4. Copy toàn bộ địa chỉ đó, dán lại vào chat — bot tự đổi thành token và lưu.\n\n'
            '<b>Cách 2 — dán JSON từ rclone (nâng cao)</b>\n'
            '1. Trên máy có trình duyệt, cài rclone: https://rclone.org/downloads/\n'
            '2. Chạy <code>rclone authorize "drive"</code> → đăng nhập → <b>Cho phép</b>.\n'
            '3. Copy chuỗi JSON terminal in ra, dán vào mục '
            '<b>📋 Dán token JSON</b>.\n\n'
            '🔁 Làm lại với từng tài khoản Google muốn gộp.\n'
            '💡 Token có <code>refresh_token</code> nên dùng lâu dài; bot tự làm mới khi cần.\n'
            '⚠️ Nếu Google báo <i>The provided client secret is invalid</i>: mở '
            '<b>🔑 OAuth client riêng</b> để dùng client của bạn (client chung của rclone '
            'đang bị Google ngừng trong 2026).\n'
            '⚠️ Chỉ dán trong chat riêng với bot và nên xóa tin nhắn chứa token sau đó.')


def gdrive_add_text():
    return ('📋 <b>DÁN TOKEN GOOGLE DRIVE (JSON)</b>\n\n'
            'Gửi <b>chuỗi JSON</b> (tin nhắn tiếp theo), lấy bằng lệnh:\n'
            '<code>rclone authorize "drive"</code>\n\n'
            'Chuỗi cần có <code>access_token</code> và <code>refresh_token</code>. '
            'Bot sẽ kiểm tra dung lượng rồi lưu tài khoản.\n'
            '• Gõ /cancel để bỏ qua.')


def gdrive_client_text(cfg):
    cid = (cfg.get('gdrive_client_id') or '').strip()
    lines = ['🔑 <b>OAUTH CLIENT GOOGLE DRIVE</b>', '']
    if cid:
        lines.append(f'• Client ID: <code>{esc(cid)}</code>')
        lines.append('• Client secret: '
                     + ('đã lưu ✅' if cfg.get('gdrive_client_secret') else 'chưa có ❌'))
    else:
        lines.append('• Đang dùng client mặc định của rclone ⚠️')
        lines.append('• Google đang ngừng client này trong 2026. Nếu khi thêm tài khoản bot báo '
                     '<i>The provided client secret is invalid</i> thì cần client riêng.')
    lines += ['', '📋 <b>Tạo OAuth client riêng (miễn phí, ~5 phút)</b>',
              '1. Mở <a href="https://console.cloud.google.com/apis/credentials">Google Cloud '
              'Console → Credentials</a>.',
              '2. Tạo project mới (hoặc chọn project có sẵn).',
              '3. <b>APIs &amp; Services → Library</b> → bật <b>Google Drive API</b>.',
              '4. <b>OAuth consent screen</b> → External → điền tên, thêm email của bạn vào '
              '<b>Test users</b>, rồi bấm <b>Publish app</b>.',
              '5. <b>Create credentials → OAuth client ID → Desktop app</b>.',
              '6. Copy <b>Client ID</b> và <b>Client secret</b>, dán vào 2 nút bên dưới.',
              '',
              '💡 Desktop app tự chấp nhận loopback <code>127.0.0.1:53682</code> — không cần '
              'thêm redirect URI. Token cũ vẫn làm mới bằng client đã tạo ra nó.',
              '⚠️ Để app ở chế độ <i>Testing</i>, refresh token chỉ sống 7 ngày — nhớ Publish.',
              '🔐 Secret chỉ lưu trong bot-config.json (repo secrets private).']
    return '\n'.join(lines)


def gdrive_client_kb(cfg):
    rows = [[('✏️ Nhập Client ID', 'gd:clientid')],
            [('🔑 Nhập Client secret', 'gd:clientsec')]]
    if cfg.get('gdrive_client_id') or cfg.get('gdrive_client_secret'):
        rows.append([('♻️ Dùng client mặc định (rclone)', 'gd:clientreset')])
    rows.append([('⬅️ Google Drive', 'set:gdrive')])
    return kb(rows)


def backup_target_text(remote, label, has_gdrive):
    txt = f"☁️ Đích backup: <b>{esc(label)}</b> (<code>{esc(remote)}:</code>)"
    if has_gdrive:
        txt += '\n　└ bấm nút "Đích" để đổi giữa OneDrive và Google Drive gộp'
    return txt


# ============================ keepalive ============================

def _since_txt(ts):
    if not ts:
        return '—'
    try:
        d = int(max(0, datetime.datetime.now().timestamp() - float(ts)))
    except Exception:
        return '—'
    if d < 60:
        return f'{d}s'
    if d < 3600:
        return f'{d // 60}m{d % 60:02d}s'
    return f'{d // 3600}h{(d % 3600) // 60:02d}m'


KA_LOG_PER_PAGE = 16
KA_LOG_LINE_CHARS = 150


def _age_txt(ts):
    """Tuổi của một mốc thời gian: '1407 phút trước (84429s)'."""
    if not ts:
        return 'chưa có'
    try:
        d = int(max(0, datetime.datetime.now().timestamp() - float(ts)))
    except (TypeError, ValueError):
        return 'chưa rõ'
    if d < 60:
        label = f'{d} giây'
    elif d < 3600:
        label = f'{d // 60} phút'
    else:
        label = f'{d // 3600} giờ {(d % 3600) // 60:02d} phút'
    return f'{label} trước ({d}s)'


def keepalive_log_slice(raw, page=0, per_page=KA_LOG_PER_PAGE):
    """Cắt log thành trang (trang 0 = mới nhất).

    Trả (các dòng của trang, trang đã kẹp, tổng số trang, tổng số dòng).
    """
    raw = [str(x) for x in (raw or [])]
    total = len(raw)
    pages = max(1, (total + per_page - 1) // per_page)
    try:
        page = int(page or 0)
    except (TypeError, ValueError):
        page = 0
    page = min(max(0, page), pages - 1)
    end = total - page * per_page
    start = max(0, end - per_page)
    return raw[start:end], page, pages, total


def keepalive_text(cfg, snap, page=0, quota=None, cs_state=None):
    snap = snap or {}
    raw = list(snap.get('log_lines') or snap.get('lines') or [])
    chunk, page, pages, total = keepalive_log_slice(raw, page)
    lines = ['🔄 <b>KEEPALIVE — NHẬT KÝ & CHẨN ĐOÁN</b>', '']
    enabled = bool(cfg.get('keepalive_enabled'))
    lines.append(f"• Trạng thái: <b>{esc(snap.get('state') or 'off')}</b>"
                 f" • keepalive: {'BẬT 🟢' if enabled else 'TẮT ⚪'}"
                 f" • chu kỳ {cfg.get('keepalive_interval')}s")
    cs = cfg.get('codespace') or ''
    if cs_state:
        lines.append(f"• Codespace: <code>{esc(cs)}</code> — "
                     f"<b>{esc(CS_STATE_VN.get(cs_state, cs_state))}</b>")
    target = snap.get('target') or cs or '(chưa có)'
    if snap.get('pid'):
        lines.append(f"• Codespace đích: <code>{esc(target)}</code> — đang mở phiên SSH")
    else:
        lines.append(f"• Codespace đích: <code>{esc(target)}</code> — chưa mở phiên SSH")
    if cs and target and cs != target:
        lines.append('　└ ⚠️ KHÁC codespace đang chọn — bấm <b>Mở lại phiên</b> để trỏ đúng.')
    host = snap.get('host') or ''
    if host:
        match = '✅' if (cs and (cs in host or host in cs)) else 'ℹ️'
        lines.append(f"• Hostname máy đích: <code>{esc(host)}</code> {match}")
        if cs and not (cs in host or host in cs):
            lines.append('　└ ℹ️ Hostname Codespace thường có thêm hậu tố — kiểm tra dòng '
                         '<code>KA_HOST</code> trong log bên dưới.')
    pid = snap.get('pid') or 0
    since = snap.get('since') or 0
    live = bool(pid) and str(snap.get('state') or '') in ('running', 'đang kết nối…')
    try:
        if live and since:
            session_sec = int(max(0, datetime.datetime.now().timestamp() - float(since)))
        else:
            session_sec = int(snap.get('last_session_sec') or 0)
    except (TypeError, ValueError):
        session_sec = 0
    sess_label = 'Phiên hiện tại' if live else 'Phiên gần nhất'
    beats = snap.get('beats') or 0
    attempts = snap.get('attempts') or 0
    lines.append(f"• Output cuối: {_age_txt(snap.get('last_output_ts'))}"
                 f" • {sess_label}: {session_sec}s ({fmt_elapsed(session_sec)})")
    lines.append(f"• pid {pid if pid else '—'}"
                 f" • restart {snap.get('manual_restarts') or 0}"
                 f" • kết nối lại {max(0, int(attempts) - 1)}"
                 f" • {total} dòng log")
    if beats:
        lines.append(f"• Nhịp tim: <b>{beats}</b>")
    if snap.get('last_line'):
        lines.append(f"• Dòng cuối: <code>{esc(str(snap.get('last_line'))[:120])}</code>")
    if snap.get('last_rc') is not None:
        lines.append(f"• Mã thoát SSH gần nhất: <code>{esc(snap.get('last_rc'))}</code>")
    if snap.get('last_error'):
        lines.append(f"• Lỗi cuối: {esc(str(snap.get('last_error'))[:220])}")
    else:
        lines.append('• Lỗi cuối: —')
    if snap.get('command'):
        lines.append(f"• Lệnh: <code>{esc(str(snap.get('command'))[:160])}</code>")
    if quota:
        qb = quota_brief(quota)
        if qb:
            lines.append(qb)
    lines.append('')
    lines.append(f"📜 <b>LOG KEEPALIVE</b> • trang {page + 1}/{pages} • {total} dòng")
    if chunk:
        shown = [str(x)[:KA_LOG_LINE_CHARS] for x in chunk]
        lines.append('<pre>' + esc('\n'.join(shown)) + '</pre>')
        if pages > 1:
            lines.append(f'<i>Trang {page + 1}/{pages} — dùng nút ◀️/▶️ để xem toàn bộ log '
                         'ngay trong chat (không cần tải file).</i>')
    else:
        lines.append('<i>Chưa có log — keepalive chưa kết nối SSH lần nào. '
                     'Bấm "Test keepalive" để kiểm tra ngay.</i>')
    lines.append('')
    lines.append('💡 "Test keepalive" mở một phiên SSH mới tới đúng Codespace đang chọn. '
                 '"Dừng Codespace" tắt máy đang chạy — keepalive tự tạm nghỉ, '
                 'bật lại máy là tự chạy tiếp.')
    return '\n'.join(lines)


def keepalive_kb(cfg, page=0, pages=1, cs_state=None):
    ka = 'TẮT' if cfg.get('keepalive_enabled') else 'BẬT'
    rows = [
        [('🔁 Làm mới log', 'ka:log'), ('🧪 Test keepalive', 'ka:test')],
        [('♻️ Mở lại phiên SSH', 'ka:restart')],
    ]
    if cfg.has_codespace:
        if cs_state == 'Available':
            rows[-1].append(('⏹ Dừng Codespace', 'ka:stop'))
        elif cs_state in ('Shutdown', 'Stopped'):
            rows[-1].append(('▶️ Bật Codespace', 'ka:start'))
        else:
            rows.append([('⏹ Dừng Codespace', 'ka:stop'),
                         ('▶️ Bật Codespace', 'ka:start')])
    rows.append([(f"🔄 {ka} keepalive", 'set:ka'), ('🗑 Xóa log', 'ka:clear')])
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(('⏪ Mới nhất', 'ka:p:0'))
            nav.append(('▶️ Mới hơn', f'ka:p:{page - 1}'))
        if page < pages - 1:
            nav.append(('◀️ Cũ hơn', f'ka:p:{page + 1}'))
            nav.append(('⏩ Cũ nhất', f'ka:p:{pages - 1}'))
        if nav:
            rows.append(nav)
    rows.append([('📊 Hạn mức', 'q:show'), ('⚙️ Cài đặt', 'set:menu'),
                 ('🏠 Trang chính', 'home')])
    return kb(rows)



def keepalive_test_text(cfg, snap, res):
    snap = snap or {}
    res = res or {}
    lines = ['🧪 <b>TEST KEEPALIVE</b>', '']
    lines.append(f"• Codespace đang chọn: <code>{esc(cfg.get('codespace') or '(chưa chọn)')}</code>")
    lines.append(f"• Keepalive đích: <code>{esc(snap.get('target') or '(chưa chạy)')}</code>")
    if snap.get('target') and cfg.get('codespace') and snap.get('target') != cfg.get('codespace'):
        lines.append('　└ ⚠️ Phiên keepalive đang trỏ Codespace KHÁC — bấm "Mở lại phiên SSH".')
    ok = bool(res.get('ok'))
    lines.append(f"• Kết quả SSH: {'✅ OK' if ok else '❌ LỖI'} (rc={esc(res.get('rc'))})")
    out = str(res.get('out') or '').strip()
    if out:
        lines.append('')
        lines.append('<pre>' + esc(out[:1500]) + '</pre>')
    if res.get('error'):
        lines.append('')
        lines.append('⚠️ ' + esc(str(res.get('error'))[:500]))
    if ok:
        m = None
        for ln in out.splitlines():
            if ln.strip().startswith('KA_TARGET='):
                m = ln.strip().split('=', 1)[1]
        lines.append('')
        if m:
            lines.append(f"✅ Lệnh chạy trên máy <code>{esc(m)}</code>. Nếu log keepalive có dòng "
                         '<code>ssh&gt; KA_BEAT …</code> tăng đều thì keepalive đang hoạt động.')
        else:
            lines.append('✅ Lệnh được chạy trên đúng Codespace đang chọn. Hãy xem log keepalive '
                         'để chắc chắn có dòng <code>ssh&gt; KA_BEAT …</code>.')
    else:
        lines.append('')
        lines.append('💡 Kiểm tra: GitHub token còn hạn/đủ scope <code>codespace</code>, '
                     'SSH key đã đăng ký (bot tự làm), Codespace đang <b>Available</b>.')
    return '\n'.join(lines)


def keepalive_test_kb():
    return kb([
        [('📜 Log keepalive', 'ka:log'), ('🧪 Test lại', 'ka:test')],
        [('📊 Hạn mức', 'q:show'), ('⚙️ Cài đặt', 'set:menu')],
        [('🏠 Trang chính', 'home')],
    ])


# ============================ quota ============================

def _hm(hours):
    hours = max(0.0, float(hours or 0))
    h = int(hours)
    m = int(round((hours - h) * 60))
    if m >= 60:
        h += 1
        m = 0
    return f'{h} giờ {m:02d} phút'


def _days(days):
    days = max(0.0, float(days or 0))
    d = int(days)
    rem = (days - d) * 24
    h = int(rem)
    m = int(round((rem - h) * 60))
    if m >= 60:
        h += 1
        m = 0
    if h >= 24:
        d += 1
        h = 0
    return f'{d} ngày {h} giờ {m:02d} phút'


def quota_brief(q):
    """Một dòng ngắn về thời gian Codespace còn chạy được (dùng cho /status)."""
    if not q or q.get('error') and not q.get('login'):
        return ''
    left = q.get('total_hours_left')
    if left is None:
        left = q.get('run_hours_left')
    if left is None:
        return ''
    txt = f"⏳ Hạn mức còn: <b>{_hm(left)}</b>"
    if left < 48:
        txt += f" (~{max(0.1, left / 24):.1f} ngày chạy 24/7)"
    if q.get('days_to_reset') is not None:
        txt += f" • làm mới sau {q.get('days_to_reset')} ngày"
    return txt


def quota_text(q):
    q = q or {}
    lines = ['📊 <b>HẠN MỨC CODESPACE CÒN LẠI</b>', '']
    if q.get('error') and not q.get('login'):
        lines.append('⛔ ' + esc(str(q.get('error'))[:300]))
        return '\n'.join(lines)
    login = q.get('login') or '(chưa rõ)'
    lines.append(f"👤 Tài khoản: <b>{esc(login)}</b> — gói <b>{esc(q.get('plan') or 'Free')}</b>")
    cores = q.get('cores') or 0
    ram = q.get('ram_gb') or 0
    disk = q.get('disk_gb') or 0
    if cores or disk:
        lines.append(f"⚙️ Máy: <b>{cores} vCPU</b> • {ram} GB RAM • ổ đĩa {disk} GB"
                     + (f" • <code>{esc(q.get('machine'))}</code>" if q.get('machine') else ''))
    lines.append('')

    inc_core = float(q.get('included_core_hours') or 0)
    used_core = q.get('used_core_hours')
    run_left = q.get('run_hours_left')
    lines.append('🖥 <b>Thời gian CHẠY máy</b> (tính theo số vCPU):')
    if used_core is None:
        lines.append('• Chưa lấy được số đã dùng từ GitHub.')
    else:
        extra = f' (≈ {_hm(float(used_core) / cores)})' if cores else ''
        lines.append(f'• Đã dùng <b>{float(used_core):.2f} / {inc_core:.0f} giờ nhân lõi</b>{extra}')
    if run_left is not None:
        lines.append(f'• Còn lại: <b>{_hm(run_left)}</b>')
    lines.append('')

    inc_st = float(q.get('included_storage_gb_month') or 0)
    used_st = q.get('used_storage_gb_month')
    lines.append('💾 <b>Thời gian TREO máy</b> (theo dung lượng ổ đĩa, kể cả khi VM dừng):')
    if used_st is None:
        lines.append('• Chưa lấy được dung lượng lưu trữ đã dùng từ GitHub.')
    else:
        lines.append(f'• Lưu trữ đã dùng <b>{float(used_st):.2f} / {inc_st:.0f} GB-tháng</b>')
    if q.get('storage_days_left') is not None and disk:
        lines.append(f'• Với ổ đĩa {disk} GB: còn khoảng <b>{_days(q.get("storage_days_left"))}</b>')
    lines.append('')

    total = q.get('total_hours_left')
    if total is None:
        total = q.get('run_hours_left')
    if total is not None:
        lines.append('⏱️ <b>TỔNG THỜI GIAN SỬ DỤNG THỰC TẾ CÒN LẠI:</b>')
        lines.append(f'👉 <b>{_hm(total)}</b>')
        if total < 240:
            lines.append(f'　└ ~<b>{max(0.1, total / 24):.1f} ngày</b> nếu bật máy chạy 24/7')
        lim = q.get('limited_by') or 'thời gian chạy máy (vCPU)'
        lines.append(f'　└ giới hạn bởi {esc(lim)}')
        if q.get('days_to_reset') is not None and q.get('reset_txt'):
            lines.append(f'　└ hạn mức làm mới ngày <b>{esc(q.get("reset_txt"))}</b> '
                         f'(còn {q.get("days_to_reset")} ngày)')
        lines.append('')
    if q.get('run_hours_left') is not None and q.get('storage_days_left') is not None:
        st_hours = float(q.get('storage_days_left') or 0) * 24.0
        if st_hours < float(q.get('run_hours_left') or 0):
            lines.append('⚠️ Lưu trữ sẽ hết <b>trước</b> thời gian chạy máy — dọn backup cũ '
                         'hoặc tăng dung lượng ổ để dùng hết hạn mức.')
            lines.append('')
    if q.get('updated'):
        lines.append(f"🕒 Cập nhật: {esc(q.get('updated'))}")
    if q.get('source') == 'api':
        lines.append('ℹ️ Số liệu GitHub cập nhật định kỳ mỗi giờ (có thể chậm hơn một chút).')
    else:
        lines.append('ℹ️ Chưa đọc được billing API nên phần "đã dùng" có thể thiếu. '
                     'Hạn mức Free mặc định: 120 giờ nhân lõi + 15 GB-tháng.')
        if q.get('error'):
            lines.append(f"⚠️ {esc(str(q.get('error'))[:250])}")
        lines.append('💡 Token GitHub cần scope <code>user</code> (hoặc quyền đọc billing) '
                     'để lấy số liệu chính xác.')
    return '\n'.join(lines)


def quota_kb(q=None):
    q = q or {}
    rows = [[('🔄 Làm mới', 'q:show'), ('📖 Cách tính', 'q:help')],
            [('✏️ Nhập số đã dùng (thủ công)', 'q:set')],
            [('🌐 Trang billing GitHub', 'https://github.com/settings/billing')]]
    rows.append([('⚙️ Cài đặt', 'set:menu'), ('🏠 Trang chính', 'home')])
    return kb(rows)


def quota_set_text():
    return ('✏️ <b>NHẬP SỐ LIỆU ĐÃ DÙNG</b>\n\n'
            'Mở https://github.com/settings/billing → mục <b>Codespaces</b>, xem '
            '"Core hours used" và "Storage used".\n\n'
            'Gửi tin nhắn dạng: <code>&lt;giờ nhân lõi&gt; &lt;GB-tháng&gt;</code>\n'
            'Ví dụ: <code>6.17 0.15</code>\n'
            '(Có thể chỉ gửi số giờ nhân lõi.)\n\n'
            'Bot sẽ tính thời gian còn lại theo số bạn nhập.\n'
            '• Gõ /cancel để bỏ qua.')


def quota_help_text(cfg):
    return ('📖 <b>CÁCH TÍNH HẠN MỨC CODESPACE</b>\n\n'
            '• GitHub Free cho <b>120 giờ nhân lõi/tháng</b> (core-hours). '
            'Máy 4 vCPU dùng 1 giờ thực tế = 4 giờ nhân lõi, nên chạy liên tục được ~30 giờ/tháng.\n'
            '• Lưu trữ Free: <b>15 GB-tháng</b>. Ổ 32 GB treo cả tháng sẽ tốn 32 GB-tháng, '
            'nên chỉ giữ được ~14 ngày nếu dùng hết 15 GB-tháng.\n'
            '• Bot đọc số đã dùng từ GitHub Billing API (usage summary) và quy đổi theo '
            'số vCPU/ổ đĩa của Codespace đang chọn.\n'
            '• Có thể chỉnh hạn mức trong <code>bot-config.json</code>: '
            f"<code>quota_core_hours={cfg.get('quota_core_hours')}</code>, "
            f"<code>quota_storage_gb_month={cfg.get('quota_storage_gb_month')}</code>.\n\n"
            '⚠️ Đây là ước tính theo hạn mức Free; nếu tài khoản có gói trả phí, '
            'hạn mức thực tế có thể khác.')





def vmconf_text(cfg):
    vm = cfg.vm()
    return ('🖥 <b>CẤU HÌNH VM</b> (áp dụng khi Tạo lại container — dữ liệu giữ nguyên)\n\n'
            f"• Số nhân CPU: <b>{vm['cores']}</b>\n"
            f"• RAM: <b>{vm['ram_gb']} GB</b>\n"
            f"• Dung lượng đĩa: <b>{vm['disk_gb']} GB</b>\n"
            f"• Windows: <b>{esc(vm['windows_version'])}</b>\n"
            f"• Tài khoản: <b>{esc(vm['user'])}</b> • mật khẩu: <code>••••••</code>\n\n"
            'Chạm để sửa từng mục:')


def vmconf_kb(cfg):
    vm = cfg.vm()
    return kb([
        [(f"🔢 CPU: {vm['cores']}", 'set:vmf:cores'), (f"🧠 RAM: {vm['ram_gb']}G", 'set:vmf:ram')],
        [(f"💽 Disk: {vm['disk_gb']}G", 'set:vmf:disk'), (f"🪟 Win: {vm['windows_version']}", 'set:vmf:ver')],
        [(f"👤 User: {shorten(vm['user'], 12)}", 'set:vmf:user'), ('🔑 Đổi mật khẩu', 'set:vmf:pass')],
        [('⬅️ Cài đặt', 'set:menu'), ('🏠 Trang chính', 'home')],
    ])


# ============================ VM ============================

def vm_text(cfg, probe, web_url=''):
    lines = [f"🖥 <b>QUẢN LÝ VM</b> — {vn_time(fmt='%H:%M %d/%m')}"]
    if probe:
        cont = VM_STATE_VN.get(probe.get('container', 'none'), probe.get('container'))
        web = str(probe.get('web') or '000')
        web_ok = '✅' if web in ('200', '302', '401', '403') else ('⌛' if web == '000' else f'⚠️ {web}')
        lines.append(f"• Container: <b>{esc(cont)}</b> (restart {probe.get('restarts', 0)})")
        lines.append(f"• Web 8006: <b>{web_ok}</b> • RDP 3389: {'✅' if probe.get('rdp') else '❌'}")
        lines.append(f"• data.img: <b>{fmt_bytes(probe.get('img_bytes') or 0)}</b>")
        df = str(probe.get('df') or '').strip()
        if df:
            lines.append(f"• Ổ /mnt/data: <code>{esc(df)}</code>")
        if probe.get('lock_busy'):
            lines.append('• ⏳ đang backup — không nên dừng VM lúc này.')
    else:
        lines.append('• Codespace không Available — bật Codespace rồi thử lại.')
    if web_url:
        lines.append('')
        lines.append(f"🌐 Web UI: <code>{esc(web_url)}</code>")
    return '\n'.join(lines)


def vm_kb(web_url=''):
    rows = [
        [('▶️ Bật / khởi động VM', 'vm:start')],
        [('⏸ Dừng VM (an toàn)', 'vm:stop'), ('🔄 Khởi động lại', 'vm:restart')],
        [('♻️ Tạo lại container (giữ data)', 'vm:recreate'), ('🗑 Xóa container', 'vm:rm')],
    ]
    if web_url:
        rows.append([('🌐 Mở Web UI (8006)', web_url)])
    rows.append([('☁️ VM Actions (RDP/Tailscale)', 'act:menu')])
    rows.append([('🏠 Trang chính', 'home')])
    return kb(rows)


def vm_confirm_text(action, cfg):
    if action == 'recreate':
        return ('♻️ <b>Tạo lại container windows?</b>\n\n'
                '• Container cũ sẽ bị xóa và tạo mới bằng image <code>dockurr/windows</code>.\n'
                '• Đĩa <code>data.img</code> trong /mnt/data <b>được giữ nguyên</b>.\n'
                f"• Cấu hình: {cfg.vm()['cores']} cores • {cfg.vm()['ram_gb']}G RAM • "
                f"{cfg.vm()['disk_gb']}G disk • Windows {esc(cfg.vm()['windows_version'])}.\n"
                '• VM sẽ khởi động lại vài phút.')
    if action == 'rm':
        return ('🗑 <b>Xóa container windows?</b>\n\n'
                '• Container bị xóa hẳn; muốn dùng lại phải "Tạo lại container".\n'
                '• Dữ liệu trong <code>data.img</code> vẫn còn (không mất).')
    return 'Xác nhận?'


def vm_confirm_kb(action):
    go = 'vm:recgo' if action == 'recreate' else 'vm:rmgo'
    return kb([[('✅ Tiếp tục', go), ('❌ Hủy', 'vm:menu')]])


# ============================ Restore ============================

def restore_list_text(folders):
    lines = ['📥 <b>KHÔI PHỤC TỪ ONEDRIVE / GOOGLE DRIVE</b>', '']
    if not folders:
        lines.append('Không thấy thư mục <code>data_backup*</code> nào trên cloud.')
        return '\n'.join(lines)
    lines.append('Chọn bản backup để khôi phục:')
    for i, f in enumerate(folders[:8]):
        lines.append(f"　{i + 1}. <code>{esc(_fld(f))}</code> — {esc(_flabel(f))}")
    lines.append('')
    lines.append('⚠️ Khôi phục sẽ <b>ghi đè</b> dữ liệu VM hiện tại.')
    return '\n'.join(lines)


def _fld(x):
    return x.get('folder') if isinstance(x, dict) else str(x)


def _flabel(x):
    if isinstance(x, dict):
        return x.get('label') or ('Google Drive' if x.get('remote') != 'onedrive' else 'OneDrive')
    return 'OneDrive'


def _fremote(x):
    if isinstance(x, dict):
        return x.get('remote') or 'onedrive'
    return 'onedrive'


def restore_list_kb(folders):
    rows = []
    for i in range(0, min(len(folders), 8), 2):
        row = [('📁 ' + shorten(_fld(folders[i]), 18), f'rst:pk:{i}')]
        if i + 1 < min(len(folders), 8):
            row.append(('📁 ' + shorten(_fld(folders[i + 1]), 18), f'rst:pk:{i + 1}'))
        rows.append(row)
    rows.append([('🔄 Quét lại cloud', 'rst:scan'), ('🏠 Trang chính', 'home')])
    return kb(rows)


def restore_confirm_text(folder, img_bytes, remote_label='OneDrive'):
    remote = _fremote(folder)
    name = _fld(folder)
    label = remote_label or _flabel(folder)
    lines = ['📥 <b>XÁC NHẬN KHÔI PHỤC</b>', '',
             f"📦 Nguồn: <code>{esc(remote)}:{esc(name)}/parts/</code> — <b>{esc(label)}</b>"]
    if img_bytes > 0:
        lines.append(f"⚠️ Codespace đang có <b>data.img {fmt_bytes(img_bytes)}</b> — "
                     'quá trình khôi phục sẽ <b>GHI ĐÈ</b> bằng bản trên cloud.')
    lines.append('')
    lines.append('Quy trình: tải parts → ghép archive → kiểm tra zstd → giải nén vào /mnt/data '
                 '(tự dừng VM để an toàn) → dọn file tạm.')
    lines.append('⏱ Có thể mất 20–60 phút tuỳ dung lượng mạng.')
    return '\n'.join(lines)


def restore_confirm_kb(idx):
    return kb([[('📥 Bắt đầu tải & khôi phục', f'rst:go:{idx}'), ('❌ Hủy', 'home')]])


# ============================ Backup ============================

def backup_confirm_text(cfg, keep_local, restart_vm, probe, remote='onedrive',
                        remote_label='OneDrive', can_toggle=False):
    lines = ['💾 <b>XÁC NHẬN BACKUP</b>', '']
    if probe:
        lines.append(f"• VM container: <b>{esc(VM_STATE_VN.get(probe.get('container'), probe.get('container')))}</b>"
                     f" • data.img: <b>{fmt_bytes(probe.get('img_bytes') or 0)}</b>")
        df = str(probe.get('df') or '').strip()
        if df:
            lines.append(f"• Ổ data: <code>{esc(df)}</code>")
        if probe.get('lock_busy'):
            lines.append('• ⏳ <b>Đang có backup khác chạy</b> — chờ xong rồi hãy chạy.')
    lines.append('')
    lines.append(backup_target_text(remote, remote_label, can_toggle))
    lines.append('')
    lines.append('Quy trình: dừng VM an toàn (ACPI) → nén toàn bộ /mnt/data bằng zstd → '
                 'chia 1GB/part → upload '
                 f'<code>{esc(remote)}:data_backup_&lt;ngày giờ&gt;/parts/</code> '
                 '→ rclone check → bật lại VM.')
    lines.append('')
    lines.append('💡 Có thể hủy an toàn ở ranh giới từng bước.')
    return '\n'.join(lines)


def backup_confirm_kb(keep_local, restart_vm, remote='onedrive', remote_label='OneDrive',
                      can_toggle=False):
    local_txt = 'BẬT' if keep_local else 'TẮT'
    restart_txt = 'BẬT' if restart_vm else 'TẮT'
    rows = [
        [(f"🗄 Giữ bản local: {local_txt}", f"bk:l:{0 if keep_local else 1}"),
         (f"⏯ Tự bật lại VM: {restart_txt}", f"bk:r:{0 if restart_vm else 1}")],
    ]
    if can_toggle:
        rows.append([(f"☁️ Đích: {remote_label} (đổi)", 'bk:t')])
    rows.append([('💾 Backup ngay', 'bk:go'), ('❌ Hủy', 'home')])
    return kb(rows)


# ============================ Cleanup / khác ============================

def cleanup_confirm_text(cfg, probe):
    lines = ['🧹 <b>DỌN FILE TẠM TRÊN CODESPACE</b>', '']
    lines.append('Sẽ xóa: <code>/mnt/data/backup</code> (parts + archive tạm), '
                 'các log <code>/tmp/vm_*</code>, <code>/tmp/restore_dl.log</code>…')
    if probe:
        df = str(probe.get('df') or '').strip()
        if df:
            lines.append(f"• Ổ data hiện tại: <code>{esc(df)}</code>")
    lines.append('')
    lines.append('💡 Không ảnh hưởng dữ liệu VM (data.img) và backup trên OneDrive.')
    return '\n'.join(lines)


def cleanup_kb():
    return kb([[('🧹 Dọn ngay', 'cl:go'), ('❌ Hủy', 'home')]])


# ============================ VM Actions (GitHub Actions) ============================

def actions_text(cfg, act=None):
    lines = [f"🖥 <b>VM WINDOWS TRÊN GITHUB ACTIONS</b> — {vn_time(fmt='%H:%M %d/%m')}", '']
    if act and act.get('status'):
        st = str(act.get('status'))
        st_txt = ACT_STATE_VN.get(st, st)
        if st == 'completed':
            st_txt += f" ({esc(act.get('conclusion') or 'không rõ kết quả')})"
        line = f"• Run #{esc(act.get('id') or '?')}: <b>{esc(st_txt)}</b>"
        if st in ACT_ACTIVE:
            r = act_remaining_txt(act)
            if r:
                line += ' • ' + esc(r)
        lines.append(line)
        if act.get('ip'):
            lines.append(f"• IP Tailscale: <code>{esc(act.get('ip'))}</code> — RDP user <b>trongphuc</b>")
        elif st == 'in_progress':
            lines.append('• IP Tailscale: ⏳ chưa thấy — máy đang khởi động, thử lại sau 1-2 phút.')
    else:
        lines.append('• Chưa có run nào gần đây — bấm 🚀 để khởi động.')
    win = str(cfg.get('actions_windows') or 'windows-2025')
    ver = '2025' if '2025' in win else '2022'
    lines.append('')
    lines.append(f"• Phiên bản Windows: <b>Windows Server {ver}</b> (<code>{esc(win)}</code>)")
    lines.append('• Hỗ trợ: <b>Windows 2025</b> và <b>Windows 2022</b> — đổi bằng nút bên dưới.')
    lines.append('• Máy GitHub-hosted <b>4 vCPU / 16 GB RAM</b> • vào bằng <b>RDP qua Tailscale</b> • '
                 'sống tối đa <b>~6 tiếng</b>, hết giờ tự kết thúc.')
    lines.append(f"• Repo: <code>{esc(cfg.get('cs_repo') or '(sẽ tạo khi chạy)')}</code>")
    lines.append(f"• Tailscale key: {'✅ đã lưu' if cfg.get('tailscale_key') else '⛔ chưa có (bấm Khởi động sẽ được hỏi)'}")
    lines.append('💡 Token GitHub cần scope <code>workflow</code> để bot chạy workflow.')
    return '\n'.join(lines)


def actions_kb(cfg=None):
    win = str((cfg.get('actions_windows') if cfg else '') or 'windows-2025')
    on25 = '✅ ' if '2025' in win else ''
    on22 = '✅ ' if '2022' in win else ''
    return kb([
        [('🚀 Khởi động VM Actions', 'act:go')],
        [('🔄 Trạng thái run', 'act:st'), ('🕘 Hủy run', 'act:cancel')],
        [(on25 + '🪟 Windows 2025', 'act:ver:windows-2025'),
         (on22 + '🪟 Windows 2022', 'act:ver:windows-2022')],
        [('📦 Đổi repo', 'set:repo')],
        [('🖥 Quản lý VM', 'vm:menu'), ('🏠 Trang chính', 'home')],
    ])


def job_kb():
    return kb([[('⛔ Hủy việc đang chạy', 'job:cancel')]])


def busy_text(job_info):
    return (f"⏳ Đang chạy <b>{esc(job_info['name'])}</b> ({job_info['elapsed']}s). "
            'Chờ xong hoặc hủy trước khi chạy việc mới.')
