# DocSync trên Ubuntu

Bộ chạy dành cho Ubuntu có Python 3.10 trở lên và phiên đăng nhập có `systemd --user`. Cài bằng tài khoản thường.

```sh
tar -xzf DocSync-Ubuntu.tar.gz
cd DocSync-Ubuntu
./install.sh
```

Mở **DocSync** trong menu ứng dụng hoặc vào <http://127.0.0.1:8787/>. Chọn **Cài đặt** để đổi WIP, hai thư mục quét bổ sung và thư mục đích kế toán. Các thư mục phải tồn tại trước khi lưu.

- Thư mục WIP mặc định là `~/Documents/WIP & Reference`.
- Đích kế toán mặc định trên máy cài mới là `~/Documents/DocSync Accounting`.
- Hai ô quét bổ sung ban đầu để trống. Điền đường dẫn Documents, Downloads hoặc thư mục nhận file riêng nếu cần.
- App và watcher chạy theo phiên Ubuntu. `~/.local/bin/docsync-ubuntu open` mở app, `~/.local/bin/docsync-ubuntu status` xem trạng thái dịch vụ và `~/.local/bin/docsync preview` xem trước từ terminal.
- Các luồng EIM cần lưu tài khoản được duyệt và bật trong **Workflows**. Thiết lập AI/ERP thực hiện trong **File trong ngày**.
- Bộ chạy kèm thư viện đọc XLS. PDF và ảnh dùng các công cụ Ubuntu `poppler-utils`, `tesseract-ocr` và gói ngôn ngữ OCR tương ứng nếu đã cài.

Bộ cài dành cho máy cài mới. Nếu đã có DocSync cài từ checkout, hãy mở app đang dùng. Nếu muốn chuẩn bị file cài trước khi khởi động dịch vụ, chạy `./install.sh --no-start`. Sau đó dùng `systemctl --user daemon-reload` và `systemctl --user enable --now ivar-document-sync-app.service ivar-watcher.service`.
