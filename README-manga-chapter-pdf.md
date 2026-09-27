# Tập truyện → PDF

`manga-chapter-pdf.py` lấy các ảnh đang hiển thị công khai trên trang tập truyện
Truyen Tuoi Tho, lưu vào bộ nhớ đệm và tạo PDF một ảnh trên mỗi trang.

```bash
python3 -m pip install requests beautifulsoup4 pillow reportlab
python3 manga-chapter-pdf.py scan 'https://truyentuoitho.com/manga/h2-adachi-mitsuru/tap-22/'
python3 manga-chapter-pdf.py run 'https://truyentuoitho.com/manga/h2-adachi-mitsuru/tap-22/'
```

Tải nhiều tập tuần tự:

```bash
python3 manga-chapter-pdf.py bulk \
  'https://truyentuoitho.com/manga/h2-adachi-mitsuru/tap-22/' \
  'https://truyentuoitho.com/manga/h2-adachi-mitsuru/tap-23/'

# Hoặc lưu một URL mỗi dòng trong links.txt; dòng bắt đầu bằng # được bỏ qua.
python3 manga-chapter-pdf.py bulk --file links.txt
python3 manga-chapter-pdf.py status --file links.txt  # xem tập nào đang tải/đã xong
```

Bulk bỏ URL trùng, tối đa 100 tập/lần. Mặc định chỉ có một luồng tải: nghỉ trung
bình 2 giây giữa request (dao động 20%) và thêm 5 giây giữa hai tập. Có thể tăng
thời gian bằng `--delay 4 --chapter-delay 15`. Gặp HTTP 403/429, công cụ dừng
toàn bộ lượt tải và hiện `Retry-After` nếu máy chủ cung cấp; không thử vượt chặn.
Lỗi riêng của một tập được ghi rõ, các tập khác vẫn được xử lý. Chạy lại cùng
lệnh để tiếp tục từ ảnh đã tải và xử lý các tập lỗi.

## Từ trang bộ truyện đến hàng đợi có thể tiếp tục

```bash
python3 manga-chapter-pdf.py discover 'https://truyentuoitho.com/manga/ninja-loan-thi/'
python3 manga-chapter-pdf.py status --file manga-ebooks/ninja-loan-thi-links.txt
python3 manga-chapter-pdf.py queue --file manga-ebooks/ninja-loan-thi-links.txt --max-new 5
```

`discover` lấy danh sách chương từ endpoint mà giao diện website sử dụng và lưu
URL theo thứ tự vào `manga-ebooks/ninja-loan-thi-links.txt`. `queue` xử lý tối đa
5 tập mới trong một lượt; chạy lại sẽ bỏ qua PDF đã đủ trang và dùng ảnh đã tải.
Khóa file ngăn hai lượt `queue` của cùng bộ truyện chạy đồng thời. Tập không có
ảnh hiển thị trong HTML được ghi `Cần xem lại` vào trạng thái hàng đợi; công cụ
không giải mã trình đọc riêng hoặc vượt yêu cầu quyền truy cập. Sau khi trang
hiển thị ảnh hợp lệ, chạy `queue ... --retry-blocked` để thử lại.

File service mẫu cho lịch tải Ninja Loạn Thị nằm tại
`systemd/manga-ninja-loan-thi.service`. Giờ chạy của timer cần được chọn riêng.

Kết quả mặc định: `manga-ebooks/h2-adachi-mitsuru/tap-22/h2-adachi-mitsuru-tap-22.pdf`.
Ảnh tải được giữ lại trong thư mục `images/`. Khi chạy lại, công cụ dùng ảnh hợp lệ
đã tải và tiếp tục từ trang thiếu; PDF đủ trang được bỏ qua nếu máy có `pdfinfo`.
Nếu danh sách trang trên website thay đổi, công
cụ yêu cầu chọn `--output-dir` mới để tránh trộn bản cũ và mới. PDF chỉ được tạo
khi đã có đủ số ảnh của tập.

Công cụ chỉ nhận URL tập truyện HTTPS trên `truyentuoitho.com` và ảnh từ máy chủ
được trang này dùng. Nó không đăng nhập, mở khóa nội dung có giới hạn quyền truy
cập, hay tải các tập khác. Chỉ lưu nội dung mà bạn có quyền sử dụng.
