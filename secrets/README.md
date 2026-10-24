# Thư mục bí mật (không commit)

Repo code công khai **không chứa token**. Token thật nằm ở repo GitHub
**private** `Phucvncom55/WinRemote-secrets`, gồm:

```
token.txt          # token bot Telegram (một dòng)
bot-config.json    # (tùy chọn) cấu hình đầy đủ do bot tự đẩy lên
```

`colab.py` đọc hai file này qua GitHub Contents API bằng một PAT chỉ có
quyền **Contents: Read** trên repo secrets.

Khi chạy local, bạn có thể tạo `secrets/token.txt` trong thư mục này để test.
File `secrets/*` đã nằm trong `.gitignore`, trừ chính README này.
