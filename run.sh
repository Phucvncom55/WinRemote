#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════
#  WinRemote — chạy bot (được bootstrap.sh gọi, hoặc chạy trực tiếp)
#
#      bash run.sh
#      bash run.sh --check
#
#  Nguồn token bot (theo thứ tự):
#      1. TELEGRAM_BOT_TOKEN trong môi trường
#      2. secrets/token.txt trong repo (nếu có)
#      3. repo private WM_SECRETS_REPO, xác thực bằng WM_GITHUB_TOKEN /
#         GH_TOKEN / GITHUB_TOKEN / `gh auth token` / Colab Secrets
#  Không in token ra màn hình.
# ════════════════════════════════════════════════════════════════════
set -euo pipefail
export GIT_TERMINAL_PROMPT=0
export PYTHONIOENCODING=utf-8
export PYTHONUTF8=1

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || {
  if command -v python >/dev/null 2>&1; then PY=python; else
    printf '\033[1;31m✖ Không tìm thấy python3. Cài Python 3.10+ rồi chạy lại.\033[0m\n' >&2
    exit 1
  fi
}

say() { printf '\033[1;36m▸ %s\033[0m\n' "$*"; }

# Repo private mặc định của chủ dự án. Có thể override bằng biến môi trường.
export WM_SECRETS_REPO="${WM_SECRETS_REPO:-Phucvncom55/WinRemote-secrets}"

# PAT chỉ-đọc để lấy token/config từ repo private. Ưu tiên biến riêng,
# sau đó tới các biến GitHub phổ biến, `gh auth token`, và Colab Secrets.
if [[ -z "${WM_GITHUB_TOKEN:-}" ]]; then
  for name in GH_TOKEN GITHUB_TOKEN; do
    value="${!name:-}"
    if [[ -n "$value" ]]; then
      export WM_GITHUB_TOKEN="$value"
      break
    fi
  done
fi
if [[ -z "${WM_GITHUB_TOKEN:-}" ]] && command -v gh >/dev/null 2>&1; then
  value="$(gh auth token 2>/dev/null || true)"
  [[ -n "$value" ]] && export WM_GITHUB_TOKEN="$value"
fi
if [[ -z "${WM_GITHUB_TOKEN:-}" && -d /content ]]; then
  value="$("$PY" -c 'from google.colab import userdata; print(userdata.get("WM_GITHUB_TOKEN") or "", end="")' 2>/dev/null || true)"
  [[ -n "$value" ]] && export WM_GITHUB_TOKEN="$value"
fi

exec "$PY" "$SCRIPT_DIR/colab.py" "$@"
