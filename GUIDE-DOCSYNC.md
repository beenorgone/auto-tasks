# Hướng dẫn DocSync

DocSync nhận diện và sắp xếp tài liệu, theo dõi hồ sơ trên Kanban và hiển thị việc cần làm tiếp theo. Có thể sử dụng bằng terminal hoặc giao diện web cục bộ.

- Thư mục chương trình: `/home/beenorgone/Documents/.auto-tasks`
- Giao diện web: <http://127.0.0.1:8787>
- Thư mục tài liệu mặc định: `~/Documents/<năm hiện tại>`; trong năm 2026 là `~/Documents/2026`.

## 1. Bắt đầu nhanh

Máy hiện tại đã có lệnh `docsync`. Nếu terminal đang mở từ trước khi cập nhật, nạp lại alias:

```bash
source ~/.bash_aliases
```

Sau đó xem sức khỏe hệ thống và hàng đợi:

```bash
docsync status
docsync scan
docsync board
```

`docsync` không có tham số tương đương `docsync board`: chỉ xem bảng. Lệnh này không còn tự chạy chuyển file như alias cũ.

Nếu shell chưa nhận lệnh, gọi trực tiếp:

```bash
/home/beenorgone/Documents/.auto-tasks/docsync board
```

## 2. Quy trình làm việc hằng ngày

### Bước 1 — Quét và xem hồ sơ

```bash
docsync scan
docsync board
```

`scan` cập nhật chỉ mục và dashboard; không di chuyển tài liệu. Mỗi thẻ hiển thị tên file, nhóm phân loại, lý do, đích đề xuất, mã hồ sơ (`dossier`) và hành động tiếp theo.

Bảng mặc định hiển thị tối đa 8 thẻ mỗi cột. Xem toàn bộ một cột:

```bash
docsync board --stage needs_review --limit 0
docsync board --stage ready --limit 0
```

### Bước 2 — Xử lý hồ sơ chờ duyệt

Xem các mã nhóm phân loại:

```bash
docsync rules
```

Lấy mã `dossier` từ thẻ, rồi ghi quyết định. Thay `DOSSIER_ID` trong các ví dụ bằng mã thực tế:

```bash
docsync review DOSSIER_ID legal_corporate --note "Đã kiểm tra, đây là hồ sơ pháp lý"
docsync set-status DOSSIER_ID waiting_external
```

Khi đã duyệt xong phân loại:

```bash
docsync review DOSSIER_ID legal_corporate --status approved --note "Đã xác nhận nhóm và đích lưu"
```

Một `dossier` có thể gồm nhiều file. Quyết định duyệt áp dụng cho các file đang hoạt động trong hồ sơ đó; hãy kiểm tra các thẻ có cùng mã trước khi dùng lệnh.

Quyết định duyệt thủ công được giữ khi quét lại nếu nội dung file chưa đổi. Khi nội dung đổi, file được phân loại lại.

### Bước 3 — Xem trước thao tác chuyển file

```bash
docsync preview
```

Kiểm tra đường dẫn nguồn, đích và lý do trong kết quả. Preview có thể cập nhật log, cấu hình rule, chỉ mục và dashboard; tài liệu nguồn không bị di chuyển, đổi tên hoặc xóa. Preview không chạy rclone.

Cột `ready` là danh sách ứng viên theo rule. Preview cho biết kế hoạch thực tế, bao gồm kiểm tra file đã xử lý, trùng nội dung và các bộ xử lý kế toán.

### Bước 4 — Áp dụng và kiểm tra

Khi đã kiểm tra kế hoạch:

```bash
docsync apply
docsync board
docsync status
```

`apply` chạy trên toàn bộ phạm vi tiếp nhận của organizer. Lọc `board --stage ...` chỉ thay đổi cách xem bảng, không giới hạn phạm vi Apply.

Ngoài 4 rule managed, Apply còn chạy các bộ xử lý kế toán hiện có: hóa đơn, UNC, PO/MO, xử lý trùng và tiếp nhận file. Các quy tắc cũ như dọn log ở gốc năm và gom file theo kỳ vẫn thuộc organizer.

Tài liệu PO rời được lưu tại `<Documents>/<năm>/20_BAO_CAO/Nhap hang`. Bộ hồ sơ `ho so PO….zip` thuộc kho `ho so mua vao/<năm>/<nhóm>/<MM>`, không phải báo cáo PO. ZIP mới tại thư mục tiếp nhận được giữ lại để kiểm tra nhóm nhập khẩu/trong nước, trước cả bước xử lý trùng; không tự suy đoán nhóm.

Chỉ khi muốn chạy thêm đồng bộ project ra ngoài bằng rclone:

```bash
docsync apply --sync-project
```

`docsync apply` thông thường không chạy rclone.

## 3. Các cột Kanban

Các cột thể hiện trạng thái hồ sơ; không bắt buộc mỗi file đi qua tất cả các cột.

| Cột | Giá trị dùng với `board --stage` | Việc tiếp theo |
|---|---|---|
| Chờ duyệt | `needs_review` | Xem nội dung, chọn nhóm, ghi quyết định |
| Đang xử lý | `in_progress` | Hoàn thiện hồ sơ |
| Chờ bên ngoài | `waiting_external` | Theo dõi phản hồi hoặc tài liệu bổ sung |
| Sẵn sàng chuyển | `ready` | Chạy Preview, kiểm tra rồi Apply |
| Đã lưu hồ sơ | `filed` | Kiểm tra tài liệu tại đích |
| Lưu trữ | `archived` | Không có việc tiếp theo trong hàng đợi |

`ready` là trạng thái hiển thị suy ra từ rule và vị trí file; không phải giá trị của `set-status`.

`set-status` chấp nhận: `captured`, `auto_classified`, `needs_review`, `in_progress`, `waiting_external`, `approved`, `filed`, `archived`.

`approved` nghĩa là đã duyệt phân loại. Với nhóm `review_only`, thẻ vẫn yêu cầu chuyển thủ công; lệnh duyệt không bật tự động chuyển vào đích đề xuất. `set-status filed` và `set-status archived` chỉ ghi trạng thái, không chuyển file. Chỉ đánh dấu `filed` sau khi đã kiểm tra việc lưu hồ sơ.

Board kết hợp file trực tiếp ở gốc năm với các file đã có trong chỉ mục WIP. Dùng `scan` để tìm file mới trong các thư mục WIP được cấu hình; đây không phải công cụ quét đệ quy toàn bộ máy tính.

## 4. Rule phân loại

### Các nhóm tự chuyển theo managed rules

Các rule này áp dụng cho file trực tiếp tại gốc thư mục năm hiện tại, sau các bước kiểm tra của organizer.

| Mã nhóm | Nhận diện chính | Đích trong thư mục năm |
|---|---|---|
| `vat` | Tên chứa `iv-mtt-batch` hoặc `iv-company-batch` | `AT. VAT` |
| `shopee_reports` | Mẫu `Order.` hoặc `orders_YYYY-MM-DD_YYYY-MM-DD.xlsx` | `AT. Shopee reports` |
| `ghtk_reports` | Mẫu `GHTK_` hoặc `bien_ban_doi_soat` | `AT. GHTK reports` |
| `bank_statements` | Mẫu `SAOKE`/`sao ke`; các tên giao dịch chung phải kiểm tra nội dung | `AT. SAOKE` |

Với `Danh sach giao dich*.xls/xlsx` và `lich-su-giao-dich*.xls/xlsx`, DocSync cần xác nhận thông tin ngân hàng, tài khoản và số dư hoặc ghi nợ/ghi có. File không đọc được, vượt giới hạn hoặc không đủ bằng chứng sẽ chờ duyệt. Bộ đọc XLS cần môi trường `.venv-docsync` và thư viện trong `requirements-docsync.txt`.

### Các nhóm chỉ đề xuất để duyệt

| Mã nhóm | Nội dung |
|---|---|
| `finance_ops` | Kế toán và vận hành |
| `quality_regulatory` | Chất lượng, kiểm nghiệm, công bố, chứng nhận |
| `packaging` | Nhãn, bao bì, in ấn |
| `commerce` | Bán hàng, bảng giá, thương mại |
| `legal_corporate` | Pháp lý và hồ sơ doanh nghiệp |
| `sourcing_logistics` | Nhập hàng và logistics |
| `technical` | Tài liệu kỹ thuật, mã nguồn, bộ cài |

Các nhóm này không tự chuyển theo đích đề xuất của WIP. Bộ xử lý kế toán riêng vẫn có thể nhận diện hóa đơn hoặc UNC. Quyết định trên Kanban không thay thế bộ xử lý kế toán của organizer.

Dùng `docsync rules` để xem cấu hình đang áp dụng. File rule thực tế nằm tại `~/Documents/<năm>/.wip/rules.json`; bảng trên mô tả cấu hình chuẩn, không thay thế các chỉnh sửa riêng trong file này.

## 5. Dùng giao diện web

Mở <http://127.0.0.1:8787> trên máy chạy DocSync.

| Điều khiển | Chức năng |
|---|---|
| Preview routing | Xem trước thao tác, sau đó cập nhật Kanban |
| Refresh Kanban | Quét lại chỉ mục và dashboard |
| Open Kanban | Mở dashboard hồ sơ |
| Apply changes | Mở bước xác nhận chạy organizer |
| Also run the final rclone project sync | Bật thêm đồng bộ ra ngoài cho lần Apply đó |
| System health · Intake Kanban | Xem watcher, lịch chạy, số lượng hồ sơ và việc tiếp theo |

Thông tin sức khỏe và hàng đợi trên web được làm mới theo chu kỳ 30 giây. Dashboard HTML được tạo khi Scan/Preview/Apply hoàn tất; mở lại trang Kanban để xem bản mới.

## 6. Tự động chạy và khóa chống trùng

- Watcher theo dõi file trực tiếp trong `~/Documents`, `~/Downloads` và thư mục năm hiện tại; không theo dõi đệ quy tất cả thư mục con.
- Chu kỳ kiểm tra là 5 giây; chờ 20 giây không có thay đổi mới, rồi chạy. Khoảng nghỉ tối thiểu giữa các lượt là 60 giây.
- File ẩn, symlink và file tải dở `.part`, `.crdownload`, `.tmp` được bỏ qua khi theo dõi.
- Các file có sẵn lúc watcher khởi động chờ timer hoặc một lần Apply chủ động.
- Watcher chạy không kèm rclone. Timer hiện có chạy mỗi 6 giờ và giữ hành vi đồng bộ project của nó.
- CLI, web, timer và watcher dùng chung khóa organizer. Nếu đang có lượt khác, lần chạy mới trả mã `75` (`BUSY`); watcher sẽ thử lại.

## 7. Xử lý tình huống thường gặp

| Hiện tượng | Cách xử lý |
|---|---|
| Gõ `docsync` vẫn chạy kiểu cũ | Chạy `source ~/.bash_aliases`, rồi kiểm tra `type docsync` |
| `command not found` | Gọi trực tiếp launcher bằng đường dẫn ở phần 1 |
| Có file nhưng chưa có mã dossier | Chạy `docsync scan`, rồi xem lại board |
| Đã `approved` nhưng file chưa chuyển | Xem `docsync rules`; nhóm `review_only` cần lưu hồ sơ thủ công |
| Báo `BUSY` hoặc mã 75 | Xem `docsync status`, chờ lượt hiện tại kết thúc rồi thử lại |
| Sao kê nằm ở Chờ duyệt | Đọc lý do trên thẻ; kiểm tra nội dung, định dạng và khả năng đọc workbook |
| File ở thư mục con không xuất hiện | Kiểm tra thư mục đó có nằm trong phạm vi WIP; chạy `scan` |
| `inactive/dead` ở organizer service | Có thể bình thường: đây là dịch vụ chạy từng lượt; xem `Result` và lần hoàn tất |
| Watcher `failed` hoặc `auto-restart` | Xem journal theo lệnh dưới đây |
| Web chưa cập nhật số lượng | Chờ chu kỳ 30 giây hoặc tải lại trang |

```bash
systemctl --user status ivar-watcher.service --no-pager
journalctl --user -u ivar-watcher.service -n 50 --no-pager
systemctl --user list-timers --all --no-pager
```

Sau khi xử lý nguyên nhân lỗi watcher:

```bash
systemctl --user reset-failed ivar-watcher.service
systemctl --user restart ivar-watcher.service
```

Không xóa file `organizer.lock` để vượt khóa; hãy kiểm tra tiến trình đang chạy.

## 8. Vị trí dữ liệu và tài liệu kỹ thuật

| Thành phần | Vị trí |
|---|---|
| Rule đang dùng | `~/Documents/<năm>/.wip/rules.json` |
| Chỉ mục và lịch sử WIP | `~/Documents/<năm>/.wip/index.sqlite` |
| Dashboard HTML | `~/Documents/<năm>/.wip/dashboard.html` |
| Log organizer theo ngày | `~/Documents/.auto-tasks/ivar-documents-organizer/logs/` |
| Trạng thái file đã xử lý | `~/Documents/.auto-tasks/ivar-documents-organizer/processed.db` |
| Dữ liệu nhà cung cấp | `~/Documents/.auto-tasks/ivar-documents-organizer/vendors.db` |

Xuất dữ liệu để công cụ khác đọc:

```bash
docsync board --json
docsync board --stage ready --json
docsync status --json
```

Với `board --stage ... --json`, danh sách `cards` được lọc; `counts` vẫn là số lượng toàn bảng. `--limit` chỉ giới hạn cách in bảng dạng văn bản.

Đọc chỉ mục của một năm khác bằng tùy chọn đặt trước tên lệnh:

```bash
docsync --year-root "$HOME/Documents/2026" board
```

Preview/Apply chỉ hỗ trợ thư mục năm hiện tại. `status` phản ánh các dịch vụ chung của máy.

Hướng dẫn cài đặt môi trường, triển khai dịch vụ và chạy kiểm thử: [README-wip-organizer.md](README-wip-organizer.md).
