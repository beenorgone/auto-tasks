> Từ 29/10/2026, thư mục làm việc mặc định là `~/Documents/WIP & Reference`.
> Các thư mục tháng cũ được giữ dưới `2026/2026-MM` (ví dụ `2026/2026-10`).
> Từ các năm sau, rule gom tháng dùng `<năm>/<năm>-<MM>` trong cùng gốc này.
> Hồ sơ, báo cáo, `00_INBOX`, `.wip` và `duplicates` nằm trực tiếp ở gốc.
> CLI, web và watcher cùng dùng gốc mặc định này; có thể cấu hình bằng `DOCSYNC_ROOT`.
> `docsync --root <path>` và tên tham số cũ `--year-root` đều được hỗ trợ.

# Hướng dẫn DocSync

DocSync nhận diện và sắp xếp tài liệu, theo dõi hồ sơ trên Kanban và hiển thị việc cần làm tiếp theo. Có thể sử dụng bằng terminal hoặc giao diện web cục bộ.

- Thư mục chương trình: `/home/beenorgone/Documents/.auto-tasks`
- Giao diện web: <http://127.0.0.1:8787>
- Thư mục tài liệu mặc định: `~/Documents/WIP & Reference` (dùng chung qua các năm).

## Cấu trúc thư mục từ 29/10/2026

```text
WIP & Reference/
├── 00_INBOX/Hoa don/
├── 10_HO_SO/
│   ├── Ke toan/
│   ├── Chat luong/
│   ├── Bao bi/
│   ├── Kinh doanh/B2B/
│   │   ├── Bang gia/
│   │   └── Doi soat cong no/
│   ├── Phap ly/
│   └── Nhap hang/
├── 20_BAO_CAO/
│   ├── VAT/
│   ├── Shopee/
│   ├── GHTK/
│   └── Sao ke/
├── 80_THAM_CHIEU/
│   ├── Bieu mau/Sao ke/
│   ├── Quy chuan/
│   └── Ma QR/
├── 90_CACH_LY_KY_THUAT/
├── 99_NHAT_KY/
├── Others/
├── 2026/
│   └── 2026-01/ ... 2026-10/   # Các tháng đã có
├── duplicates/                 # UNC/hóa đơn trùng, chờ dọn sau
└── .wip/
```

Tên thư mục mô tả nội dung. Trạng thái xử lý nằm trên Kanban; `10_WIP` và
`90_NEEDS_REVIEW` cũ chỉ được bỏ khi rỗng. B2B hiện chỉ có **bảng giá** và
**đối soát công nợ**. Phân loại B2B vẫn cần duyệt, không tự chuyển theo tên.

`10_HO_SO` là nơi làm việc với hồ sơ nghiệp vụ. Bộ xử lý hóa đơn/UNC vẫn sử dụng
đích kế toán chính thức đang cấu hình trong project IVAR.

Đợt đổi cấu trúc giữ nguyên tên file và cấu trúc bên trong từng bộ hồ sơ; không
suy diễn tháng sao kê hay số tài khoản từ ngày tải file. Báo cáo chưa có cấu
trúc kỳ rõ ràng được giữ trong nhóm báo cáo tương ứng. Phạm vi Scan bao gồm
Inbox, Hồ sơ, Báo cáo, Tham chiếu và Cách ly kỹ thuật, không bao gồm `Others`.

Nhật ký chuyển nằm trong `99_NHAT_KY/layout-migration-*.json`; bản sao chỉ mục,
rule và database đường dẫn trước khi chuyển nằm trong `.wip/layout-backup-*`.
Di chuyển thủ công bằng file manager có thể làm lệch chỉ mục; hãy dùng quy trình
migration có kiểm tra hash và cập nhật đường dẫn khi đổi cấu trúc tiếp.

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

UNC và hóa đơn (PDF/XML) đã có bản lưu chỉ được coi là trùng khi khớp cả dung lượng và SHA-256 với file đích còn tồn tại. Bản trùng được giữ tại `<Documents>/WIP & Reference/duplicates` để dọn sau; không ghi đè bản đã có và không xóa bản trùng. Preview kiểm tra cùng điều kiện nhưng không chuyển file. Không còn rule gom các file thông thường trùng nội dung vào `<năm>/<tháng>/duplicates`; các thư mục duplicates tháng đã có được giữ nguyên.

Tài liệu PO rời được lưu tại `<Documents>/WIP & Reference/20_BAO_CAO/Nhap hang`. Bộ hồ sơ `ho so PO….zip` thuộc kho `ho so mua vao/<năm>/<nhóm>/<MM>`, không phải báo cáo PO. ZIP mới tại thư mục tiếp nhận được giữ lại để kiểm tra nhóm nhập khẩu/trong nước, trước cả bước xử lý trùng; không tự suy đoán nhóm.

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

Các rule này áp dụng cho file tại gốc năm và trong `00_INBOX`, sau các bước kiểm tra của organizer.

| Mã nhóm | Nhận diện chính | Đích trong thư mục năm |
|---|---|---|
| `vat` | Tên chứa `iv-mtt-batch` hoặc `iv-company-batch` | `20_BAO_CAO/VAT` |
| `shopee_reports` | Mẫu `Order.` hoặc `orders_YYYY-MM-DD_YYYY-MM-DD.xlsx` | `20_BAO_CAO/Shopee` |
| `ghtk_reports` | Mẫu `GHTK_` hoặc `bien_ban_doi_soat` | `20_BAO_CAO/GHTK` |
| `bank_statements` | Mẫu `SAOKE`/`sao ke`; các tên giao dịch chung phải kiểm tra nội dung | `20_BAO_CAO/Sao ke` |

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

- Watcher theo dõi file trực tiếp trong `~/Documents`, `~/Downloads`, gốc năm, `00_INBOX` và `00_INBOX/Hoa don`. Các thư mục sâu hơn trong Inbox được xử lý khi chạy Apply hoặc timer; watcher không theo dõi đệ quy.
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

Chọn thư mục chỉ mục bằng tùy chọn đặt trước tên lệnh:

```bash
docsync --root "$HOME/Documents/WIP & Reference" board
```

Preview/Apply chỉ hỗ trợ gốc làm việc đang cấu hình (`DOCSYNC_ROOT`, mặc định là `WIP & Reference`). `--year-root` vẫn là tên tham số tương thích. `status` phản ánh các dịch vụ chung của máy.

Hướng dẫn cài đặt môi trường, triển khai dịch vụ và chạy kiểm thử: [README-wip-organizer.md](README-wip-organizer.md).

## 9. Tự xếp hồ sơ mua vào theo tháng

Rule `purchase_months` dùng chung cho organizer (CLI/web/timer) và watcher, tại:
`~/Documents/Drives/gDriveProjects/Working/Projects/IVAR/Accounting - Ke toan/Data - Du lieu/ho so mua vao`.
Nếu đặt `IVAR_DATA`, rule dùng thư mục `ho so mua vao` bên trong đường dẫn đó.

- Nhận file hoặc thư mục bắt đầu bằng `ho so `, có đúng một ngày hợp lệ `YYYY-MM-DD` trên tên; năm phải khớp thư mục năm.
- Đích: `<năm>/<MM>/` hoặc `<năm>/nhap khau|trong nuoc/<MM>/`. Hồ sơ trong `Q1`–`Q4` hoặc tháng sai cũng được đưa về tháng đúng; không tự xóa thư mục rỗng.
- Bộ hồ sơ ZIP mang mã PO vẫn thuộc kho mua vào theo tháng; giữ nguyên nhóm nhập khẩu/trong nước và toàn bộ bộ hồ sơ. Chỉ tài liệu PO rời thuộc báo cáo `20_BAO_CAO/Nhap hang`.
- Chuyển nguyên thư mục hồ sơ, bao gồm ghi chú; không giải nén ZIP. Không suy đoán tháng từ ngày sửa file.
- Kanban: `ready` khi cần chuyển, `filed` khi đúng tháng, `needs_review` khi tên/ngày/năm không rõ hoặc đích trùng. Với rule này, sửa tên/vị trí hoặc kiểm tra đích trùng rồi chạy Preview; không dùng `review <dossier>`.
- `docsync rules` hiển thị rule; `docsync preview` chỉ in kế hoạch. `docsync apply` áp dụng trong khóa chung, ghi nguồn/đích vào `ivar-documents-organizer/purchase-months.jsonl` để truy vết/hoàn tác thủ công.
- Watcher kiểm tra thêm các năm, nhóm và thư mục quý/tháng trong phạm vi trên mỗi 5 giây; theo dõi thay đổi của file đính kèm trong bộ hồ sơ và chờ yên 20 giây. Không quét các nhánh tùy ý ngoài cấu trúc này.
- Không ghi đè đích, không đi qua symlink, không chuyển bộ hồ sơ còn `.part`, `.crdownload`, `.tmp`. Hồ sơ có sẵn lúc watcher khởi động vẫn chờ Apply/timer hoặc thay đổi mới.
- Rule chỉ sắp xếp thư mục local; không bật thêm rclone. Lượt `--rescan-unc` không áp dụng rule này.
