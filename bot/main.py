# -*- coding: utf-8 -*-
"""Điểm vào bot: Telegram long-poll điều khiển Codespace Windows VM.

Chạy trực tiếp:
    python -m bot.main
hoặc trên Colab:
    python colab.py

Env:
  TELEGRAM_BOT_TOKEN   token bot (bắt buộc khi TELEGRAM_MODE=real)
  TELEGRAM_MODE        'real' (mặc định) hoặc 'fake' (test local)
  TELEGRAM_FAKE_INBOX / TELEGRAM_FAKE_OUTBOX  file cho chế độ fake
  TELEGRAM_ADMIN_ID    tùy chọn: chat_id owner (thường để trống — /start tự nhận)
  DATA_DIR             nơi lưu bot-config.json (mặc định ./data)
  CONFIG_SYNC_REPO     repo private để tự sao lưu bot-config.json (tùy chọn)
  CONFIG_SYNC_PATH     đường dẫn file trong repo (mặc định bot-config.json)
  CONFIG_SYNC_TOKEN    token riêng để ghi repo sync (mặc định dùng github_token)
  GH_BIN               đường dẫn gh CLI (mặc định 'gh')
  WM_POLL_SEC          giây giữa các lần poll marker (mặc định 15; test giảm 0.5)
"""
import logging
import os
import signal
import sys
import time

from bot import cfg as cfgmod
from bot.engine import Engine
from bot.gh import GhCli
from bot.keepalive import Keepalive
from bot.remote import Remote
from bot.sync import ConfigPusher
from bot.tg import make_telegram


def resolve_data_dir():
    d = (os.environ.get('DATA_DIR') or '').strip()
    if d:
        return os.path.abspath(os.path.expanduser(d))
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(here, 'data')


def main():
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    log = logging.getLogger('bot')

    env = dict(os.environ)
    mode = (env.get('TELEGRAM_MODE') or 'real').strip().lower()
    if mode != 'fake' and not (env.get('TELEGRAM_BOT_TOKEN') or '').strip():
        sys.exit('Thiếu TELEGRAM_BOT_TOKEN')

    data_dir = resolve_data_dir()
    os.makedirs(data_dir, exist_ok=True)

    cfg = cfgmod.Config(os.path.join(data_dir, 'bot-config.json'))
    admin_env = (env.get('TELEGRAM_ADMIN_ID') or '').strip()
    if admin_env and not cfg.get('owner_chat_id'):
        try:
            cfg.set('owner_chat_id', int(admin_env))
        except ValueError:
            pass

    # Tự sao lưu cấu hình (token, codespace, owner...) lên repo private.
    # Nhờ vậy Colab bị thu hồi runtime vẫn khôi phục được ở lần chạy sau.
    sync_repo = (env.get('CONFIG_SYNC_REPO') or '').strip()
    sync_path = (env.get('CONFIG_SYNC_PATH') or 'bot-config.json').strip()
    sync_token = (env.get('CONFIG_SYNC_TOKEN') or '').strip()
    pusher = None
    if sync_repo:
        pusher = ConfigPusher(
            cfg, sync_repo, sync_path, log,
            token_getter=(lambda: sync_token) if sync_token else None)
        cfg.on_save = pusher.schedule
        log.info('config sync bật: %s/%s', sync_repo, sync_path)
    else:
        log.info('config sync tắt (không có CONFIG_SYNC_REPO)')

    log.info('data_dir=%s owner=%s codespace=%s',
             data_dir, cfg.get('owner_chat_id') or '(chưa nhận)',
             cfg.get('codespace') or '(chưa chọn)')

    tg = make_telegram(env)
    gh = GhCli(cfg, data_dir)
    remote = Remote(gh, cfg)
    engine = Engine(cfg, tg, gh, remote, None, log)
    ka = Keepalive(gh, cfg, log, notify=engine.notify_owner)
    engine.ka = ka
    ka.start()

    def _stop(signum, _frame):
        log.info('nhận signal %s — thoát', signum)
        try:
            ka.stop()
        except Exception:  # noqa
            pass
        if pusher is not None:
            try:
                pusher.flush(timeout=5)
            except Exception:  # noqa
                pass
        os._exit(0)

    for sig in ('SIGTERM', 'SIGINT'):
        try:
            signal.signal(getattr(signal, sig), _stop)
        except (ValueError, AttributeError):
            pass

    try:
        tg.set_my_commands()
    except Exception as e:  # noqa
        log.warning('setMyCommands lỗi: %r', e)

    log.info('bot bắt đầu — long polling')
    offset = None
    while True:
        try:
            ups = tg.get_updates(offset, timeout=50)
        except Exception as e:  # noqa
            log.warning('getUpdates lỗi: %r', e)
            time.sleep(3)
            continue
        for upd in ups:
            try:
                engine.handle_update(upd)
            except Exception:  # noqa
                log.exception('handle_update lỗi')
            offset = upd.get('update_id', 0) + 1


if __name__ == '__main__':
    main()
