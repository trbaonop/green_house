# Bài 4 — Gửi dữ liệu qua MQTT

* **Họ tên:** Đoàn Trung Bảo
* **MSSV:** 23110153

## Thành phần chính

* `docker-compose.yml`, `mosquitto/config/`: cấu hình Broker và xác thực.
* `firmware/`: firmware ESP32 dùng FreeRTOS, Wi-Fi và MQTT.
* `topic_payload.md`: quy ước topic và payload JSON.
* `task_queue.png`: sơ đồ task và queue.
* `log_10min.txt`, `log_disconnect.txt`: kết quả thử nghiệm truyền tin và mất kết nối.
* `scripts/`: công cụ hỗ trợ cấu hình và tạo sơ đồ.

## Payload

ESP32 publish dữ liệu lên topic `nhom01/node01/telemetry`:

```json
{
  "device_id": "node01",
  "ts": 1730621450,
  "seq": 45,
  "temp": 28.2,
  "hum": 66.5,
  "rain": 0,
  "unit": {"temp": "C", "hum": "%", "rain": "binary"},
  "valid": true,
  "fsm_state": "RUNNING",
  "pump": 100
}
```

`seq` dùng để theo dõi thứ tự bản tin; `valid` cho biết số đo có hợp lệ hay không. Giá trị `0` là số đo hợp lệ, còn `null` biểu thị dữ liệu cảm biến không hợp lệ hoặc không đọc được. Khi lỗi, firmware đặt `valid` thành `false` và đưa bơm về trạng thái an toàn.

## Firmware và độ tin cậy

Firmware tách thành hai task trên ESP32:

* **`vTaskSafetyFSM` (Core 1):** đọc cảm biến, xử lý nút nhấn, giám sát thời gian chạy tối đa của bơm và cập nhật PWM.
* **`vTaskMQTT` (Core 0):** duy trì kết nối Wi-Fi/MQTT và gửi dữ liệu lên Broker.

Hai task trao đổi qua queue 10 bản ghi. Khi queue đầy, firmware bỏ bản ghi cũ nhất để ưu tiên số liệu mới. Task an toàn không chờ MQTT, giúp tiếp tục xử lý điều khiển khi Broker hoặc mạng bị gián đoạn.

## Kết quả thử nghiệm

* Nhận đủ **200/200** bản tin trong thử nghiệm 10 phút; tỷ lệ mất gói **0%**.
* Khi Broker bị dừng 30 giây, task an toàn vẫn hoạt động; MQTT tự kết nối lại khi Broker khởi động.
* Thử tải `SLOW 5000` xác nhận queue áp dụng chính sách **Drop Oldest** khi đầy.

Chi tiết nằm trong `log_10min.txt` và `log_disconnect.txt`.

## Đóng góp cá nhân

Thực hiện cấu hình Mosquitto và Docker Compose, phát triển firmware FreeRTOS/MQTT, xử lý queue và dữ liệu `null`/`0`, chạy thử nghiệm, thu thập log, tạo sơ đồ kiến trúc và tài liệu hóa topic/payload.
