# WinRemote — Bot Telegram quản lý Windows VM trong GitHub Codespaces

Bot Telegram chạy trên **Google Colab** (hoặc Linux/VPS), điều khiển từ xa một
**Windows VM (dockurr/windows)** trong **GitHub Codespace**: backup/restore OneDrive,
bật/tắt VM, chạy SSH, mở Windows tạm trên GitHub Actions — tất cả qua chat.

> Không còn Railway, không Dockerfile, không volume `/data`, không cổng public.
> Hướng dẫn đầy đủ bằng tiếng Việt nằm trong [README.md](README.md).

## Chạy trên Colab — một dòng

```python
!WM_GITHUB_TOKEN='github_pat_xxx' bash <(curl -fsSL 'https://raw.githubusercontent.com/Phucvncom55/WinRemote/main/bootstrap.sh?v=1')
```

`github_pat_xxx` là **fine-grained PAT chỉ-đọc** repo private
`Phucvncom55/WinRemote-secrets` (quyền **Contents: Read-only**). Nếu đã lưu PAT
trong Colab Secrets với tên `WM_GITHUB_TOKEN`, có thể dùng lệnh gọn:

```python
!bash <(curl -fsSL 'https://raw.githubusercontent.com/Phucvncom55/WinRemote/main/bootstrap.sh?v=1')
```

Lệnh tự clone/cập nhật code, tải `token.txt` từ repo secrets, khôi phục
`bot-config.json` nếu có, rồi chạy bot long-poll. Không hỏi gì trên terminal.

## Sau khi bot chạy

1. `/start` — chat đầu tiên nhận quyền owner.
2. `/settings` — nhập GitHub token (scope `repo`, `codespace`, `workflow`,
   `admin:public_key`, `read:user`) và OneDrive token.
3. Chọn/tạo Codespace, chạy `/setup`, `/restore` (nếu cần), rồi `/vm`.

Mỗi lần đổi setting, bot tự đẩy `bot-config.json` lên repo private để lần chạy
Colab sau tự khôi phục.

## Lưu ý

- Colab không phải hosting 24/7: runtime tối đa khoảng 12 giờ và có thể ngắt khi idle.
- Repo secrets phải **private**; không commit token vào repo code public.
- Cần Python 3.10+, `git`, `gh`, `ssh` — `colab.py` tự cài `gh` nếu thiếu.

Xem [README.md](README.md) để biết kiến trúc, danh sách lệnh, biến môi trường và
cách xử lý sự cố.
