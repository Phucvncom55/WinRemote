# -*- coding: utf-8 -*-
"""Tiện ích dùng chung (định dạng, thời gian, parse tiến trình rclone)."""
import html
import json
import re
import time
import uuid
from datetime import datetime, timezone, timedelta

VN_TZ = timezone(timedelta(hours=7))


def now_ts():
    return time.time()


def vn_time(ts=None, fmt='%d/%m %H:%M'):
    dt = datetime.fromtimestamp(ts if ts is not None else time.time(), VN_TZ)
    return dt.strftime(fmt)


def vn_stamp():
    return datetime.now(VN_TZ).strftime('%Y%m%d_%H%M%S')


def esc(s):
    return html.escape(str(s if s is not None else ''), quote=False)


def trunc(s, n=3400):
    s = str(s if s is not None else '')
    if len(s) <= n:
        return s
    return s[:n - 40] + f"\n…(đã cắt bớt {len(s) - n + 40} ký tự)"


def fmt_bytes(b):
    try:
        b = float(b)
    except (TypeError, ValueError):
        return '0 B'
    sign = '-' if b < 0 else ''
    b = abs(b)
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if b < 1024 or unit == 'TB':
            if unit == 'B':
                return f"{sign}{b:,.0f} B"
            return f"{sign}{b:,.1f} {unit}"
        b /= 1024.0
    return f"{sign}{b:,.1f} PB"


def fmt_elapsed(sec):
    try:
        sec = int(sec)
    except (TypeError, ValueError):
        sec = 0
    if sec < 0:
        sec = 0
    d, r = divmod(sec, 86400)
    h, r = divmod(r, 3600)
    m, s = divmod(r, 60)
    if d:
        return f"{d}d {h}h{m:02d}m"
    if h:
        return f"{h}h{m:02d}m{s:02d}s"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def fmt_age(ts):
    if not ts:
        return 'chưa rõ'
    d = time.time() - float(ts)
    if d < 5:
        return 'vừa xong'
    if d < 60:
        return f"{int(d)} giây trước"
    if d < 3600:
        return f"{int(d / 60)} phút trước"
    if d < 86400:
        return f"{int(d / 3600)} giờ trước"
    return f"{int(d / 86400)} ngày trước"


def fmt_speed(bps):
    return fmt_bytes(bps) + '/s'


def bar(pct, width=10):
    try:
        pct = max(0, min(100, int(round(float(pct)))))
    except (TypeError, ValueError):
        pct = 0
    fill = int(round(pct * width / 100.0))
    return '▰' * fill + '▱' * (width - fill) + f" {pct}%"


_RX_PCT = re.compile(r'(\d{1,3})%')
_RX_ETA = re.compile(r'ETA\s+([0-9hms.]+)')
_RX_SPD = re.compile(r'([\d.,]+)\s*([KMGTP]i?B/s)')
_RX_TRANSFERRED = re.compile(
    r'Transferred:\s*([\d.,]+\s*[KMGTP]?i?B|\d+)\s*/\s*([\d.,]+\s*[KMGTP]?i?B|\d+)')
_RX_ERRORS = re.compile(r'Errors:\s*(\d+)')


def parse_rclone_line(line):
    """Trích xuất thông tin từ 1 dòng stats của rclone (--stats-one-line)."""
    out = {}
    if not line:
        return out
    m = _RX_TRANSFERRED.search(line)
    if m:
        out['transferred'] = m.group(1).strip()
        out['total'] = m.group(2).strip()
    m = _RX_PCT.search(line)
    if m:
        out['pct'] = int(m.group(1))
    m = _RX_SPD.search(line)
    if m:
        out['speed'] = f"{m.group(1)} {m.group(2)}"
    m = _RX_ETA.search(line)
    if m:
        out['eta'] = m.group(1)
    m = _RX_ERRORS.search(line)
    if m:
        out['errors'] = int(m.group(1))
    return out


def new_id():
    return uuid.uuid4().hex[:12]


def jloads(s, default=None):
    if s is None:
        return default
    try:
        return json.loads(s)
    except (ValueError, TypeError):
        return default


def jdumps(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(',', ':'))


def to_int(v, default=0):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def sanity_filename(name, fallback='backup'):
    """Tên thư mục backup an toàn."""
    name = re.sub(r'[^A-Za-z0-9_.\-]+', '_', str(name or '')).strip('_.')
    return name[:80] or fallback
