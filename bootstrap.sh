#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════
#  WinRemote — bootstrap 1 dòng cho Google Colab / Linux
#
#  Luôn tải/cập nhật repo rồi mới chạy run.sh, tránh kẹt bản cũ do cache
#  của raw.githubusercontent.com. Thêm ?v=N vào URL để ép cache mới.
#
#  Cách dùng: đặt PAT chỉ-đọc của repo secrets vào WM_GITHUB_TOKEN,
#  hoặc lưu PAT vào Colab Secrets cùng tên rồi dùng lệnh gọn:
#      bash <(curl -fsSL 'https://raw.githubusercontent.com/Phucvncom55/WinRemote/main/bootstrap.sh?v=1')
#
#  Nếu token bot nằm trong repo private, truyền PAT chỉ-đọc:
#      WM_GITHUB_TOKEN='github_pat_...' \
#        bash <(curl -fsSL '.../bootstrap.sh?v=1')
#
#  Biến môi trường:
#      WINREMOTE_REPO    URL/tên repo code (mặc định Phucvncom55/WinRemote)
#      WINREMOTE_BRANCH  nhánh (mặc định main)
#      WINREMOTE_DIR     thư mục checkout (mặc định $HOME/WinRemote)
#      WM_SECRETS_REPO   repo private chứa token.txt + bot-config.json
#      WM_GITHUB_TOKEN   PAT đọc repo private (hoặc GH_TOKEN/GITHUB_TOKEN)
# ════════════════════════════════════════════════════════════════════
set -euo pipefail
export GIT_TERMINAL_PROMPT=0

REPO="${WINREMOTE_REPO:-https://github.com/Phucvncom55/WinRemote.git}"
BRANCH="${WINREMOTE_BRANCH:-main}"
APP_DIR="${WINREMOTE_DIR:-$HOME/WinRemote}"

# Cho phép truyền repo trực tiếp: bash bootstrap.sh https://github.com/user/repo
if [[ $# -ge 1 && -n "${1:-}" && "${1}" != -* ]]; then
  REPO="$1"
  shift
fi
REPO="${REPO%/}"
if [[ "$REPO" != *://* && "$REPO" != git@* && "$REPO" != /* && "$REPO" == */* ]]; then
  REPO="https://github.com/${REPO}"
fi
[[ "$REPO" == http* && "$REPO" != *.git ]] && REPO="${REPO}.git"

say()  { printf '\033[1;36m▸ %s\033[0m\n' "$*"; }
fail() { printf '\033[1;31m✖ %s\033[0m\n' "$*" >&2; exit 1; }

command -v git >/dev/null 2>&1 || {
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update -qq && apt-get install -y -qq git
  else
    fail "Cần git. Cài git rồi chạy lại."
  fi
}

say "WinRemote bootstrap v1"

if [[ -d "$APP_DIR/.git" ]]; then
  say "Cập nhật mã nguồn trong $APP_DIR"
  if git -C "$APP_DIR" pull --ff-only >/dev/null 2>&1; then
    :
  else
    say "Không fast-forward được — lấy đúng bản $BRANCH trên GitHub."
    git -C "$APP_DIR" fetch --depth 1 origin "$BRANCH" >/dev/null 2>&1 \
      || fail "Không tải được $REPO (nhánh $BRANCH)."
    git -C "$APP_DIR" reset --hard FETCH_HEAD >/dev/null 2>&1 \
      || fail "Không cập nhật được $APP_DIR."
  fi
else
  if [[ -e "$APP_DIR" ]]; then
    fail "$APP_DIR đã tồn tại nhưng không phải repo git. Đổi WINREMOTE_DIR hoặc xóa thư mục đó trước."
  fi
  say "Tải mã nguồn từ $REPO"
  git clone --depth 1 --branch "$BRANCH" "$REPO" "$APP_DIR" \
    || fail "Không clone được $REPO — kiểm tra URL/nhánh, hoặc repo private cần PAT."
fi

[[ -f "$APP_DIR/run.sh" ]] || fail "Repo thiếu run.sh."
[[ -f "$APP_DIR/colab.py" ]] || fail "Repo thiếu colab.py."

exec bash "$APP_DIR/run.sh" "$@"
