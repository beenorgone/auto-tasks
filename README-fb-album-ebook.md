# Facebook album → Ebook (EPUB/PDF) → Kindle

`fb-album-ebook.py` lấy ảnh + caption của 1 album Facebook, ghép thành ebook có
mục lục (TOC) và gửi qua email tới địa chỉ Send-to-Kindle.

```
fetch (ảnh + caption) → build (EPUB/PDF có TOC) → send (SMTP → @kindle.com)
```

## Cài đặt

```bash
pip install pillow reportlab playwright   # pillow: nén ảnh, reportlab: PDF, playwright: lấy từ browser
```

EPUB và gửi email chỉ cần thư viện chuẩn của Python. Playwright dùng Chromium
có sẵn; nếu máy chưa có: `python3 -m playwright install chromium`.

## Chạy nhanh

```bash
# 1. Đăng nhập Facebook một lần (mở cửa sổ browser, profile lưu ở ~/.cache/fb-album-ebook)
python3 fb-album-ebook.py login

# 2. Lấy album + build EPUB + gửi Kindle
export SMTP_USER=you@gmail.com SMTP_PASSWORD='gmail-app-password' KINDLE_EMAIL=you@kindle.com
python3 fb-album-ebook.py run 'https://web.facebook.com/media/set/?set=a.122143769745029493&type=3' \
  --formats epub,pdf --send
```

Kết quả nằm ở `fb-ebooks/<album id>/` (có trong `.gitignore`): `album.json`,
`images/`, `<Tên album>.epub`, `<Tên album>.pdf`. Có thể chạy lại lệnh `fetch`
nếu bị ngắt giữa chừng, vì tool bỏ qua các ảnh đã tải.

Bạn cũng có thể chạy từng bước:

```bash
python3 fb-album-ebook.py fetch URL --limit 5          # thử 5 ảnh đầu
python3 fb-album-ebook.py build fb-ebooks/122143769745029493 --formats epub --toc caption
python3 fb-album-ebook.py send 'fb-ebooks/122143769745029493/Tên album.epub' --to you@kindle.com
```

## Bookmarklet (dùng Chrome đang đăng nhập sẵn)

Không cần `login` hay Playwright: bookmarklet chạy ngay trong tab Facebook của bạn.

```bash
python3 fb-album-ebook.py bookmarklet      # tạo fb-ebooks/bookmarklet.html
```

1. Mở `fb-ebooks/bookmarklet.html`, kéo nút **📚 FB → Ebook** lên thanh bookmark.
2. Trên Facebook, mở **ảnh đầu tiên** của album (URL có `fbid=…&set=a.…`), bấm bookmark → **Bắt đầu**.
3. Giữ tab mở. Khi hết album hoặc dừng, file `fb-album-<id>.json` được tải về.
4. Chạy ngay (link ảnh fbcdn hết hạn sau vài giờ):

```bash
python3 fb-album-ebook.py run ~/Downloads/fb-album-<id>.json --source bookmarklet --formats epub,pdf
```

Album lớn thì chạy nhiều lần: bấm lại bookmarklet trên ảnh đã dừng (nút **Tới ảnh đã dừng**),
mỗi file JSON mới được gộp vào `fb-ebooks/<id>/` và bỏ qua ảnh đã tải.

Giảm rủi ro bị Facebook chặn (sửa hằng số đầu file `fb-album-bookmarklet.js`, rồi chạy lại lệnh `bookmarklet`):

- Chỉ bấm nút "Ảnh tiếp theo" như người dùng, không gọi request nền, không mở nhiều tab.
- Chờ ngẫu nhiên **20–60 giây** giữa hai ảnh (`DELAY_MIN`/`DELAY_MAX`), tối đa **150 ảnh/lần** (`MAX_PER_RUN`).
- Tự dừng hẳn, không thử lại, khi gặp checkpoint, trang đăng nhập, hộp thoại "tạm thời bị chặn", "quá nhanh", captcha,
  ảnh không tải được, hoặc 3 ảnh liên tiếp không đọc được. Gặp cảnh báo thì nghỉ ít nhất 24 giờ.
- Tab chạy nền có thể bị Chrome làm chậm timer (khoảng 1 phút/ảnh): chậm hơn nhưng không hỏng.

## Nguồn dữ liệu (`--source`)

| Nguồn | Khi nào dùng | Ghi chú |
|---|---|---|
| `browser` (mặc định) | Album mà tài khoản đã login xem được | Playwright mở từng ảnh và đọc caption. Facebook đổi giao diện thì phải cập nhật selector trong `PHOTO_DETAIL_JS`. |
| `graph` | Album của Page mình quản trị | `--token` hoặc `FB_ACCESS_TOKEN` = Page access token. Đây là cách ổn định nhất. |
| `bookmarklet` | File JSON do bookmarklet tải về | Xem mục Bookmarklet ở trên. |
| `export` | File "Tải xuống thông tin của bạn" (JSON) | `fetch <thư mục export> --source export --album-name "Tên album"`. Tool tự sửa lỗi font tiếng Việt của file export. |

## Mục lục (`--toc`)

- `caption` (mặc định): ảnh nào có caption thì mở một mục mới, lấy dòng đầu của caption làm tiêu đề mục. Các ảnh không có caption đi sau được gộp vào mục đó.
- `photo`: mỗi ảnh là một mục.

## Kindle

- EPUB là định dạng Kindle khuyến nghị: chữ reflow, TOC hiện ở menu "Go to". PDF giữ nguyên layout (khổ mặc định A5, đổi bằng `--page-size`).
- Giới hạn 50 MB/email. Ảnh được nén về cạnh dài 1600px (`--max-image-side`); nếu file vẫn lớn thì giảm giá trị này.
- Email gửi đi (`SMTP_FROM` hoặc `SMTP_USER`) phải có trong *Approved Personal Document E-mail List* trên Amazon (Manage Content & Devices → Preferences → Personal Document Settings).
- SMTP mặc định là `smtp.gmail.com:587`. Đổi bằng `SMTP_HOST`/`SMTP_PORT` (port 465 dùng SSL).

## Lưu ý

Chỉ dùng tool cho album của mình/Page mình quản lý, hoặc khi đã được phép. Việc
scrape Facebook có thể vi phạm điều khoản của Facebook nếu lạm dụng. Tool mặc
định chờ khoảng 1,5–2,5 giây giữa các ảnh (`--delay`).

## Test

```bash
python3 -m unittest test_fb_album_ebook -v
```
