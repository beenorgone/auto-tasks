> Từ 29/10/2026, thư mục làm việc mặc định là `~/Documents/WIP & Reference`.
> Các thư mục tháng cũ được giữ dưới `2026/2026-MM` (ví dụ `2026/2026-10`).
> Từ 12/11/2026, file mới phân loại theo chủ đề công việc; không tự gom theo đuôi file vào tháng mới.
> Hồ sơ, báo cáo, `00_INBOX`, `.wip` và `duplicates` nằm trực tiếp ở gốc.
> CLI, web và watcher cùng dùng gốc mặc định này; có thể cấu hình bằng `DOCSYNC_ROOT`.
> `docsync --root <path>` và tên tham số cũ `--year-root` đều được hỗ trợ.

# Hướng dẫn DocSync

DocSync nhận diện và sắp xếp tài liệu, hiển thị việc cần làm tiếp theo và theo dõi lịch sử phân loại. Có thể sử dụng bằng terminal hoặc giao diện web cục bộ.

- Thư mục chương trình: `/home/beenorgone/Documents/.auto-tasks`
- Giao diện web: <http://127.0.0.1:8787>
- Thư mục tài liệu mặc định: `~/Documents/WIP & Reference` (dùng chung qua các năm).

Máy Ubuntu cài mới dùng [bộ chạy DocSync cho Ubuntu](packaging/docsync-ubuntu/README.md).

## Cấu trúc theo chủ đề công việc từ 12/11/2026

```text
WIP & Reference/
├── Sản xuất/
│   └── Bao bì/
├── Mua hàng - dịch vụ/
│   └── PO/
├── Giao vận/
│   ├── Shopee/
│   ├── GHTK/
│   └── Chứng từ/
├── Kinh doanh/
│   └── B2B/Bang gia/ và B2B/Doi soat cong no/
├── QC/
│   └── Quy chuẩn/
├── Kế toán - tài chính/
│   ├── Sao kê/
│   ├── VAT/
│   ├── Hóa đơn/
│   └── Biểu mẫu/Sao kê/
├── Pháp Lý/
├── .docsync/       # Nhật ký kiểm chứng và cách ly mã kỹ thuật
├── .wip/          # Chỉ mục, cấu hình và Kanban
├── 2026/          # Các thư mục lưu theo tháng đang có
├── Others/
└── duplicates/
```

Thư mục thể hiện **chủ đề công việc**; trạng thái chờ duyệt/đang xử lý/đã lưu nằm
trên Kanban. Các nhóm cũ `10_HO_SO`, `20_BAO_CAO`, `80_THAM_CHIEU` được chuyển
qua bảng ánh xạ có kiểm tra hash, chỉ mục và nhật ký; chỉ bỏ thư mục nguồn khi rỗng.
Mục chưa xác định được chủ đề giữ nguyên vị trí và gửi bot Telegram để bạn chọn.

Chỉ tài liệu có quy tắc rõ, một chủ đề và đủ độ tin cậy mới tự chuyển. Tài liệu
có quyết định thủ công được giữ lại để chốt; báo cáo Shopee/GHTK thuộc Giao vận,
file nhập sản phẩm/marketing Shopee vẫn cần xác định theo công việc thực tế.
ZIP hồ sơ PO tiếp tục dùng kho mua vào kế toán chính thức và quy tắc lịch sử NCC.
Đổi cấu trúc WIP không di chuyển kho bên ngoài này và không bật đồng bộ Drive.

Cầu nối Telegram gửi nhiều mục trong một tin, nhưng mỗi mục có mã riêng. Trả lời
`/ds MÃ NHÓM`, dùng mã hồ sơ 6 ký tự và mã nhóm `sx`, `mh`, `gv`, `kd`,
`qc`, `kt`, `pl`; chọn `g` để giữ nguyên. Ví dụ: `/ds a1b2c3 sx d4e5f6 kt`.
Một tin chốt tối đa 10 mục; hỗ trợ xuống dòng, dấu phẩy hoặc chấm phẩy giữa các cặp.
Lệnh `/ds` hiển thị hướng dẫn. `/docsync`, mã hồ sơ 32 ký tự và tên nhóm cũ vẫn dùng được. Quyết định gắn với toàn
bộ nội dung file/thư mục, chỉ tài khoản liên kết sở hữu yêu cầu được chốt trong
7 ngày. Nội dung thay đổi phải tạo yêu cầu mới; không ghi đè đích trùng.

Nhật ký chuyển và bản sao chỉ mục nằm ở `.docsync/Nhật ký/layout-migration-*.json`
và `.wip/layout-backup-*`. Lệnh Preview và xem Kanban không di chuyển tài liệu.

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

### Gửi file đã chọn lên ERP hoặc đối soát GHTK

Mở [File trong ngày](http://127.0.0.1:8787/day) để xem và phân loại file theo ngày
trên máy. Bảng lấy file mới, vừa chuyển hoặc vừa cập nhật trong **WIP & Reference** và
hiển thị nhóm từ quy tắc WIP. File tạm, file hệ thống, liên kết và bản trùng đã cách ly
được bỏ qua.
Thanh tab chung mở **Tổng quan**, **File trong ngày**, **Lịch sử hôm nay** và **Workflows**.
Tab **Lịch sử hôm nay** hiển thị lần phân loại gần nhất trong ngày của từng file,
gồm nhóm, nguồn, đích và kết quả đã chuyển hoặc chờ duyệt.

Để gắn một file, bấm **Gắn vào ERP**, chọn loại bản ghi, tìm bằng mã hoặc tên, rồi chọn
bản ghi và trường nhận file. Nếu tài khoản và bản ghi hỗ trợ AI, bạn có thể bật
**Phân tích file và tạo Ghi chú AI** trước khi bấm **Gắn file lên ERP**. Kết quả báo
file đã gắn hay đã tồn tại và AI có được xếp hàng hay chưa. Bảng này giữ file nguồn trên máy.

Mục **Tài khoản gửi ERP** lưu riêng Application Password đã duyệt `erp_attach` trên máy.
Nếu chưa lưu riêng, DocSync dùng tài khoản Workflows đã có. Các báo cáo đủ điều kiện
cho luồng tự động đang bật tiếp tục do watcher xử lý và hiện liên kết **Theo luồng EIM
tự động** cùng kết quả nếu đã có.

`docsync intake` dùng Application Password của chính tài khoản WordPress được
duyệt phạm vi đẩy file và luồng tương ứng. Mật khẩu nhập ẩn trong terminal ở
mỗi lần gọi. Có thể đặt `DOCSYNC_ERP_USER` để điền sẵn tên đăng nhập hoặc
`DOCSYNC_ERP_URL` để chọn một site thử nghiệm HTTPS. Site mặc định là
`https://ivarvietnam.com`.

```bash
docsync intake flows
docsync intake targets --type po --query 1234
docsync intake record --type po --id 1234
docsync intake attach ./hosomuahang.pdf --type po --id 1234 --field FIELD_KEY
docsync intake attach ./hosomuahang.pdf --type po --id 1234 --field FIELD_KEY --send
```

Thay `FIELD_KEY` bằng `key` từ lệnh `record`. Lệnh thiếu `--send`
chỉ tra quyền và đích trên site, rồi tính SHA-256 cục bộ để xem trước.
`--ai-mode summary` hoặc `--ai-mode analysis` yêu cầu luồng `erp_attach_ai`
và gửi nội dung file tới nhà cung cấp AI đã cấu hình để tạo Ghi chú AI.

```bash
docsync intake reconcile ./ghtk.csv --type carrier_delivery
docsync intake reconcile ./ghtk.csv --type carrier_delivery --send
docsync intake report SHA256_TU_KET_QUA
```

Dùng `pickup_cashflow` cho báo cáo dòng tiền lấy hàng. Luồng này có thể
xếp đơn sang Đang giao. Với `carrier_delivery`, bản xem trước hiện quyền
dựng lô hóa đơn nháp và tự hoàn thành đơn. Nếu quyền tự hoàn thành đang bật,
lệnh gửi yêu cầu thêm `--allow-order-completion`. Các quyền thực thi do
site kiểm tra lúc nhận file và lúc chạy tác vụ nền.
Nếu kết quả là `building`, tra tiếp bằng lệnh `report`. Khi mất kết nối,
kiểm tra theo SHA-256 trước khi gửi lại. File nguồn vẫn ở trên máy và file
trùng được API nhận diện theo nội dung. Trong CLI, dùng `--send` để gửi file.
Telegram Inbox tiếp tục quy trình lưu cục bộ.

### Quản lý workflows tự động trên máy

Mở [Workflows tự động](http://127.0.0.1:8787/workflows). Các cột **Đang bật**, **Đang tắt**
và **Đề xuất** cho biết file đầu vào, tác vụ và bước tiếp theo. Công tắc **Phân loại chứng từ
trên máy** điều khiển watcher. Lịch Documents Organizer định kỳ và cầu nối Telegram chạy riêng.

**GHTK · Đối soát lấy hàng** mặc định tắt. Lưu username WordPress và Application Password đã
được duyệt `eim_pickup_cashflow` trong mục **Tài khoản gửi EIM**, sau đó bật luồng. DocSync
lưu thông tin trong cấu hình người dùng `~/.config/docsync/intake.json` (hoặc theo
`XDG_CONFIG_HOME`), với quyền đọc ghi chỉ cho người dùng hiện tại. Lưu tài khoản watcher
mới sẽ tắt các luồng đối soát tự động để bạn duyệt và bật lại khi sẵn sàng.

Watcher nhận XLSX có tên mẫu **GHTK_bao_cao_van_hanh_va_dong_tien_DD_MM_S…** và có sheet
**BC Dòng tiền** trong `Giao vận/GHTK`. Chỉ file tạo hoặc cập nhật sau khi bật và ổn định ít
nhất 20 giây mới được chọn. Kết quả gần đây hiện mã báo cáo và số đơn khớp, chưa khớp.
Nếu DocSync đang dùng bố cục cũ, watcher cũng nhận file trong thư mục GHTK của bố cục đó.
Lỗi tạm thời có thời gian chờ trước khi thử lại. Đơn đủ điều kiện có thể được xếp hàng sang
**Đang giao**. Lỗi hoặc file sai mẫu hiện **Cần kiểm tra**.

**GHTK · Đối soát thanh toán** nhận biên bản CSV/XLSX có tên mẫu
`bien_ban_doi_soat_S…` trong `Giao vận/GHTK`. Watcher kiểm tra tiêu đề của mẫu trước
khi gửi. Nhật ký Workflows chuyển từ **Đang kiểm tra** sang **Đang gửi** sau khi
file và quyền gửi đều hợp lệ. Tài khoản cần được duyệt `eim_carrier_delivery`. Bật thêm **Dựng lô hóa đơn nháp**
khi tài khoản được duyệt `eim_carrier_draft_batches`. DocSync chọn chế độ tương ứng
cho từng biên bản. EIM giữ trạng thái đơn WooCommerce và xác nhận thanh toán ngoài
hai chế độ này. Quyền được kiểm tra lại ở mỗi lượt. File đã có trước thời điểm bật
được giữ nguyên.

Hồ sơ PO/QC dùng thao tác **Gắn vào ERP** trong tab **File trong ngày**. Hóa đơn,
sao kê và báo cáo sàn tiếp tục được phân loại cục bộ theo quy tắc đang dùng.

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

UNC và hóa đơn (PDF/XML) đã có bản lưu chỉ được coi là trùng khi khớp cả dung lượng và SHA-256 với file đích còn tồn tại. UNC trùng được giữ tại `<Documents>/WIP & Reference/duplicates`; hóa đơn trùng được đổi tên ngay cạnh nguồn với hậu tố `_duplicate` và số tránh trùng tên. Không ghi đè bản đã có và không xóa bản trùng. Preview kiểm tra cùng điều kiện nhưng không chuyển file. Không còn rule gom các file thông thường trùng nội dung vào `<năm>/<tháng>/duplicates`; các thư mục duplicates tháng đã có được giữ nguyên.

Tài liệu PO rời được lưu tại `<Documents>/WIP & Reference/Mua hàng - dịch vụ/PO`. Bộ hồ sơ `ho so PO….zip` thuộc kho `ho so mua vao/<năm>/<nhóm>/<MM>`, không phải báo cáo PO. ZIP mới bỏ qua bộ xử lý PO/trùng chung và được chuyển nguyên bộ khi lịch sử NCC có nhóm nhất quán. Trường hợp chưa rõ được giữ lại và gửi Telegram khi đã cấu hình cầu nối bên dưới.

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
| `vat` | Tên chứa `iv-mtt-batch` hoặc `iv-company-batch` | `Kế toán - tài chính/VAT` |
| `shopee_reports` | Mẫu `Order.` hoặc `orders_YYYY-MM-DD_YYYY-MM-DD.xlsx` | `Giao vận/Shopee` |
| `ghtk_reports` | Mẫu `GHTK_` hoặc `bien_ban_doi_soat` | `Giao vận/GHTK` |
| `bank_statements` | Mẫu `SAOKE`/`sao ke`; các tên giao dịch chung phải kiểm tra nội dung | `Kế toán - tài chính/Sao kê` |

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
| Preview routing | Xem trước thao tác, sau đó cập nhật chỉ mục |
| Cập nhật phân loại | Quét lại chỉ mục phân loại |
| Lịch sử hôm nay | Nhập lệnh phân loại và xem các lượt đã chuyển hoặc đang chờ duyệt |
| Cài đặt | Chọn gốc WIP, hai thư mục quét bổ sung và gốc đích kế toán |
| Apply changes | Mở bước xác nhận chạy organizer |
| Also run the final rclone project sync | Bật thêm đồng bộ ra ngoài cho lần Apply đó |
| System health · Intake | Xem watcher, lịch chạy, số lượng hồ sơ và việc tiếp theo |

Thông tin sức khỏe và hàng đợi trên **Tổng quan** được làm mới theo chu kỳ 30 giây.
Chọn **Tiếng Việt** hoặc **English** ở đầu trang để đổi ngôn ngữ Tổng quan.

Trong **Lịch sử hôm nay**, nhập **xem trước** (hoặc `docsync preview`) để xem đề xuất,
hay **phân loại** (hoặc `docsync apply`) để áp dụng quy tắc hiện hành và chuyển các file đủ điều kiện.
Bấm **Chạy lệnh** và xem tiến độ trong **Nhật ký chạy**. Bảng tự cập nhật sau lượt áp dụng.
Các lệnh trên tab này chạy với tùy chọn `--no-sync` cho bước đồng bộ rclone.
Nút **Làm mới** cập nhật các lượt được ghi nhận từ watcher hoặc nơi khác.

Để xử lý một file trong WIP, bấm **Phân loại** ở dòng **Chờ duyệt**. Chọn một trong bảy
nhóm công việc như trên bot Telegram, kiểm tra thư mục đích rồi bấm **Lưu lựa chọn**.
Chọn **Giữ lại** để DocSync bỏ qua đúng bản file đó trong các lượt tự động.
File ZIP hồ sơ PO tiếp tục dùng quy trình chọn **Trong nước** hoặc **Nhập khẩu** riêng.

Trong **File trong ngày** hoặc **Lịch sử hôm nay**, chọn các file cần xử lý và chọn
**Phân loại**, **Giữ lại**, **Đổi tên** hoặc **Xóa file** rồi bấm **Tiếp tục**. Đổi tên
nhận một tên đầy đủ cho mỗi file. Xóa đưa file vào `.docsync/Trash` trong WIP.
Nhật ký phân loại hiển thị đích đổi tên hoặc vị trí trong thùng rác để tìm lại.

Trong **Cài đặt**, nhập đường dẫn của thư mục đã tồn tại rồi bấm **Lưu thư mục**.
Hai ô quét bổ sung có thể để trống. App, CLI và watcher dùng giá trị đã lưu
từ lượt tiếp theo. Mỗi gốc WIP có chỉ mục và công tắc workflow riêng.
Biến môi trường đặt sẵn cho dịch vụ hoặc terminal được ưu tiên và hiện chỉ đọc trên web.

Trong **Cài đặt → Quy tắc phân loại và chuyển file → Biểu mẫu**, mở quy tắc để
chỉnh đích, từ khóa, cách xử lý hoặc bật/tắt. Dùng **Lên / Xuống** để đổi thứ tự,
rồi bấm **Lưu quy tắc**. Chế độ **JSON** và CLI dùng cùng cấu hình `.wip/rules.json`.
User script xuất cấu hình, chỉnh bằng công cụ JSON rồi nhập lại:

```sh
docsync rules --export > rules.json
# Chỉnh rules.json bằng user script hoặc trình soạn thảo.
docsync rules --import rules.json
docsync preview
```

Dùng `docsync rules --import -` để nhận JSON qua stdin. Thứ tự trong `categories`
là thứ tự thử mẫu, `enabled: false` tắt một quy tắc. Đích tự động nằm trong nhóm
công việc của WIP. Mặc định giữ **Giao vận/GHTK** cho báo cáo GHTK để watcher
EIM theo dõi đúng thư mục. Xử lý hóa đơn, UNC và hồ sơ PO ZIP có luồng chuyên biệt.

## 6. Tự động chạy và khóa chống trùng

- Watcher theo dõi file trực tiếp trong `~/Documents`, `~/Downloads`, gốc năm, `00_INBOX` và `Kế toán - tài chính/Hóa đơn`. Các thư mục sâu hơn trong Inbox được xử lý khi chạy Apply hoặc timer; watcher không theo dõi đệ quy.
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
- Bộ hồ sơ ZIP mang mã PO vẫn thuộc kho mua vào theo tháng; giữ nguyên nhóm nhập khẩu/trong nước và toàn bộ bộ hồ sơ. Chỉ tài liệu PO rời thuộc báo cáo `Mua hàng - dịch vụ/PO`.
- Chuyển nguyên thư mục hồ sơ, bao gồm ghi chú; không giải nén ZIP. Không suy đoán tháng từ ngày sửa file.
- Kanban: `ready` khi cần chuyển, `filed` khi đúng tháng, `needs_review` khi tên/ngày/năm không rõ hoặc đích trùng. Với rule này, sửa tên/vị trí hoặc kiểm tra đích trùng rồi chạy Preview; không dùng `review <dossier>`.
- `docsync rules` hiển thị rule; `docsync preview` chỉ in kế hoạch. `docsync apply` áp dụng trong khóa chung, ghi nguồn/đích vào `ivar-documents-organizer/purchase-months.jsonl` để truy vết/hoàn tác thủ công.
- Watcher kiểm tra thêm các năm, nhóm và thư mục quý/tháng trong phạm vi trên mỗi 5 giây; theo dõi thay đổi của file đính kèm trong bộ hồ sơ và chờ yên 20 giây. Không quét các nhánh tùy ý ngoài cấu trúc này.
- Không ghi đè đích, không đi qua symlink, không chuyển bộ hồ sơ còn `.part`, `.crdownload`, `.tmp`. Hồ sơ có sẵn lúc watcher khởi động vẫn chờ Apply/timer hoặc thay đổi mới.
- Rule chỉ sắp xếp thư mục local; không bật thêm rclone. Lượt `--rescan-unc` không áp dụng rule này.

### ZIP mới và quyết định Telegram

- Nhận ZIP `ho so PO… - YYYY-MM-DD - NCC ….zip` tại gốc WIP, `Kế toán - tài chính/Hóa đơn` và Inbox cũ còn sót (kể cả thư mục con), gốc Documents và Downloads.
- Chỉ so tên NCC đầy đủ, chuẩn hóa Unicode và chữ hoa/thường. Không ghép gần đúng. Chỉ dùng hồ sơ đã lưu đúng tháng trong nhóm `nhap khau` hoặc `trong nuoc`; lịch sử không có nhóm không phải căn cứ suy luận.
- Nếu mọi hồ sơ lịch sử có cùng nhóm, tự chuyển theo năm/tháng trên tên ZIP. Nếu không có lịch sử hoặc có cả hai nhóm, giữ lại chờ bạn quyết định.
- Kanban dùng chung đề xuất, lý do và đích; yêu cầu đã gửi Telegram ở trạng thái `waiting_external`. Không ghi đè; ghi SHA-256, căn cứ lịch sử và nguồn/đích trong `purchase-months.jsonl`.
- Cầu nối `docsync_telegram.py` dùng SSH và WP-CLI tới bot ERP đã cấu hình. Token bot không được sao chép về máy local. Mã nguồn MU plugin: `telegram/docsync-telegram.php`, cài tại `wp-content/mu-plugins/ivar-docsync-telegram.php` trên ERP.
- Cấu hình local, không commit: `ivar-documents-organizer/purchase-telegram.json`, gồm `host`, `wp_path`, `login` (đúng tài khoản ERP), `bot` (username không có @). Phải có liên kết Telegram đã được ERP duyệt và SSH hoạt động bằng khóa.
- Cài hai unit `systemd/docsync-telegram.{service,timer}` vào `~/.config/systemd/user/`, chạy `systemctl --user daemon-reload` rồi `systemctl --user enable --now docsync-telegram.timer`. Timer kiểm tra mỗi phút; dùng khóa organizer chung và chờ file yên ít nhất 20 giây. Dừng bằng `systemctl --user disable --now docsync-telegram.timer`.
- Bot gửi mã riêng cho từng file. Trả lời `/ds MÃ tn` (trong nước), `/ds MÃ nk` (nhập khẩu) hoặc `/ds MÃ g` (giữ lại). Chỉ tài khoản liên kết sở hữu yêu cầu được quyết định, một lần, trong 7 ngày. Lệnh không gọi AI và không sửa dữ liệu PO của ERP.
- Phê duyệt gắn với SHA-256 đúng file; file đổi thì cần yêu cầu mới. `giu_lai` tiếp tục có hiệu lực dù về sau xuất hiện lịch sử NCC. Đích trùng/ngày không hợp lệ vẫn cần xử lý riêng, không được vượt qua bằng cách chọn nhóm.
- Trạng thái và chống gửi lặp lưu tại `purchase-telegram-state.json` (không commit). Đề xuất ERP hết hạn sau 7 ngày, tự dọn sau 30 ngày. Yêu cầu hết hạn giữ nguyên file; cần tạo lại trạng thái yêu cầu sau khi kiểm tra.
- Máy local phải đang chạy và có mạng để nhận quyết định. Bot xác nhận **đã ghi nhận** trước; sau khi chuyển và kiểm tra hash, bot mới báo **đã chuyển**. Không bật đồng bộ Drive.

Hướng dẫn kỹ thuật: [triển khai, setup, logic, nghiệm thu và rollback](docs/DOCSYNC-DEPLOYMENT.md).


## Yêu cầu xóa file qua Telegram

- `/rm a1b2c3` hoặc `/ds a1b2c3 rm`: yêu cầu xóa file của đề xuất đang chờ quyết định.
- `/rm a1b2c3 d4e5f6`: tối đa 10 mã/tin; không nhận đường dẫn, wildcard hay tùy chọn shell.
- Chỉ chủ tài khoản Telegram đang liên kết và đủ quyền được chốt; giữ hạn 7 ngày và mỗi đề xuất chỉ chốt một lần.
- Bot báo đã nhận yêu cầu trước. Bridge giữ `organizer.lock`, kiểm tra SHA-256 rồi chuyển file vào `$STATE_DIR/telegram-trash/<id>/<tên file>`; không xóa vĩnh viễn, không xóa thư mục, không ghi đè hoặc đi qua symlink.
- Chỉ báo hoàn tất sau khi kiểm tra file trong thùng rác. File đã đổi hoặc bị chặn được giữ để kiểm tra; lỗi I/O được timer thử lại.
- Chỉ mục Telegram lưu đường dẫn nguồn, hash và `trash` để khôi phục trên máy. Muốn khôi phục, tạm dừng timer/watcher, đối chiếu hash và chuyển về đường dẫn nguồn nếu nguồn đang trống; không ghi đè file mới.
- Thùng rác là dữ liệu vận hành, không đưa vào Git; cần triển khai MU plugin mới và bridge local cùng nhau.


### Ảnh từ mới cho WordQuest

Với ảnh đang chờ DocSync, gửi `/ds MÃ wq`. Hỗ trợ JPG/PNG/WebP tối đa 6 MB,
50 từ/cụm từ tiếng Anh. Bot đọc ảnh qua AI ERP rồi gửi link WordQuest để bạn
kiểm tra chữ và nghĩa; chỉ sau khi bấm **Lưu giỏ & tạo bài ôn** mới thêm vào
thư viện. Giỏ hỗ trợ các dạng chọn nghĩa, nhìn nghĩa đoán từ, nghe, điền chữ
và xếp chữ có sẵn. Ảnh gốc không bị chuyển hay xóa; danh sách từ được gửi trong
phần fragment của link, không đưa ảnh lên host công khai. Bài học dài có thể
chia thành nhiều link. Nếu đọc ảnh lỗi, gửi lại `/ds MÃ wq` để thử cùng ảnh;
ảnh đã thay nội dung cần một đề xuất mới. WordQuest vẫn giữ quyền riêng tư cũ,
tiến độ và giỏ đã lưu nằm trên trình duyệt đang sử dụng.
