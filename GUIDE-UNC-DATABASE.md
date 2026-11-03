# Hướng Dẫn Cơ Sở Dữ Liệu Tra Cứu UNC (IVAR Payment Orders Database)

Tài liệu hướng dẫn tra cứu, quản lý và kiểm chứng cơ sở dữ liệu Ủy nhiệm chi (UNC) cho bộ phận Kế toán IVAR.

## Mở ứng dụng trên Chrome

Tìm **IVAR UNC** trong danh sách ứng dụng của Ubuntu, hoặc mở `http://127.0.0.1:8788/` trên Chrome.
Ứng dụng chạy nền qua user service `ivar-unc.service`, tự khởi động khi đăng nhập.
Biểu tượng IVAR UNC mở Chrome ở chế độ cửa sổ ứng dụng và khởi động lại service nếu cần.

```bash
bash /home/beenorgone/Documents/.auto-tasks/start-unc-app.sh
systemctl --user status ivar-unc.service
# Dừng ứng dụng chạy nền:
systemctl --user stop ivar-unc.service
```

Các file cài đặt: `ivar-unc.service` (chép vào `~/.config/systemd/user/`) và
`ivar-unc.desktop` (chép vào `~/.local/share/applications/`). Sau khi cài:
`systemctl --user daemon-reload` và `systemctl --user enable --now ivar-unc.service`.
Giao diện chỉ truy cập trên máy này; dữ liệu hiển thị theo lần nạp database gần nhất.

---

## 1. Tổng quan & Đường dẫn hệ thống

- **Database SQLite**: `/home/beenorgone/Documents/.auto-tasks/ivar-documents-organizer/unc.db`
- **Thư mục nguồn PDF**: `/home/beenorgone/Documents/Drives/gDriveProjects/Working/Projects/IVAR/Accounting - Ke toan/Data - Du lieu/ngan hang/UNC/` *(Bảo toàn nguyên vẹn, chỉ đọc - không di chuyển, không đổi tên, không xóa)*
- **Công cụ CLI & Web Server**: `/home/beenorgone/Documents/.auto-tasks/unc_tool.py`
- **Module lõi & Parser**: `/home/beenorgone/Documents/.auto-tasks/unc_db.py`
- **Bộ kiểm thử tự động**: `/home/beenorgone/Documents/.auto-tasks/test_unc_database.py`

---

## 2. Kết quả nạp và kiểm chứng toàn diện (Verified 2026-11-01)

| Chỉ số | Số lượng | Ghi chú |
| :--- | :---: | :--- |
| **Tổng số file PDF vật lý** | **320** | Bao gồm 1 file mới đồng bộ trong tháng 11/2026 và 1 file có ký tự xuống dòng (`\n`) |
| **Số chứng từ nội dung duy nhất** | **316** | Khóa theo `content_sha256` |
| **Nhóm trùng lặp nội dung (Duplicates)** | **4 nhóm (8 files)** | Lưu đầy đủ tất cả đường dẫn vật lý mapping về chứng từ gốc |
| **Tổng số tiền giải ngân** | **4,470,429,294 VND** | 4.47 tỷ đồng |
| **Số đơn đặt hàng (PO) liên kết** | **68 PO riêng biệt** | 102 chứng từ có liên kết PO (hỗ trợ 1 UNC gắn nhiều PO) |
| **Thiếu số tham chiếu (Ref)** | **0** | 100% đầy đủ cho UNC ngân hàng |
| **Thiếu ngày giao dịch** | **0** | 100% đầy đủ |
| **Thiếu số tiền** | **0** | 100% đầy đủ |
| **Thiếu số tài khoản thụ hưởng** | **0** | 100% đầy đủ, bảo toàn số 0 ở đầu (`0031...`, `0071...`) |
| **Thiếu tên người thụ hưởng** | **0** | 100% đầy đủ |
| **Thiếu ngân hàng thụ hưởng** | **0** | 100% đầy đủ |

### Phân loại theo mục đích chi (Payment Categories):
1. `supplier_payment` (Thanh toán nhà cung cấp / Hàng hóa / Dịch vụ): **213** chứng từ (67.4%)
2. `advertising` (Chi phí quảng cáo Facebook / Google): **42** chứng từ (13.3%)
3. `payroll` (Chi lương, phụ cấp nhân sự): **23** chứng từ (7.3%)
4. `electricity` (Tiền điện EVN / Hợp tác xã): **18** chứng từ (5.7%)
5. `social_insurance` (Đóng BHXH / BHYT / BHTN): **9** chứng từ (2.8%)
6. `other` (Chi phí khác / Cần kế toán rà soát): **6** chứng từ (1.9%)
7. `internal_transfer` (Chuyển khoản nội bộ giữa các TK IVAR): **3** chứng từ (0.9%)
8. `refund` (Hoàn tiền giao dịch): **2** chứng từ (0.6%)

### Trạng thái rà soát (Review States):
- `VERIFIED` (Đã tự động xác minh đầy đủ): **307** chứng từ
- `NEEDS_REVIEW` (Cần kế toán rà soát chi tiết): **7** chứng từ
- `NON_UNC` (Chứng từ thông báo ngoài, không phải UNC ngân hàng): **2** chứng từ

---

## 3. Hướng dẫn lệnh tra cứu (CLI Commands & Examples)

### 3.1. Xem thống kê tổng quan
```bash
python3 unc_tool.py stats
```

### 3.2. Tra cứu theo phân loại chi phí (`--category` / `-c`)
```bash
# Xem các khoản chi lương
python3 unc_tool.py search -c payroll

# Xem các khoản tiền điện
python3 unc_tool.py search -c electricity

# Xem các khoản quảng cáo
python3 unc_tool.py search -c advertising

# Xem chuyển tiền nội bộ
python3 unc_tool.py search -c internal_transfer
```

### 3.3. Tra cứu theo số tài khoản thụ hưởng (`--account` / `-a`)
*Hệ thống bảo toàn nguyên vẹn số 0 ở đầu (text format).*
```bash
# Tìm số tài khoản Vietcombank bắt đầu bằng 0031...
python3 unc_tool.py search -a 0031000310916

# Tìm số tài khoản Vietcombank của Eurofins
python3 unc_tool.py search -a 0071000695552

# Tìm tài khoản ảo GHTKPAY (Alphanumeric)
python3 unc_tool.py search -a M369S000000036990
```

### 3.4. Tra cứu theo Mã đơn đặt hàng PO (`--po` / `-p`)
*Hỗ trợ nhập dạng `PO8275` hoặc chỉ nhập số `8275`.*
```bash
# Tìm theo PO8275
python3 unc_tool.py search -p PO8275

# Tìm theo số PO (chỉ nhập số)
python3 unc_tool.py search -p 8275

# Tìm UNC chứa nhiều PO (PO6940 và PO7795)
python3 unc_tool.py search -p 6940
```

### 3.5. Tra cứu kết hợp nhiều tiêu chí
```bash
# Tìm thanh toán nhà cung cấp trong năm 2026 có số tiền từ 20 triệu đến 50 triệu
python3 unc_tool.py search -c supplier_payment -y 2026 --amount-min 20000000 --amount-max 50000000

# Tìm chi phí quảng cáo thụ hưởng ZION qua ngân hàng VPBank
python3 unc_tool.py search -c advertising -n ZION -b VPBank
```

### 3.6. Tìm kiếm tự do toàn văn (Full-Text Search - FTS5)
```bash
# Tìm theo từ khóa trong nội dung chuyển tiền
python3 unc_tool.py search -q "tien thue van phong"
python3 unc_tool.py search -q "may in date"
```

### 3.7. Xem chi tiết và Mở file PDF gốc
```bash
# Xem chi tiết UNC theo Số tham chiếu (Ref No) hoặc ID
python3 unc_tool.py show 1031926F12827289
python3 unc_tool.py show 2504

# Mở trực tiếp file PDF gốc bằng trình xem tài liệu mặc định (xdg-open)
python3 unc_tool.py open 1031926F12827289
python3 unc_tool.py open 2504

# Tìm kiếm và tự động mở file đầu tiên tìm thấy
python3 unc_tool.py search -p 8275 --open
```

### 3.8. Xuất dữ liệu báo cáo (CSV / JSON)
```bash
# Xuất toàn bộ dữ liệu ra CSV
python3 unc_tool.py export -o bao_cao_unc.csv

# Xuất dạng JSON
python3 unc_tool.py export -o bao_cao_unc.json
```

---

## 4. Bảng điều khiển giao diện web Material (Local Control Panel)

Khởi chạy giao diện trực quan hỗ trợ cả dạng Bảng (Table) và Kanban (theo phân loại chi phí):

```bash
python3 unc_tool.py serve --port 8788 --open
```

- **URL truy cập**: `http://127.0.0.1:8788/`
- **Tính năng giao diện**:
  - **Bộ lọc động**: Tìm kiếm tức thì, lọc theo Chip (Nhà cung cấp, Chi lương, Quảng cáo, Tiền điện, BHXH, Chuyển nội bộ, Cần duyệt).
  - **Chế độ Kanban**: Phân nhóm trực quan theo 8 nhóm danh mục chi phí.
  - **Chi tiết chứng từ (Modal)**: Bấm vào dòng/card để xem đầy đủ người trả, người nhận, tài khoản, nội dung, bằng chứng OCR/bbox và danh sách các file nguồn.
  - **Mở PDF nguồn**: Nút bấm "Mở File PDF Gốc" gửi lệnh an toàn tới hệ thống để mở tài liệu tại chỗ mà không cần copy hay di chuyển file.

---

## 5. Danh sách các mục trong hàng đợi rà soát (Review Queue)

Hệ thống ghi nhận **7 chứng từ** ở trạng thái `NEEDS_REVIEW` để kế toán kiểm tra lại:

1. **ID 53 (Số Ref: `1030226E04472375`)**:
   - Thụ hưởng: `TRUNG TAM MA SO, MA VACH QUOC GIA` (Số tiền: 800,000 đ).
   - Nội dung: `893622667 DT2026`.
   - Lý do rà soát: Phí duy trì mã số mã vạch GS1 hàng năm (phân loại `other`, kế toán có thể xếp vào phí hành chính/nhà cung cấp).
2. **ID 171 (Số Ref: `1031926J05709320`)**:
   - Thụ hưởng: `CONG TY CO PHAN ZION-Facebook Ads` (Số tiền: 20,500,000 đ).
   - Lý do rà soát: Trong file PDF trường `Ngày Date: null`. Hệ thống đã tự động phục hồi ngày chính xác `2026-10-05` từ `Accounting Date` và ghi nhận trong audit evidence.
3. **ID 187 (Số Ref: `1031926J09979133`)**:
   - Thụ hưởng: `CONG TY CO PHAN SEPAY` (Số tiền: 28,000 đ).
   - Nội dung: `SEP00053590`.
   - Lý do rà soát: Phí dịch vụ cổng SePay (phân loại `other`, kế toán có thể duyệt thành phí dịch vụ phần mềm).
4. **ID 220 (Số Ref: `1030224H02777456`)**:
   - Thụ hưởng: `CT TNHH TM VA DV TONG HOP VAN PHUC` (Số tiền: 16,500,000 đ).
   - Nội dung: `Chi Ngoc 006`.
   - Lý do rà soát: Nội dung chuyển khoản ghi tên cá nhân kèm mã, cần đối chiếu hóa đơn đầu vào.
5. **ID 247 (Số Ref: `1031925D30538238`)**:
   - Thụ hưởng: `CT TNHH EUROFINS SAC KY HAI DANG` (Số tiền: 29,779,164 đ).
   - Nội dung: `IVAR EUVNHC-00297936`.
   - Lý do rà soát: Phí kiểm nghiệm mẫu Eurofins không ghi rõ số PO (phân loại `other`, kế toán có thể chuyển thành NCC).
6. **ID 259 & 271 (Số Ref: `1031925G22578767`, `1031925G19451529`)**:
   - Thụ hưởng: `CTCP IN VA TM DICH VU DONG LOI` (Số tiền: 603,180 đ và 771,606 đ).
   - Nội dung: Ghi lặp tên công ty IVAR trong remarks. Đơn vị thụ hưởng là NCC in ấn Đồng Lợi.
7. **2 chứng từ NON_UNC (`NON_UNC`)**:
   - `UNC TT Số hoá đơn Ngày Ghi chú.pdf`: Thông báo chi phí lô hàng nhập khẩu của Công ty ASC Trans.
   - `UNC STT BHYT BHTN CỘNG.pdf`: Mẫu thông báo C12-TS của BHXH Thành phố Hà Nội.

---

## 6. Hướng dẫn cập nhật và đồng bộ định kỳ (Incremental Sync)

Hệ thống được thiết kế **idempotent (bất biến)** và **incremental (tăng dần)**:
- Mỗi file được lưu trữ `file_size`, `file_mtime`, `file_sha256` và `content_sha256`.
- Quá trình quét lại chỉ mất **~0.02 giây** nếu không có file mới.
- Khi người dùng sao chép thêm file UNC PDF mới vào thư mục nguồn, chỉ cần chạy:

```bash
python3 unc_tool.py scan
```

Hệ thống sẽ tự động phát hiện các file mới, trích xuất dữ liệu, kiểm tra trùng lặp và cập nhật database ngay lập tức.
