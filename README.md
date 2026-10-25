# WinRemote — Bot Telegram điều khiển Windows VM trong GitHub Codespaces

Bot Telegram chạy trên **Google Colab** (hoặc Linux/VPS bất kỳ), điều khiển từ xa một
**Windows VM (dockurr/windows)** trong **GitHub Codespace** của bạn: backup/restore lên
OneDrive, bật/tắt VM, chạy lệnh SSH, mở máy Windows tạm trên GitHub Actions — tất cả qua chat.

> **Không còn Railway.** Không Dockerfile, không volume `/data`, không cổng public.
> Cấu hình được tự động sao lưu lên một repo GitHub **private** để Colab reset vẫn khôi phục được.

---

## 1. Kiến trúc

```
 Điện thoại                Google Colab                     GitHub Codespace
┌──────────┐   Telegram   ┌──────────────────┐  gh codespace  ┌─────────────────────┐
│  Bạn     │ ───────────► │  bot long-poll   │ ─────────────► │  docker: windows VM │
│ Telegram │ ◄─────────── │  job queue       │ ◄───────────── │  data.img @ /mnt/data│
└──────────┘              │  config + token  │                │  rclone → OneDrive  │
                          └────────┬─────────┘                └──────────┬──────────┘
                                   │                                     │
                                   │ GitHub API (private repo)           │ rclone
                                   ▼                                     ▼
                          ┌──────────────────┐                  ┌──────────────────┐
                          │  secrets repo    │                  │     OneDrive     │
                          │  token.txt       │                  │ data_backup_*/   │
                          │  bot-config.json │                  │ parts/*.tar.zst  │
                          └──────────────────┘                  └──────────────────┘
```

- **Colab** chỉ chạy bot Python (stdlib), `gh` CLI và `ssh`. Không cần Docker.
- **Codespace** là nơi chạy VM Windows thật, qua `gh codespace ssh`.
- **OneDrive** là kho backup.
- **Secrets repo** (private) chứa token bot + config; bot tự đẩy config lên sau mỗi lần đổi setting.

---

## 2. Chuẩn bị tài khoản

### 2.1. GitHub token (classic)

Tạo tại <https://github.com/settings/tokens> → **Generate new token (classic)**.
Chọn các scope:

| Scope | Dùng cho |
|---|---|
| `repo` | tạo repo private, đọc/ghi workflow, secret |
| `codespace` | list/start/stop/ssh Codespace |
| `admin:public_key` | tự đăng ký SSH key cho `gh codespace ssh` |
| `workflow` | chạy workflow VM Actions |
| `read:user` | xác minh tài khoản |

> Token này nhập qua `/settings` trong Telegram, **không** nằm trong code.

### 2.2. OneDrive token (bằng link + mã)

Không cần trình duyệt trên server:

1. `/settings` → **☁️ OneDrive** → **🔗 Đăng nhập OneDrive (link + mã)**.
2. Bot gửi link `microsoft.com/devicelogin` và mã 9 ký tự.
3. Mở link trên điện thoại/máy tính, nhập mã, đăng nhập và bấm **Cho phép**.
4. Bot tự poll, nhận `access_token` + `refresh_token`, kiểm tra dung lượng và lưu.

Token có refresh token nên dùng lâu dài; bot tự làm mới khi gần hết hạn.
Nếu muốn nhập tay, vẫn có thể chạy `rclone authorize onedrive` trên máy có trình duyệt
rồi dán JSON vào `/settings` (nút **📋 Dán token JSON**).

### 2.3. Google Drive (tùy chọn — gộp nhiều tài khoản)

Dùng khi muốn backup sang Google Drive, hoặc gộp nhiều tài khoản thành một ổ lớn
(xem `/gdrive`). Có hai cách thêm tài khoản:

**Cách 1 — bằng link (khuyến nghị, không cần cài gì):**

1. `/gdrive` → **🔗 Thêm tài khoản bằng link**.
2. Bấm nút **Mở trang đăng nhập Google** → chọn tài khoản → **Cho phép / Allow**.
3. Trình duyệt chuyển tới `http://127.0.0.1:53682/?code=…` và báo *không truy cập được*
   — bình thường, bot không chạy web server trên máy bạn.
4. Copy **toàn bộ địa chỉ** trên thanh địa chỉ (hoặc phần `code=…`) rồi dán lại vào chat.
   Bot tự đổi mã thành token, kiểm tra dung lượng và lưu tài khoản.

Làm lại bước 1–4 với từng tài khoản Google muốn gộp; bot tạo `gdrive1..N` và một remote
union `gdrive:` với tổng dung lượng.

**OAuth client riêng (khi Google từ chối client mặc định):**

Bot dùng client công khai của rclone để đăng nhập. Google đang ngừng client này trong 2026;
nếu bot báo `The provided client secret is invalid` hoặc `invalid_client`, hãy tạo client riêng:

1. `/gdrive` → **🔑 OAuth client riêng** (xem hướng dẫn ngay trong bot).
2. Vào [Google Cloud Console → Credentials](https://console.cloud.google.com/apis/credentials),
   tạo project → bật **Google Drive API**.
3. **OAuth consent screen** → External → thêm email của bạn vào **Test users** → **Publish app**
   (để Testing, refresh token chỉ sống 7 ngày).
4. **Create credentials → OAuth client ID → Desktop app**, rồi dán **Client ID** và
   **Client secret** vào bot.
5. Bấm **🔗 Thêm tài khoản bằng link** lại. Không cần thêm redirect URI — Desktop app tự
   chấp nhận loopback `127.0.0.1:53682`.

Client ID/secret lưu trong `bot-config.json` (repo secrets private), không gửi ra ngoài.
Token đã tạo bằng client nào sẽ tự làm mới bằng client đó.

**Cách 2 — dán JSON từ rclone (nâng cao):**

```bash
rclone authorize "drive"
```

Copy nguyên chuỗi JSON (`access_token` + `refresh_token`), rồi `/gdrive` →
**📋 Dán token JSON** và dán vào.

### 2.4. Telegram bot token

Tạo bot với [@BotFather](https://t.me/BotFather), lấy token dạng `123456:ABC...`.

### 2.5. Hai repo GitHub

| Repo | Chế độ | Nội dung |
|---|---|---|
| `Phucvncom55/WinRemote` | **Public** | code (repo này) |
| `Phucvncom55/WinRemote-secrets` | **Private** | `token.txt`, `bot-config.json` |

Repo code public để lệnh `curl` đọc được `bootstrap.sh`.
Repo secrets private chứa token — **tuyệt đối không để public**.

---

## 3. Chạy trên Colab — một dòng

Mở <https://colab.research.google.com>, tạo notebook mới, dán **một dòng** vào cell và chạy:

```python
!WM_GITHUB_TOKEN='github_pat_xxx' bash <(curl -fsSL 'https://raw.githubusercontent.com/Phucvncom55/WinRemote/main/bootstrap.sh?v=1')
```

Thay `github_pat_xxx` bằng **fine-grained PAT**. Tạo nhanh tại:

<https://github.com/settings/personal-access-tokens/new?name=WinRemote+Colab&description=Read+WinRemote+secrets&contents=read&expires_in=90>

- **Resource owner:** `Phucvncom55`
- **Repository access:** Only select repositories → `WinRemote-secrets`
- **Permissions:** Contents = **Read-only** (Metadata tự động có)

Sau đó dán token vào lệnh trên. PAT này **chỉ đọc** repo secrets, không sửa được
code hay repo khác. Tuy nhiên nó đọc được mọi file trong repo secrets — gồm token bot
và `bot-config.json` (có thể chứa GitHub/OneDrive token). Vì vậy không chia sẻ notebook
hoặc dòng lệnh có PAT; nên đặt hạn 7–90 ngày và thu hồi ngay nếu lộ.
Muốn lệnh gọn hơn, lưu PAT vào **Colab Secrets** với tên `WM_GITHUB_TOKEN`
(biểu tượng 🔑 ở thanh bên trái, bật quyền cho notebook), sau đó dùng:

```python
!bash <(curl -fsSL 'https://raw.githubusercontent.com/Phucvncom55/WinRemote/main/bootstrap.sh?v=1')
```

Lệnh này tự clone/cập nhật code rồi gọi `run.sh`. `run.sh` tìm token bot theo thứ tự:

1. Biến `TELEGRAM_BOT_TOKEN`
2. File `secrets/token.txt` trong repo (nếu có)
3. Repo private `Phucvncom55/WinRemote-secrets`, xác thực bằng `WM_GITHUB_TOKEN`,
   `GH_TOKEN`, `GITHUB_TOKEN`, `gh auth token` hoặc Colab Secret (một trong ba tên
   `WM_GITHUB_TOKEN`, `GITHUB_TOKEN`, `GH_TOKEN`)

Nếu không tìm được PAT nào, `colab.py` tự mời đăng nhập GitHub CLI bằng mã thiết bị
(device code): nó in `XXXX-XXXX` và link `github.com/login/device`, bạn nhập mã rồi
Authorize — bot đọc repo secrets và chạy tiếp. Cách này **không cần tạo PAT**, nhưng
phải làm lại mỗi lần Colab cấp runtime mới (khoảng 12 giờ). Muốn khỏi lặp lại, hãy tạo
PAT chỉ-đọc và lưu vào Colab Secrets như trên.

Nếu repo code để **private**, thêm `WINREMOTE_REPO='Phucvncom55/WinRemote'` và cấp PAT
quyền đọc repo code.

> `?v=1` chỉ để phá cache của `raw.githubusercontent.com`. Khi sửa `bootstrap.sh`,
> tăng thành `?v=2`, `?v=3`… để Colab chắc chắn lấy bản mới.

Lần đầu chạy, bot sẽ:

1. Cài `gh` CLI bản chính thức (nếu chưa có).
2. Kiểm tra `ssh`/`ssh-keygen`.
3. Đọc `token.txt` từ repo secrets.
4. Khôi phục `bot-config.json` (nếu đã có).
5. Bắt đầu long-poll Telegram.

Sau khi cell chạy, bot sống cho tới khi Colab ngắt runtime (khoảng 12 giờ hoặc khi idle lâu).
Mở Telegram, gõ `/start`.

---

## 4. Lần đầu sử dụng

1. **`/start`** — tin nhắn đầu tiên sẽ nhận quyền chủ bot (chỉ chat của bạn điều khiển được).
2. **`/settings` → GitHub token** — dán classic token ở mục 2.1.
3. **`/settings` → OneDrive** — bấm **Đăng nhập tự động** rồi làm theo link + mã
   (hoặc dán JSON `rclone authorize onedrive`).
4. **`/settings` → Codespace** — chọn Codespace có sẵn, hoặc bấm **Tạo Codespace mới**
   (máy 4 core / 16 GB RAM / 32 GB disk). Nút **📦 Repo** cho phép tạo/đổi repo
   `winvm-codespace` ngay trên màn hình trạng thái và hành động.
5. **`/setup`** — mount ổ dữ liệu `/mnt/data`, cài rclone, nối OneDrive (và Google Drive
   nếu đã thêm tài khoản).
6. **`/restore`** — chọn bản backup trên OneDrive **hoặc** Google Drive (hoặc bỏ qua nếu muốn tạo VM mới).
7. **`/vm` → Bật VM** — mở Web UI 8006 và RDP 3389.

Tùy chọn: **`/gdrive`** để gộp nhiều tài khoản Google Drive, **`/quota`** để xem thời gian
Codespace còn lại, **`/kalog`** để xem log keepalive.

Mỗi lần đổi setting, bot tự đẩy `bot-config.json` lên repo secrets. Lần chạy Colab sau,
`colab.py` tự khôi phục — không phải nhập lại token.

---

## 5. Lệnh chính

| Lệnh | Chức năng |
|---|---|
| `/start` | Nhận quyền chủ bot / mở trang chính |
| `/status` | Codespace, container, cổng, dung lượng, job, keepalive, **quota còn lại** |
| `/quota` | Tổng thời gian Codespace còn dùng được (chạy + treo), ngày làm mới, nhập số đã dùng thủ công |
| `/backup` | Dừng VM an toàn → nén zstd → chia 1 GB/part → upload (OneDrive/Google Drive) → kiểm tra → bật lại VM |
| `/restore` | Quét **cả** OneDrive và Google Drive → chọn bản backup → tải → ghép → verify → giải nén vào `/mnt/data` |
| `/gdrive` | Gộp nhiều tài khoản Google Drive thành một remote union `gdrive:`; thêm **bằng link** (hoặc dán JSON), xóa/kiểm tra/đổi đích backup |
| `/repo` | Chọn hoặc tạo repo private `winvm-codespace` cho Codespace và Actions |
| `/vm` | Bật/tắt/restart/tạo lại/xóa container; đổi CPU/RAM/disk/Windows/user/password |
| `/setup` | Mount `/mnt/data` + cài rclone + ghi `rclone.conf` + xác minh OneDrive/Google Drive |
| `/ssh <lệnh>` | Chạy bash trong Codespace; output dài gửi kèm file `.txt` |
| `/cleanup` | Xóa parts/archive/log tạm trên Codespace |
| `/actions` | Mở Windows tạm trên GitHub Actions (~6 giờ), RDP qua Tailscale; chọn runner **windows-2025** hoặc **windows-2022** |
| `/kalog` | Log keepalive hiển thị **ngay trong chat** (phân trang ◀️/▶️, không tải file): đúng Codespace chưa, hostname, nhịp tim, lỗi gần nhất; có nút **⏹ Dừng / ▶️ Bật Codespace** |
| `/ports` | Mở public cổng 8006/3389 |
| `/settings` | Token, Codespace, keepalive, cấu hình VM |
| `/cancel` | Hủy job đang chạy (an toàn ở ranh giới bước) |
| `/help` | Hướng dẫn nhanh |

---

## 6. Cấu hình & đồng bộ

Toàn bộ cấu hình nằm trong `DATA_DIR/bot-config.json` (mặc định `./data`).

| Biến môi trường | Mặc định | Ý nghĩa |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | — | Token bot (bắt buộc) |
| `DATA_DIR` | `./data` | Nơi lưu config, `gh` home, SSH key |
| `CONFIG_SYNC_REPO` | — | Repo private để đẩy config, dạng `user/repo` |
| `CONFIG_SYNC_PATH` | `bot-config.json` | Đường dẫn file trong repo sync |
| `CONFIG_SYNC_TOKEN` | — | Token ghi repo sync (mặc định dùng `github_token`) |
| `TELEGRAM_ADMIN_ID` | — | Chat ID owner cố định (thường để trống) |
| `GH_BIN` | `gh` | Đường dẫn gh CLI |
| `WM_POLL_SEC` | `15` | Chu kỳ poll tiến trình backup/restore |

**Cơ chế sync:** bot dùng chính `github_token` (classic, có scope `repo`) để `PUT`
`bot-config.json` lên repo private. Nếu token hết hạn hoặc không có quyền, bot vẫn chạy
bình thường; chỉ mất khả năng khôi phục sau khi Colab reset.

> Nếu dùng GitHub token fine-grained, phải cấp quyền **Contents: Read and write**
> cho repo secrets, nếu không sync sẽ báo lỗi 403.

### 6.1. Google Drive gộp nhiều tài khoản

Mỗi tài khoản được lưu trong `gdrive_accounts` của `bot-config.json` (kèm
`access_token`/`refresh_token`). Khi `/setup` hoặc `/gdrive → Kiểm tra`, bot ghi
`rclone.conf` trên Codespace:

```ini
[gdrive1] type = drive ...
[gdrive2] type = drive ...
[gdrive]  type = union
upstreams = gdrive1: gdrive2:
create_policy = epmfs
search_policy = ff
```

- Dung lượng hiển thị là **tổng** các tài khoản; file mới được ghi vào tài khoản
  còn nhiều chỗ trống nhất (`epmfs`), nhưng **mỗi file nằm trọn trên một tài khoản**.
- Union **không** làm tăng tốc độ; file lớn hơn dung lượng trống của tài khoản
  được chọn sẽ lỗi.
- Không xóa tài khoản khỏi `/gdrive` khi vẫn còn backup trên đó — bot cảnh báo
  trước khi xóa, nhưng dữ liệu vẫn nằm trên Google Drive của bạn.
- Token Google dùng client công khai của rclone (redirect loopback
  `http://127.0.0.1:53682/`); nếu bị thu hồi, vào `/gdrive` →
  **🔗 Thêm tài khoản bằng link** để cấp quyền lại (hoặc dán JSON rclone).

---

## 7. Bảo mật

- Repo secrets phải **private**. Không bao giờ commit token vào repo code public.
- PAT dùng trong lệnh Colab chỉ nên có **Contents: Read** trên đúng repo secrets,
  đặt thời hạn ngắn (7–90 ngày). PAT đó đọc được cả `token.txt` và `bot-config.json`
  (có thể chứa GitHub/OneDrive token), nên đừng chia sẻ notebook hoặc dòng lệnh có PAT.
- Nếu notebook bị chia sẻ hoặc PAT/token bị lộ: thu hồi PAT, tạo bot token mới với
  @BotFather, cập nhật `secrets/token.txt`, rồi chạy lại. Nếu GitHub/OneDrive token
  trong `bot-config.json` bị lộ, thu hồi và nhập lại qua `/settings`.
- `.env.local` và `secrets/*` đã bị `.gitignore`; chỉ `secrets/README.md` được commit.
- Bot chỉ phục vụ **một owner** — chat đầu tiên gõ `/start`.

---

## 8. Giới hạn của Colab (quan trọng)

Colab **không phải hosting 24/7**:

- Runtime tối đa khoảng **12 giờ**, sau đó bị thu hồi.
- Ngắt kết nối/không tương tác khoảng **90 phút** cũng có thể bị dừng.
- Khi Colab dừng, bot dừng theo (VM trong Codespace vẫn chạy độc lập).

Vì vậy:

- Dùng Colab khi cần điều khiển/backup trong phiên làm việc.
- Muốn chạy 24/7 thật sự, dùng một VPS Linux nhỏ (không Railway) — cùng code này:
  `python colab.py` hoặc `bash run.sh`.
- Cấu hình/token vẫn được khôi phục nhờ repo secrets, nên mỗi lần mở Colab chỉ cần chạy lại
  đúng một dòng.

Ngoài ra, GitHub Codespaces có quota (ví dụ 120 core-hours/tháng với tài khoản Free) và
có thể tự dừng khi idle. Dùng `/quota` để xem thời gian chạy/treo còn lại và ngày làm mới
(bot lấy số liệu billing, có thể nhập tay nếu GitHub chưa cập nhật).
Không dùng bot để vượt giới hạn của GitHub.

---

## 9. Chạy local / kiểm tra

```bash
# Kiểm tra môi trường, token Telegram, cấu hình
python colab.py --check

# Chạy bot (đọc secrets/token.txt hoặc TELEGRAM_BOT_TOKEN)
python colab.py

# Chạy trực tiếp không qua bootstrap
python -m bot.main

# Test toàn bộ engine không cần mạng
python tests/test_local.py
```

Yêu cầu local: Python 3.10+, `gh` CLI, `ssh`, `ssh-keygen`, `git`.
Không cần `pip install` — bot chỉ dùng thư viện chuẩn.

---

## 10. Cấu trúc repo

```
bootstrap.sh          # one-liner: clone/update repo rồi gọi run.sh
run.sh                # tìm token/PAT, chạy colab.py
colab.py              # cài gh, đọc token/config từ repo secrets, chạy bot
bot/
  main.py             # entrypoint long-poll
  cfg.py              # đọc/ghi bot-config.json
  sync.py             # đẩy config lên repo GitHub private
  engine.py           # router lệnh + job backup/restore/VM/Actions
  gh.py               # wrapper gh CLI + GitHub API
  oauth.py            # device-code login OneDrive + refresh token Google
  remote.py           # chạy lệnh trên Codespace qua gh codespace ssh
  keepalive.py        # giữ Codespace không idle
  tg.py               # Telegram client (stdlib) + fake cho test
  ui.py               # nội dung tin nhắn/button
  util.py             # format, parse
remote/               # script bash đẩy lên Codespace
  02_rclone.sh        # ghi/merge rclone.conf (OneDrive, giữ nguyên section Drive)
  10_gdrive.sh        # tạo gdrive1..N + remote union gdrive:
  92_backup_once.sh   # upload/kiểm tra backup theo VM_BACKUP_REMOTE
tests/test_local.py   # test engine với Telegram/gh giả
secrets/README.md     # mô tả repo secrets (token thật không nằm ở đây)
```

---

## 11. Xử lý sự cố

| Hiện tượng | Cách xử lý |
|---|---|
| `Không đọc được token bot từ repo bí mật` / `repo private và chưa có WM_GITHUB_TOKEN` | Repo `WinRemote-secrets` là private nên phải truyền PAT chỉ-đọc khi chạy one-liner: `WM_GITHUB_TOKEN='github_pat_...' bash <(curl ...)`. Fine-grained PAT phải chọn **đúng repo** `WinRemote-secrets` + quyền **Contents: Read-only**. Nếu lưu trong Colab Secrets: mở 🔑 → bật **Notebook access** cho `WM_GITHUB_TOKEN` (hoặc `GITHUB_TOKEN`/`GH_TOKEN`). Không có PAT, `colab.py` sẽ mời đăng nhập GitHub bằng mã thiết bị. |
| `Đăng nhập GitHub CLI chưa xong` | Mã thiết bị đã hết hạn hoặc Colab chặn luồng tương tác — chạy lại cell, nhập mã mới trong 15 phút; hoặc dùng PAT chỉ-đọc. |
| `token.txt không giống token bot` | File chỉ nên chứa token dạng `123456:ABC…` (hoặc một dòng `TELEGRAM_BOT_TOKEN=…`), không thêm ngoặc kép, khoảng trắng hay ghi chú. |
| `PAT không hợp lệ hoặc đã hết hạn (HTTP 401)` | Tạo PAT mới (7–90 ngày) rồi chạy lại one-liner với PAT đó. |
| `Không thấy .../token.txt (HTTP 404)` | PAT fine-grained chưa được cấp quyền repo đó, hoặc file chưa commit lên nhánh `main`. |
| `Token Telegram không hợp lệ` | Token trong `token.txt` đã bị thu hồi/đổi — lấy token mới từ @BotFather, cập nhật file trong repo secrets rồi chạy lại. |
| `Thiếu gh CLI` | Chạy lại `colab.py` (tự tải) hoặc `apt-get install gh` |
| `HTTP 401 Bad credentials` | GitHub token sai/hết hạn → `/settings` nhập lại |
| `Token thiếu quyền codespace` | Tạo classic token có scope `codespace` |
| `Token thiếu scope workflow` | Thêm scope `workflow` cho token GitHub |
| `Codespace không Available` | Kiểm tra quota/billing GitHub Codespaces |
| `SSH tới Codespace thất bại` | Bot tự tạo/đăng ký key; kiểm tra scope `admin:public_key` |
| `OneDrive token hết hạn` | `/settings` → OneDrive → **Đăng nhập tự động** lại, hoặc dán token mới |
| `Google Drive token hết hạn/thu hồi` | `/gdrive` → xóa tài khoản lỗi → **🔗 Thêm tài khoản bằng link** (hoặc dán JSON `rclone authorize "drive"`) |
| `The provided client secret is invalid` / `invalid_client` | Google đã ngừng client chung của rclone. `/gdrive` → **🔑 OAuth client riêng** → nhập Client ID + secret của Desktop app bạn tự tạo (xem 2.3), rồi thêm tài khoản lại. |
| `Không thấy backup trên Drive` | `/restore` quét cả hai cloud; kiểm tra `/gdrive → Kiểm tra` và đích backup ở `/backup` |
| `Keepalive không chạy` | `/kalog` xem hostname/target có đúng Codespace không, bấm **Khởi động lại**; kiểm tra scope `codespace` + `admin:public_key` |
| `/quota` báo thiếu số liệu | GitHub chỉ cập nhật mỗi giờ; bấm **Nhập số đã dùng** để nhập tay từ trang billing |
| Actions không chạy `windows-2022` | Repo phải có quyền Actions; thử lại với `windows-2025` hoặc kiểm tra workflow run |
| `config sync lỗi 403` | Token GitHub không có quyền ghi repo secrets |
| Colab báo hết RAM/ngắt | Runtime bị thu hồi; chạy lại cell one-liner |
| `git clone` lỗi 404 | Repo code private hoặc sai tên; đặt `WINREMOTE_REPO` + PAT có quyền đọc |

---

## 12. Miễn trừ

Việc chạy VM liên tục trong GitHub Codespaces có thể không đúng mục đích sử dụng của GitHub;
tài khoản có nguy cơ bị hạn chế. Bạn tự chịu trách nhiệm về quota, điều khoản và dữ liệu.
