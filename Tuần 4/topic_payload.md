# Quy Chuẩn Topic và Payload MQTT (Bài Thực Hành 04)

Tài liệu này mô tả chi tiết quy chuẩn kiến trúc topic, định dạng payload dữ liệu cảm biến (telemetry), quy tắc xử lý lỗi (phân biệt giữa `null` và `0`), và mô hình triển khai hệ thống cho Bài thực hành 04 / 10 môn **CSE3069 - Phát triển ứng dụng IoT**.

---

## 1. Nơi triển khai hệ thống (Deployment Architecture)

| Thành phần | Nơi thực thi / Thiết bị | Địa chỉ mạng / Giao thức | Vai trò |
| :--- | :--- | :--- | :--- |
| **Edge Node (ESP32)** | ESP32-WROOM trên mô hình nhà kính | Wi-Fi Station (192.168.x.x) | Chạy 2 task FreeRTOS song song: đọc cảm biến/FSM an toàn (Core 1) và đẩy tin MQTT qua Queue (Core 0). |
| **MQTT Broker** | Docker Compose (Mosquitto 2.0) trên PC/VPS | Port 1883 (TCP) | Tiếp nhận bản tin, xác thực tài khoản (`iot_user`), chuyển tiếp tin tới subscriber. |
| **Monitor / Subscriber** | PC / Laptop quan sát (cùng mạng LAN) | `mosquitto_sub` hoặc MQTT Explorer | Lắng nghe topic telemetry, ghi log liên tục đối chiếu với chu kỳ gửi từ ESP32. |

---

## 2. Quy chuẩn Topic (Topic Specification)

* **Quy tắc đặt tên:** Phân cấp theo định danh nhóm và mã trạm đo cụ thể, không sử dụng tên chung chung (`test`, `data`, `esp32`).
* **Cấu trúc topic:**
  ```text
  <nhóm>/<mã_node>/telemetry
  ```
* **Topic chuẩn sử dụng trong bài:**
  ```text
  nhom01/node01/telemetry
  ```
  *(Các node khác mở rộng: `nhom01/node02/telemetry`, `nhom02/node01/telemetry`,...)*

---

## 3. Quy chuẩn Payload (JSON Schema & Format)

### 3.1 Cấu trúc các trường (Fields Specification)

| Trường | Kiểu dữ liệu | Ý nghĩa | Ví dụ | Ghi chú |
| :--- | :--- | :--- | :--- | :--- |
| `device_id` | String | Mã định danh duy nhất của thiết bị | `"node01"` | Bắt buộc |
| `ts` | Integer / Long | Mốc thời gian (Unix epoch tính bằng giây hoặc uptime ms) | `1730621450` | Định vị thứ tự gói |
| `seq` | Integer | Số thứ tự bản tin (Sequence number) | `42` | Dùng để kiểm đếm rớt gói |
| `temp` | Float / Null | Nhiệt độ môi trường (°C) | `28.4` | Null nếu cảm biến lỗi |
| `hum` | Float / Null | Độ ẩm môi trường (%) | `68.5` | Null nếu cảm biến lỗi |
| `rain` | Integer / Null | Trạng thái cảm biến mưa: `0` (khô), `1` (có mưa) | `0` | Null nếu cảm biến lỗi |
| `unit` | Object / String | Đơn vị đo lường tương ứng | `{"temp":"C","hum":"%","rain":"binary"}` | Đảm bảo ngữ cảnh rõ ràng |
| `valid` | Boolean | Cờ chất lượng dữ liệu (Quality Flag) | `true` | `false` khi có bất kỳ cảm biến nào lỗi |
| `fsm_state` | String | Trạng thái vòng an toàn của máy bơm (FSM Bài 3) | `"RUNNING"` / `"STOPPED"` / `"ERROR"` | Minh chứng task an toàn chạy |
| `pump` | Integer | Công suất bơm hiện tại (%) | `0` đến `100` | Theo chu kỳ FSM |

---

### 3.2 Quy tắc quan trọng: Phân biệt giữa `null` và `0`

> **Nguyên tắc kỹ thuật IoT:** 
> - Giá trị `0` là một giá trị đo vật lý hoàn toàn hợp lệ (ví dụ: nhiệt độ `0.0°C`, hoặc cảm biến mưa `0` tức là trời khô ráo, không có giọt nước).
> - Giá trị `null` biểu thị trạng thái **mất dữ liệu, mất kết nối phần cứng (I2C NACK / DHT timeout) hoặc giá trị ngoài ngưỡng an toàn (out of range)**.
> - Tuyệt đối **không** dùng `0` để thay thế cho giá trị lỗi!

#### A. Ví dụ Payload trạng thái hoạt động bình thường (Data Valid):
```json
{
  "device_id": "node01",
  "ts": 1730621450,
  "seq": 105,
  "temp": 27.6,
  "hum": 65.2,
  "rain": 0,
  "unit": {
    "temp": "C",
    "hum": "%",
    "rain": "binary"
  },
  "valid": true,
  "fsm_state": "RUNNING",
  "pump": 100
}
```
*Giải thích:*
- Nhiệt độ đo được: 27.6 °C
- Độ ẩm đo được: 65.2 %
- Cảm biến mưa: `0` (khô, không có mưa)
- `valid: true`: Số đo nằm trong dải hợp lệ (0–50°C, 20–90%RH), cảm biến hoạt động tốt.
- `fsm_state: "RUNNING"`, `pump: 100`: Bơm đang chạy với công suất 100%.

#### B. Ví dụ Payload khi cảm biến mưa phát hiện mưa (Rain = 1):
```json
{
  "device_id": "node01",
  "ts": 1730621455,
  "seq": 106,
  "temp": 26.8,
  "hum": 75.0,
  "rain": 1,
  "unit": {
    "temp": "C",
    "hum": "%",
    "rain": "binary"
  },
  "valid": true,
  "fsm_state": "RUNNING",
  "pump": 100
}
```

#### C. Ví dụ Payload khi mất tín hiệu cảm biến hoặc số đo lỗi (Data Invalid - `null`):
```json
{
  "device_id": "node01",
  "ts": 1730621460,
  "seq": 107,
  "temp": null,
  "hum": null,
  "rain": 0,
  "unit": {
    "temp": "C",
    "hum": "%",
    "rain": "binary"
  },
  "valid": false,
  "fsm_state": "ERROR",
  "pump": 0
}
```
*Giải thích:*
- Cảm biến nhiệt - ẩm bị mất kết nối (NaN) -> `temp: null`, `hum: null`.
- `valid: false` phản ánh tức thì lỗi dữ liệu.
- Vòng điều khiển an toàn (Safety FSM) trên Core 1 phát hiện lỗi cảm biến lập tức chuyển sang trạng thái `ST_ERROR`, tự động ngắt tải bơm về `pump: 0` để bảo vệ hệ thống.
