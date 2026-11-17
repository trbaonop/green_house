# TH08 — Phân quyền và bảo vệ kết nối

## 1. Thành phần
- `api/app.py`: Flask API, SQLite, đăng nhập token, role `operator`/`admin`, MQTT TLS.
- `dashboard/index.html`: dashboard, điều khiển bơm, giao diện quản trị.
- `api/simulate_node.py`: giả lập ESP32 qua MQTT TLS.
- `mosquitto/mosquitto.conf`, `mosquitto/acl.conf`: cấu hình broker và ACL mẫu.
- `.env.example`: tên biến môi trường; không chứa mật khẩu thật.

## 2. Ma trận quyền
| Chức năng | Vận hành (`operator`) | Quản trị (`admin`) |
|---|---|---|
| Xem telemetry | Có | Có |
| Xem lịch sử lệnh | Có | Có |
| Bật/tắt bơm | Có | Có |
| Xem/thay đổi cấu hình | Không | Có |
| Tạo/xóa tài khoản | Không | Có |

API kiểm tra quyền ở phía server; ẩn nút trên giao diện không được xem là biện pháp bảo vệ.

## 3. Cài đặt API
Python 3.10+ khuyến nghị. Từ thư mục dự án:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r api/requirements.txt
```
Đặt biến môi trường theo `.env.example` (không commit `.env`). Ví dụ trong shell:
```bash
export ADMIN_USERNAME=admin
export ADMIN_PASSWORD='REPLACE_WITH_A_LONG_RANDOM_PASSWORD'
export OPERATOR_USERNAME=operator
export OPERATOR_PASSWORD='REPLACE_WITH_ANOTHER_LONG_RANDOM_PASSWORD'
export MQTT_HOST='broker.example.local'
export MQTT_PORT=8883
export MQTT_USER=greenhouse_api
export MQTT_PASS='REPLACE_WITH_MQTT_PASSWORD'
export MQTT_CA_CERT='/etc/greenhouse/certs/ca.crt'
export NODE_ID=node01
python api/app.py
```
Tên `MQTT_HOST` phải khớp DNS SAN trong broker certificate. Không dùng `tls_insecure_set(True)` và không tắt kiểm tra hostname.

## 4. Mosquitto và ACL
- Tạo broker certificate có SAN DNS/IP đúng với địa chỉ client sử dụng.
- Đặt CA public cert, broker cert và private key ở đường dẫn bảo vệ theo `mosquitto/mosquitto.conf`.
- Tạo password file bằng `mosquitto_passwd`; tạo riêng user `esp32` và `greenhouse_api`.
- Chỉnh `acl.conf` theo `NODE_ID` thực tế.
- Broker dùng TLS listener 8883. Không mở anonymous access.
- ESP32/simulator chỉ được publish telemetry/ACK và subscribe command đúng node.
- API chỉ được subscribe telemetry/ACK và publish command đúng node.
- Không đưa `broker.key`, password file, mật khẩu hoặc `.env` vào Git.

## 5. Chạy node mô phỏng
```bash
python api/simulate_node.py --host broker.example.local --port 8883 \
  --user esp32 --password 'ESP32_MQTT_PASSWORD' --node node01 \
  --ca /path/to/ca.crt
```
Các lệnh nhập trong terminal: `ok`, `mute`, `late`, `error`, `silent`, `talk`, `quit`.

## 6. Kiểm tra TH08
1. Đăng nhập operator: xem dữ liệu, bật/tắt bơm thành công.
2. Gọi `GET /api/admin/config` bằng operator: phải trả 403 và có dòng trong `api/log_denied.txt`.
3. Đăng nhập admin: `GET/POST /api/admin/config`, `GET/POST/DELETE /api/admin/users` được phép.
4. Thử tài khoản MQTT `esp32` publish `nhom01/node01/cmd`: broker phải từ chối theo ACL.
5. Thử `esp32` publish `nhom01/node01/telemetry` và subscribe `nhom01/node01/cmd`: được phép.
6. Kết nối bằng CA sai hoặc hostname không khớp SAN: TLS handshake phải thất bại. Lưu output kiểm thử.
7. Kiểm tra `api/log_allowed.txt`, `api/log_denied.txt`, `api/cmd_ack_log.txt`, `api/log_timeout.txt`.

Ví dụ test API sau khi đăng nhập và lấy token:
```bash
curl -i -H "Authorization: Bearer OPERATOR_TOKEN" https://YOUR_HOST/api/admin/config
```
Kỳ vọng operator nhận `403 Forbidden`. Không chia sẻ token thật trong báo cáo.

## 7. Lưu ý trước khi triển khai
- `ADMIN_PASSWORD`, `OPERATOR_PASSWORD`, `MQTT_PASS` phải được đặt qua environment/secret store.
- Đổi `broker.example.local`, đường dẫn chứng chỉ và `NODE_ID` theo môi trường thật.
- Cấu hình Mosquitto là mẫu, phải kiểm tra đường dẫn/chủ sở hữu/quyền đọc file trên máy lab.
- `log_allowed.txt` và `log_denied.txt` được tạo khi phát sinh hoạt động; không tự tạo log giả.
- SQLite `dashboard.db` và các file log không commit nếu có dữ liệu thật.
