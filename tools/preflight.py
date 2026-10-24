# -*- coding: utf-8 -*-
"""Chuẩn hoá LF/no-BOM cho remote/*.sh + remote/*.yml + py_compile bot/*.py + quét CJK lẫn."""
import glob
import os
import py_compile
import sys

try:  # console Windows hay lỗi cp1252 khi in tiếng Việt
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

REMOTE_FILES = (sorted(glob.glob(os.path.join(ROOT, 'remote', '*.sh'))) +
                sorted(glob.glob(os.path.join(ROOT, 'remote', '*.yml'))))

changed = []
for p in REMOTE_FILES:
    b = open(p, 'rb').read()
    nb = b.replace(b'\r\n', b'\n')
    if nb.startswith(b'\xef\xbb\xbf'):
        nb = nb[3:]
    if nb != b:
        open(p, 'wb').write(nb)
        changed.append(os.path.basename(p))
print('remote sh/yml normalized:', changed or 'none')

left = []
for p in REMOTE_FILES:
    b = open(p, 'rb').read()
    if b.startswith(b'\xef\xbb\xbf') or b'\r\n' in b:
        left.append(os.path.basename(p))
print('remote sh/yml BOM/CRLF left:', left or 'none')

tabs = []
for p in sorted(glob.glob(os.path.join(ROOT, 'remote', '*.yml'))):
    if b'\t' in open(p, 'rb').read():
        tabs.append(os.path.basename(p))
print('yml tabs (YAML cấm tab thụt lề):', tabs or 'none')

errs = []
for p in sorted(glob.glob(os.path.join(ROOT, 'bot', '*.py'))):
    try:
        py_compile.compile(p, doraise=True)
    except Exception as e:
        errs.append((os.path.basename(p), str(e)))
print('py_compile:', 'OK' if not errs else errs)

cjk = []
for p in sorted(glob.glob(os.path.join(ROOT, 'bot', '*.py'))):
    s = open(p, encoding='utf-8').read()
    for i, ch in enumerate(s):
        if '\u4e00' <= ch <= '\u9fff':
            cjk.append((os.path.basename(p), s[:i].count('\n') + 1, ch))
print('CJK leftovers:', cjk or 'none')
