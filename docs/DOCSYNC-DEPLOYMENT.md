> Từ 12/11/2026, WIP dùng 7 chủ đề công việc: Sản xuất, Mua hàng - dịch vụ,
> Giao vận, Kinh doanh, QC, Kế toán - tài chính, Pháp Lý. Cấu hình đường dẫn tập
> trung tại `docsync_layout.py`; nhật ký nằm trong `.docsync/Nhật ký`.
> `docsync_topics.py` xếp mục rõ nghĩa; mục chưa rõ được gửi Telegram theo lô tối đa
> 5 mục/tin, mỗi mục có quyết định riêng. `topic-telegram-state.json` không commit.
> Bridge vẫn giữ luồng phê duyệt ZIP PO riêng và không thay đổi kho kế toán ngoài WIP.

# DocSync: triển khai, cấu hình và logic xử lý

Tài liệu này dành cho người triển khai bản DocSync có phân loại ZIP hồ sơ mua vào theo lịch sử NCC, cầu nối Telegram và đánh dấu hóa đơn trùng. Hướng dẫn thao tác hằng ngày nằm trong [GUIDE-DOCSYNC.md](../GUIDE-DOCSYNC.md).

## 1. Thành phần và luồng dữ liệu

| Thành phần | Trách nhiệm |
| --- | --- |
| `ivar-documents-organizer.sh` | Khóa chung; xử lý tài liệu kế toán; bỏ qua ZIP hồ sơ ở bộ xử lý PO/trùng chung; gọi rule hồ sơ mua vào cuối lượt |
| `docsync_purchase.py` | Đọc lịch sử NCC, đề xuất nhóm/đích, kiểm tra file và chuyển nguyên bộ, ghi audit |
| `docsync_core.py` | Hiển thị thẻ hồ sơ và hành động tiếp theo trên Kanban |
| `docsync_telegram.py` | Giữ khóa chung; đọc quyết định từ ERP qua SSH; gửi đề xuất và thực hiện quyết định đúng hash |
| `telegram/docsync-telegram.php` | MU plugin trên ERP: lưu đề xuất, gửi qua bot đang có, nhận `/ds` (alias `/docsync`), kiểm tra chủ sở hữu/hạn dùng |
| `systemd/docsync-telegram.{service,timer}` | Chạy bridge local sau khởi động 45 giây, sau mỗi lượt kết thúc 60 giây |

Luồng chính: file đầu vào → so lịch sử NCC → `ready` nếu nhóm nhất quán → chuyển và ghi audit. Nếu thiếu/xung đột lịch sử: `needs_review` → gửi Telegram → `waiting_external` → người sở hữu chọn nhóm hoặc giữ lại → bridge kiểm tra lại file → chuyển → báo hoàn tất. Thông báo **đã ghi nhận** chỉ xác nhận quyết định; **đã chuyển** được gửi sau khi kiểm tra file đích.

### Quy tắc ZIP và lịch sử

- ZIP phải khớp `ho so PO…zip`; tên chuẩn: `ho so PO123 - 2026-11-01 - NCC Supplier.zip`.
- Quét file trực tiếp tại WIP, Documents, Downloads; riêng `00_INBOX` quét đệ quy, bỏ thư mục ẩn và symlink.
- Tên NCC lấy từ phần ` - NCC …` trước `.zip`, chuẩn hóa Unicode NFC, chữ hoa/thường và khoảng trắng hai đầu. Không ghép gần đúng hoặc một phần tên.
- Chỉ hồ sơ đã nằm đúng tháng, dưới `<năm>/nhap khau/<MM>` hoặc `<năm>/trong nuoc/<MM>`, mới là căn cứ nhóm. Lịch sử không có nhóm không được dùng suy luận.
- Mọi lịch sử đủ điều kiện của NCC cùng một nhóm thì tự phân loại. Không có lịch sử hoặc có cả hai nhóm thì cần quyết định.
- Đích: `$IVAR_DATA/ho so mua vao/<YYYY>/<nhóm>/<MM>/<tên ZIP>`, lấy năm/tháng từ đúng một ngày hợp lệ trên tên, giữ nguyên ZIP.
- Hồ sơ đã ở kho được sắp xếp theo tháng trên tên trong phạm vi năm/nhóm hiện có. Năm tên khác năm thư mục phải kiểm tra thủ công.
- Đích trùng, symlink, ngày sai hoặc nhiều ngày đều giữ để kiểm tra. Chọn nhóm qua Telegram không vượt được các điều kiện này.

### Quyết định và tính toàn vẹn

Bridge lưu SHA-256 local cùng mã đề xuất. Quyết định chỉ dùng cho file còn đúng hash; file thay đổi cần yêu cầu mới. `giu_lai` tiếp tục giữ file dù sau đó có lịch sử NCC nhất quán. ERP chỉ nhận tên file, ngày, mã và tài khoản/bot đích; không nhận nội dung ZIP hoặc bot token từ local.

Lệnh `/ds MÃ tn`, `/ds MÃ nk`, `/ds MÃ g` chỉ chấp nhận tài khoản Telegram liên kết sở hữu đề xuất, một lần trong 7 ngày. ERP dọn đề xuất sau 30 ngày. Local giữ trạng thái hết hạn và không tự gửi lại cùng file; muốn gửi lại cần dừng timer, sao lưu state, kiểm tra file rồi xóa riêng bản ghi hết hạn tương ứng trong JSON.

Các lượt shell và bridge dùng cùng `organizer.lock`; bridge bỏ qua lượt nếu đang bận. Bridge chờ mọi ứng viên trong lượt yên ít nhất 20 giây theo mtime/ctime. Chuyển dùng rename không ghi đè, kiểm tra metadata trước/sau; ZIP intake được kiểm tra SHA-256 sau chuyển. Rename yêu cầu nguồn và đích cùng filesystem; lỗi cross-device cần người vận hành xử lý, không có fallback copy/delete.

### Hóa đơn trùng và UNC

Chỉ bản lưu còn tồn tại, khớp dung lượng và SHA-256 mới chứng minh trùng. Bản ghi database cũ không đủ. Hóa đơn VAT PDF/XML trùng được đổi tên ngay cạnh nguồn thành `_duplicate`, thêm `_1`, `_2` nếu cần; sidecar XML chỉ đổi khi cũng được chứng minh trùng. UNC trùng được chuyển vào `duplicates` của WIP. Không ghi đè bản đã lưu; không tự xóa bản trùng. Database ghi cả đường dẫn mới để tránh đổi tên lại ở lượt sau.

## 2. Điều kiện triển khai

Máy Ubuntu cài mới có thể cài app và watcher từ [bộ chạy Ubuntu](../packaging/docsync-ubuntu/README.md).
Các bước kết nối Telegram và ERP dưới đây áp dụng cho các máy bật tích hợp đó.

- Máy Linux có Python 3, `sh`, `flock`, OpenSSH và công cụ phụ thuộc của organizer hiện có. Rule rename dùng Linux/glibc `renameat2`.
- Checkout local ở `~/Documents/.auto-tasks`; nếu dùng chỗ khác phải chỉnh `ExecStart` của service.
- ERP WordPress đã có tích hợp Telegram AI của IVAR: cấu hình bật, bot hoạt động, tài khoản ERP được phép sử dụng và Telegram đã liên kết/duyệt.
- SSH bằng khóa từ máy local tới tài khoản triển khai ERP; máy ERP có PHP và WP-CLI. Chạy WP-CLI dưới tài khoản có quyền đọc WordPress và ghi options.
- Đường dẫn tích hợp theme phải tồn tại: `custom-functions/erp/integrations/telegram-ai/telegram-ai.php`. Bridge gọi `--skip-plugins --skip-themes` rồi nạp tích hợp này; MU plugins vẫn được WordPress nạp.

Không cần cài thư viện Python riêng cho hai module purchase/Telegram. Bot token tiếp tục nằm trên ERP.

## 3. Cấu hình local

Các mặc định:

| Biến | Mặc định | Ý nghĩa |
| --- | --- | --- |
| `DOCSYNC_ROOT` | `~/Documents/WIP & Reference` | Gốc WIP/intake và Kanban |
| `IVAR_DATA` | `~/Documents/Drives/gDriveProjects/Working/Projects/IVAR/Accounting - Ke toan/Data - Du lieu` | Gốc dữ liệu kế toán, chứa `ho so mua vao` |
| `STATE_DIR` | `~/Documents/.auto-tasks/ivar-documents-organizer` | State, database, audit và khóa chung |
| `DOCSYNC_DOCUMENTS_SCAN` | `~/Documents` | Thư mục quét bổ sung 1, giá trị rỗng để tắt |
| `DOCSYNC_DOWNLOADS_SCAN` | `~/Downloads` | Thư mục quét bổ sung 2, giá trị rỗng để tắt |

Tab **Cài đặt** lưu các thư mục làm việc trong `${XDG_CONFIG_HOME:-~/.config}/docsync/folders.json` cho cùng người dùng.
Các biến môi trường tường minh được ưu tiên. Gói Ubuntu lưu state tại `~/.local/share/docsync/state`.

Python bridge mặc định tìm state cạnh mã nguồn; shell mặc định theo Documents như bảng. Khi đổi checkout/state, đặt `STATE_DIR` rõ ràng cho mọi entrypoint, bao gồm app, watcher, organizer và bridge. Biến export ở terminal không tự truyền vào systemd.

Tạo cấu hình local, thay các giá trị mẫu bằng cấu hình thực:

```bash
cd ~/Documents/.auto-tasks
mkdir -p ivar-documents-organizer
umask 077
cat > ivar-documents-organizer/purchase-telegram.json <<'JSON'
{
  "host": "erp-docsync",
  "wp_path": "/absolute/path/to/wordpress",
  "login": "ERP_OWNER_LOGIN",
  "bot": "ACTUAL_BOT_USERNAME"
}
JSON
chmod 600 ivar-documents-organizer/purchase-telegram.json
```

`host` là SSH host alias hoặc `user@host`, không phải URL. `bot` không chứa `@`; bridge đối chiếu username thực qua Telegram `getMe`. Thiết lập SSH alias trong `~/.ssh/config` nếu cần; kiểm tra host key qua SSH tương tác trước khi dùng `BatchMode`.

Không commit `purchase-telegram.json`, `purchase-telegram-state.json`, `purchase-months.jsonl` hoặc database vận hành. Ba file purchase đã có rule ignore; database đang được Git theo dõi từ trước nên phải chủ động loại khỏi staging.

## 4. Cài MU plugin trên ERP

Sao lưu bản plugin đang có nếu đã triển khai. Ví dụ dưới đây giả định SSH alias, WP path và quyền ghi đã được cấu hình:

```bash
ssh erp-docsync 'mkdir -p /absolute/path/to/wordpress/wp-content/mu-plugins'
scp telegram/docsync-telegram.php erp-docsync:/absolute/path/to/wordpress/wp-content/mu-plugins/ivar-docsync-telegram.php
ssh erp-docsync 'php -l /absolute/path/to/wordpress/wp-content/mu-plugins/ivar-docsync-telegram.php'
ssh erp-docsync 'wp --path=/absolute/path/to/wordpress --skip-plugins --skip-themes eval '\''echo function_exists("ivar_docsync_bridge") ? "bridge loaded\n" : "bridge missing\n";'\'''
```

MU plugin được nạp tự động, không chạy `wp plugin activate`. Kiểm tra đường dẫn theo máy thực trước khi sao chép. Những lệnh này không phải bằng chứng bot đã gửi/nhận quyết định.

## 5. Kiểm tra và bật dịch vụ

Chạy kiểm tra local, không gọi Telegram thật:

```bash
python3 -m unittest test_docsync_topics test_accounting_duplicates test_docsync_purchase test_docsync_telegram test_docsync test_docsync_layout test_doc_sync_app test_wip_organizer test_docsync_relocate
sh -n ivar-documents-organizer.sh
php -l telegram/docsync-telegram.php
php telegram/test-docsync-telegram.php
git diff --check
```

Xem đề xuất trước khi chuyển:

```bash
./docsync preview
./docsync board
```

Preview không chuyển tài liệu; organizer có thể ghi log/state vận hành. Chỉ khi đã xem và đồng ý các đích đề xuất mới chạy `./docsync apply`. Lượt apply xử lý cả các rule organizer đang bật, không chỉ ZIP.

Cài timer sau khi cấu hình và plugin đã sẵn sàng:

```bash
mkdir -p ~/.config/systemd/user
install -m 644 systemd/docsync-telegram.service systemd/docsync-telegram.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now docsync-telegram.timer
systemctl --user list-timers docsync-telegram.timer
journalctl --user -u docsync-telegram.service -n 50 --no-pager
```

Timer có quyền tự chuyển ZIP đủ điều kiện lịch sử và ZIP đã duyệt. Chạy `python3 docsync_telegram.py` cũng là một lượt thực thi, không phải dry-run; không dùng lệnh này chỉ để kiểm tra kết nối. Thiếu file cấu hình thì bridge kết thúc im lặng.

Nếu dùng đường dẫn khác, chạy `systemctl --user edit docsync-telegram.service` và khai báo override, ví dụ:

```ini
[Service]
Environment="STATE_DIR=/absolute/path/to/state"
Environment="DOCSYNC_ROOT=/absolute/path/to/WIP"
Environment="IVAR_DATA=/absolute/path/to/accounting-data"
ExecStart=
ExecStart=/usr/bin/python3 /absolute/path/to/checkout/docsync_telegram.py
```

Sau sửa override, chạy `daemon-reload`. User timer cần user manager hoạt động; nếu cần chạy sau logout, người quản trị có thể bật `loginctl enable-linger USERNAME` theo chính sách máy. Máy tắt hoặc mất mạng thì quyết định chờ lượt chạy tiếp theo.

## 6. Nghiệm thu thực tế

Dùng một ZIP thử nghiệm riêng và thư mục đích kiểm soát được; không dùng hồ sơ đang xử lý để thử tùy ý.

1. Preview: NCC nhất quán đề xuất đúng nhóm và tháng; NCC chưa có lịch sử giữ `needs_review`.
2. Bật timer: trường hợp chưa rõ nhận đúng bot/tài khoản, Kanban hiện `waiting_external`; không gửi lặp qua nhiều lượt.
3. Chọn nhóm: bot báo đã ghi nhận; lượt sau file được chuyển đúng đích, hash giữ nguyên, audit có `planned` rồi `completed`, sau đó bot báo đã chuyển.
4. Chọn `giu_lai` ở hồ sơ khác: file tiếp tục ở nguồn, kể cả khi xuất hiện lịch sử NCC.
5. Kiểm tra lệnh bot hiện có như `/tasks` vẫn hoạt động. Không coi test mô phỏng là bằng chứng dispatch production.

## 7. Theo dõi và xử lý lỗi

| Hiện tượng | Kiểm tra/hành động |
| --- | --- |
| Không gửi gì | File cấu hình có đúng `STATE_DIR` không; tên ZIP/ngày; có đang ở thời gian chờ 20 giây; đã có pending/held/expired cùng hash chưa |
| `BUSY` hoặc lượt bridge không làm gì | Organizer/watcher đang giữ khóa; chờ lượt sau, không xóa file lock |
| SSH/WP lỗi | Kiểm tra SSH BatchMode, host key, WP path, quyền và MU plugin; stdout remote không được bridge đưa thẳng vào thông báo |
| `wrong_bot`, `recipient_not_linked` | Kiểm tra username bot thực, login ERP, eligibility và liên kết Telegram đã duyệt |
| File vẫn giữ sau chọn nhóm | Kiểm tra hash nguồn, ngày, symlink, đích trùng và trạng thái hết hạn |
| `Invalid cross-device link` | Nguồn/đích khác filesystem; giữ file, chuẩn bị quy trình copy kiểm tra hash riêng trước khi xử lý thủ công |
| Chuyển xong chưa thấy thông báo | Xem state `completed`/`notified`; bridge thử thông báo lại ở lượt sau. Kiểm tra audit và file thực tế trước khi kết luận |
| Service timeout | Unit giới hạn 120 giây, mỗi SSH call tối đa 30 giây; nhiều yêu cầu có thể cần nhiều lượt. Kiểm tra journal và state trước khi tăng timeout bằng override |

Audit: `$STATE_DIR/purchase-months.jsonl` lưu nguồn, đích, thời gian, lý do, căn cứ lịch sử và SHA-256 của ZIP intake. `purchase-telegram-state.json` lưu mã, hash, thời điểm, trạng thái pending/queued/held/stale/expired/completed và cờ thông báo. Sao lưu hai file cùng database trước khi sửa trạng thái thủ công.

## 8. Dừng và rollback

```bash
systemctl --user disable --now docsync-telegram.timer
systemctl --user stop docsync-telegram.service
```

Dừng timer chỉ dừng cầu nối; organizer/app/watcher vẫn có thể tự phân loại theo lịch sử. Nếu cần dừng toàn bộ di chuyển, dừng cả entrypoint đó trước khi khôi phục mã/state.

Khôi phục phiên bản MU plugin và mã local đã sao lưu, giữ lại config/state/audit để đối chiếu; không reset toàn bộ checkout có thay đổi khác. Quyết định ERP tồn tại tới lịch dọn 30 ngày. Rollback mã không tự chuyển hồ sơ về nguồn. Muốn hoàn tác file: đối chiếu bản ghi audit `completed`, kiểm tra hash file đích và bảo đảm nguồn chưa tồn tại, rồi chuẩn bị kế hoạch chuyển ngược riêng dưới khóa chung. Không khôi phục database cũ mà bỏ qua các lượt xử lý mới.

## Chuyển cấu trúc WIP sang chủ đề công việc

Xem kế hoạch (chỉ đọc):

```bash
python3 docsync_migrate_layout.py --year-root "$HOME/Documents/WIP & Reference"
```

Sau khi đã xem các đường dẫn nguồn/đích và có quyền chuyển, dừng watcher, timer,
app đang rảnh, rồi chạy cùng lệnh với `--apply`. Engine giữ khóa chung, sao lưu
SQLite/rules, kiểm tra SHA-256/inode/mtime, cập nhật đường dẫn chỉ mục và
`processed.db`, kiểm tra file ngoài phạm vi, rồi ghi manifest. Nếu lỗi trước
khi hoàn tất, engine hoàn tác các mục của lượt chạy đó. Không dùng symlink để
che cấu trúc cũ. Khởi động lại watcher/app/timer sau khi kiểm chứng và quét lại
Kanban. Chạy bridge sẽ vừa tự xếp mục rõ nghĩa vừa gửi các đề xuất chưa rõ;
đây là hành động thực thi, không phải Preview.

Lựa chọn Telegram cho chủ đề là `san_xuat`, `mua_hang`, `giao_van`, `kinh_doanh`,
`qc`, `ke_toan`, `phap_ly`, `giu_lai`. Lựa chọn nhóm ZIP PO (`trong_nuoc`,
`nhap_khau`) chỉ dùng cho đúng loại yêu cầu; không thể dùng để duyệt yêu cầu chủ đề.

## Lệnh Telegram rút gọn từ 14/11/2026

- `/ds MÃ NHÓM`: mã đề xuất 6 ký tự, được ánh xạ duy nhất về ID 32 ký tự.
- Nhóm công việc: `sx`, `mh`, `gv`, `kd`, `qc`, `kt`, `pl`; `g` giữ lại.
- ZIP PO: `tn`, `nk`, `g`. Không dùng mã nhóm PO để duyệt chủ đề công việc.
- Gộp tối đa 10 cặp trong một tin: `/ds a1b2c3 sx d4e5f6 kt`.
- `/ds` xem hướng dẫn; hỗ trợ @bot, chữ hoa/thường và phân cách bằng xuống dòng/phẩy/chấm phẩy.
- `/docsync`, ID 32 ký tự và tên nhóm cũ vẫn được chấp nhận cho tin đã gửi.
- Mã ngắn không phải tiền tố ID: cấp bằng option duy nhất, xử lý va chạm và dọn cùng đề xuất sau 30 ngày.
- Một lần tra tài khoản liên kết cho cả lô; khóa quyết định bằng insert option duy nhất để không chốt đè.
- Không dùng chặn im lặng 2 giây cho các lần trả lời nhanh; giới hạn 30 tin/phút, tối đa 10 mục/tin.
- Thông báo chỉ ghi “chờ chuyển” khi đã lưu quyết định; “đã chuyển” vẫn chỉ gửi sau khi máy xác minh file.
- State định dạng thông báo nằm riêng trong `ivar_ds_notice_*`, tránh ghi đè một quyết định đến đồng thời.
- Có thể gửi lại đề xuất đang chờ ở định dạng mới một lần bằng `submit_topics` với `refresh_format=true`; mã và quyết định cũ vẫn hợp lệ.


## Yêu cầu xóa file qua Telegram

- `/rm a1b2c3` hoặc `/ds a1b2c3 rm`: yêu cầu xóa file của đề xuất đang chờ quyết định.
- `/rm a1b2c3 d4e5f6`: tối đa 10 mã/tin; không nhận đường dẫn, wildcard hay tùy chọn shell.
- Chỉ chủ tài khoản Telegram đang liên kết và đủ quyền được chốt; giữ hạn 7 ngày và mỗi đề xuất chỉ chốt một lần.
- Bot báo đã nhận yêu cầu trước. Bridge giữ `organizer.lock`, kiểm tra SHA-256 rồi chuyển file vào `$STATE_DIR/telegram-trash/<id>/<tên file>`; không xóa vĩnh viễn, không xóa thư mục, không ghi đè hoặc đi qua symlink.
- Chỉ báo hoàn tất sau khi kiểm tra file trong thùng rác. File đã đổi hoặc bị chặn được giữ để kiểm tra; lỗi I/O được timer thử lại.
- Chỉ mục Telegram lưu đường dẫn nguồn, hash và `trash` để khôi phục trên máy. Muốn khôi phục, tạm dừng timer/watcher, đối chiếu hash và chuyển về đường dẫn nguồn nếu nguồn đang trống; không ghi đè file mới.
- Thùng rác là dữ liệu vận hành, không đưa vào Git; cần triển khai MU plugin mới và bridge local cùng nhau.

## Ảnh từ mới → WordQuest

- Chọn `/ds MÃ wq` cho ảnh JPG/PNG/WebP đang chờ. Đây là yêu cầu đọc ảnh bằng AI, không phải một nhóm thư mục công việc.
- Máy kiểm tra ảnh tối đa 6 MB, hash đúng bản đã duyệt; không tự lấy ảnh khác. Mỗi lượt xử lý một ảnh, ngoài khóa organizer để không chặn phân loại tài liệu.
- `telegram/docsync-wordquest.php` phải nằm cạnh MU plugin `ivar-docsync-telegram.php`. Helper gửi bytes ảnh qua gateway AI ERP hiện có, có budget guard/run log; file tạm riêng tư được xóa sau xử lý. Không gửi ảnh vào Search và không đặt ảnh trong uploads công khai.
- Bot gửi link WordQuest riêng tư chứa danh sách từ trong URL fragment; app chỉ lưu sau khi phụ huynh kiểm tra và xác nhận. Không có khóa API trong link. Ảnh gốc được giữ nguyên, kết quả local tại `.docsync/WordQuest/`.
- Nội dung/ảnh không rõ hoặc ảnh đổi sẽ báo chưa tạo được bài, không tự đoán và không chuyển/xóa nguồn.
- Tăng TimeoutStartSec của timer bridge lên 240 giây; Vision có timeout riêng và xử lý tối đa một ảnh mỗi lượt.
