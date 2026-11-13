# Bài 5 — Lưu và phân tích dữ liệu

* **Họ tên:** Đoàn Trung Bảo
* **MSSV:** 23110153
* **Học phần:** CSE3069 — Phát triển ứng dụng IoT (Tuần 10 - TH05)
* **Kế thừa:** Tiếp nối hệ thống trạm đo và gửi MQTT từ Bài 4

  <img width="1784" height="1070" alt="chart" src="https://github.com/user-attachments/assets/c378720f-b099-4ac2-8930-bf38ba05a94f" />
  <img width="1008" height="424" alt="Ảnh chụp màn hình 2026-11-13 194507" src="https://github.com/user-attachments/assets/4df0f578-0366-4407-8fcd-2b344bc1e4bf" />


---

## File trong repo này

* `src/main.cpp`: Mã nguồn firmware ESP32 FreeRTOS (Core 0: MQTT Task & Queue, Core 1: FSM an toàn máy bơm & đọc cảm biến). Tích hợp Web Dashboard cục bộ tự vẽ biểu đồ trực tiếp bằng HTML5 Canvas và tự xuất file `chart.png`.
* `include/config.h`: Cấu hình trạm đo (chân GPIO, Wi-Fi SSID/PASS, IP Mosquitto Broker, ngưỡng cảm biến an toàn).
* `ingest/ingest_service.py`: Service Python subscribe MQTT, kiểm duyệt dữ liệu, ghi đồng thời vào cơ sở dữ liệu bền vững cục bộ (SQLite WAL) và đồng bộ Cloud thời gian thực lên Firebase Realtime Database.
* `schema.sql`: Định nghĩa cấu trúc bảng `telemetry` và bảng lưu bản ghi sai `rejected_records`.
* `queries.md` / `queries.sql`: Các câu truy vấn SQL mẫu (truy vấn thời gian, đếm chất lượng, tính trung bình tải nặng, phát hiện rớt gói).
* `query_timerange.py`: Script truy vấn theo thiết bị và khoảng thời gian, tự động trích xuất và vẽ biểu đồ từ DB SQLite.
* `chart.png`: Biểu đồ trực quan hóa nhiệt độ và độ ẩm, đánh dấu riêng điểm `null` và điểm nghi ngờ `suspect` (có thể xuất từ DB hoặc trực tiếp từ ESP32 Web Dashboard).
* `restart_check.py`: Công cụ kiểm thử tự động lưu trữ bền trước và sau khi restart dịch vụ/DB.
* `log_before_restart.txt`: Kết quả truy vấn trước khi dừng dịch vụ.
* `log_after_restart.txt`: Kết quả truy vấn sau khi restart dịch vụ (khớp 100% dữ liệu).
* `concurrent_load_test.py`: Thí nghiệm đo tải đồng thời với ingest service đang chạy.
* `concurrent_load_selftest.py`: Kịch bản kiểm thử tải đồng thời độc lập (ghi + đọc nặng song song).
* `concurrent_load_result.txt`: Kết quả đo đạc độ trễ và số liệu thí nghiệm tải đồng thời.
* `mock_publisher.py`: Công cụ phát lại bản tin MQTT giả lập để kiểm thử logic khi chưa kết nối mô hình phần cứng.

---

## Hướng dẫn chạy hệ thống

### 1. Nạp Firmware ESP32 (Greenhouse Node)

### 2. Khởi động MQTT Broker (Mosquitto)
```bash
docker-compose up -d broker
```

### 3. Cài đặt môi trường Python cho Service
```bash
pip install paho-mqtt matplotlib numpy
```

### 4. Chạy Service thu nhận (Ingest Service — Lưu kép SQLite & Firebase)
```bash
python ingest/ingest_service.py
```
*Service sẽ:*
1. Tự động khởi tạo DB `greenhouse.db` và ghi dữ liệu có kiểm duyệt (lọc trùng, phân biệt `null` vs `0`, cờ `suspect`).
2. Tự động đọc cấu hình trong `.env` (`FIREBASE_DATABASE_URL`, `FIREBASE_LEGACY_TOKEN`) và kích hoạt **Worker đồng bộ bất đồng bộ** đẩy dữ liệu lên Firebase Realtime Database tại `/greenhouse/nodes/<device_id>/latest` và `/history/<ts>`. Việc đồng bộ Cloud chạy ở luồng riêng, không làm ảnh hưởng tới độ trễ ghi SQLite.

### 5. Xem biểu đồ trực tiếp từ ESP32 & Tự xuất ảnh chart.png
ESP32 tích hợp sẵn Web Server nội bộ (Port 80) với bộ đệm vòng lưu 60 điểm đo gần nhất và tự vẽ đồ thị bằng HTML5 Canvas:
- Mở trình duyệt truy cập: `http://<IP_ESP32>/` (IP hiển thị trên Serial Monitor khi kết nối Wi-Fi, ví dụ `http://172.16.x.x/`).
- Bấm nút **"📸 Xuất & Tải chart.png"** để trình duyệt lưu ngay biểu đồ về máy tính.
- Tùy chọn tự động: Truy cập đường dẫn `http://<IP_ESP32>/?auto_export=1` để ESP32 tự động kích hoạt tải file `chart.png`.

### 6. Truy vấn từ CSDL SQLite và xuất biểu đồ
```bash
# Truy vấn lịch sử từ DB và xuất biểu đồ ra file chart.png
python query_timerange.py --device node01 --save chart.png
```

### 7. Thử nghiệm khởi động lại và kiểm tra lưu trữ bền
```bash
# Bước 1: Ghi nhận log trước restart
python restart_check.py

# Bước 2: Dừng Ingest Service hoặc Restart máy
# Bước 3: Chạy lại Ingest Service
# Bước 4: Chạy kiểm tra sau restart (tự động so sánh đối chiếu)
python restart_check.py
```

### 7. Chạy thí nghiệm tải đồng thời
```bash
python concurrent_load_selftest.py
```

---

## 4.1 Quy tắc kiểm dữ liệu

Hệ thống tuân thủ chặt chẽ các quy chuẩn kiểm duyệt dữ liệu trước khi ghi vào cơ sở dữ liệu nhằm bảo toàn tính toàn vẹn và phân định rõ lỗi:

1. **Xử lý thiếu trường và sai schema (Bị từ chối):**
   - Trường bắt buộc: `device_id` (chuỗi ký tự không rỗng), `ts` (số nguyên dương), `valid` (boolean).
   - Nếu bản tin JSON bị lỗi cú pháp, thiếu trường bắt buộc, hoặc sai kiểu dữ liệu căn bản $\rightarrow$ **Từ chối ghi vào bảng chính**, lưu nguyên văn payload vào bảng `rejected_records` kèm lý do để phục vụ giám sát và kiểm tra hồi tố.

2. **Phân biệt rạch ròi giữa `null` và `0`:**
   - Giá trị `0` là một số đo vật lý hoàn toàn hợp lệ (ví dụ: nhiệt độ $0.0^\circ\text{C}$, hoặc cảm biến mưa trạng thái `0` nghĩa là trời khô ráo).
   - Giá trị `null` biểu thị tình trạng mất kết nối cảm biến (I2C NACK / cảm biến DHT lỗi đọc ra `NaN`). Khi đó, firmware gửi `temp: null, hum: null, valid: false`. Service chấp nhận lưu bản ghi này vào cơ sở dữ liệu với `valid = 0`, tuyệt đối không tự ý gán về `0`.

3. **Quy tắc khử bản ghi trùng (Deduplication):**
   - Khóa duy nhất xác định trùng: Tổ hợp `(device_id, ts, seq)`.
   - Chiến lược lựa chọn: **Giữ lại bản ghi đầu tiên đến**, bỏ qua bản ghi đến sau nếu trùng khóa (phù hợp với cơ chế QoS 1 retry hoặc gửi bù).

### Minh chứng bản ghi hợp lệ:
```json
{
  "device_id": "node01",
  "ts": 1794298793,
  "seq": 105,
  "temp": 28.4,
  "hum": 68.2,
  "rain": 0,
  "unit": {"temp": "C", "hum": "%", "rain": "binary"},
  "valid": true,
  "fsm_state": "RUNNING",
  "pump": 100
}
```

## 4.2 Kết quả truy vấn trước/sau restart

* **Phương pháp kiểm thử:** Chốt hai mốc thời gian tuyệt đối $T_0 = 1794298793$ và $T_1 = 1794298913$. Ngừng ghi mới trong khoảng này, chạy truy vấn lấy toàn bộ dữ liệu lưu vào `log_before_restart.txt`. Dừng toàn bộ tiến trình Ingest Service, khởi động lại hệ thống, và chạy lại đúng câu lệnh truy vấn trên khoảng thời gian $[T_0, T_1]$ để lưu vào `log_after_restart.txt`.

### Câu lệnh truy vấn SQL đối chiếu:
```sql
SELECT id, device_id, ts, seq, temp, hum, rain, valid, suspect, received_at
FROM   telemetry
WHERE  device_id = 'node01'
  AND  ts BETWEEN 1794298793 AND 1794298913
ORDER  BY ts ASC;
```

### Kết quả so sánh đối chiếu:
* Số lượng bản ghi trước khi restart: **120 bản ghi** (119 bản ghi valid, 1 bản ghi lỗi cảm biến `null`, 1 bản ghi nghi ngờ `suspect`).
* Số lượng bản ghi sau khi restart: **120 bản ghi**.
* **Độ tương đồng:** Khớp chính xác 100% từng trường dữ liệu, mã định danh, `seq` và giá trị số đo (Chi tiết đối chiếu trong `log_before_restart.txt` và `log_after_restart.txt`).
* **Cơ chế đảm bảo tính bền vững (Persistence):** Cơ sở dữ liệu SQLite được lưu trữ trực tiếp trên tập tin bền vững `greenhouse.db`. Chế độ ghi nhật ký trước `PRAGMA journal_mode = WAL` (Write-Ahead Logging) kết hợp với `PRAGMA synchronous = NORMAL` đảm bảo mỗi giao dịch (transaction) một khi đã commit sẽ được ghi an toàn xuống ổ đĩa vật lý, không bị thất thoát khi tiến trình đột ngột dừng hoặc khởi động lại.

---

## 4.3 Thí nghiệm tải đồng thời

* **Bài toán:** Kiểm tra xem khi có các truy vấn phân tích dữ liệu nặng (quét toàn bộ bảng, gom nhóm và tính giá trị trung bình) chạy liên tục, tiến trình ghi nhận dữ liệu mới từ MQTT vào cơ sở dữ liệu có bị nghẽn hoặc gia tăng độ trễ không.

* **Truy vấn nặng thực hiện song song (mỗi 0.5s):**
```sql
SELECT device_id, COUNT(*) AS n, AVG(temp), AVG(hum), MIN(temp), MAX(temp)
FROM   telemetry
WHERE  valid = 1
GROUP  BY device_id;
```

### Kết quả đo kiểm thực tế (`concurrent_load_result.txt`):

| Chỉ số quan sát | Điều kiện chuẩn (Baseline - Không có query nặng) | Dưới tải đồng thời (Loaded - Query nặng lặp lại) | Chênh lệch (Delta) |
| :--- | :--- | :--- | :--- |
| **Số lần ghi (Write operations)** | 20 mẫu | 30 mẫu | — |
| **Độ trễ ghi trung bình (Avg Write Latency)** | **2.688 ms** | **4.813 ms** | **+2.124 ms (+79.0%)** |
| **Độ trễ ghi lớn nhất (Max Write Latency)** | 4.218 ms | 62.221 ms | +58.003 ms |
| **Tần suất truy vấn nặng** | 0 | 60 lần / 30s | — |
| **Độ trễ truy vấn nặng trung bình** | — | **0.27 ms** | — |

PS C:\Users\Admin\Documents\PlatformIO\Projects\highline> python query_timerange.py --device node01 --t0 1794298793 --t1 1794298912 --save chart.png

============================================================
Truy vấn: device=node01  T0=2026-11-10T08:19:53Z  T1=2026-11-10T08:21:52Z
Số bản ghi: 120
  Hợp lệ (valid=1):   119
  Không hợp lệ:        1
  Nghi ngờ (suspect):  1
  seq min..max:        1..120
============================================================

5 bản ghi đầu:
  seq=   1  ts=08:19:53  temp=26.0°C  hum=61.7%  valid=✓  suspect=-
  seq=   2  ts=08:19:54  temp=26.8°C  hum=68.6%  valid=✓  suspect=-
  seq=   3  ts=08:19:55  temp=33.8°C  hum=68.8%  valid=✓  suspect=-
  seq=   4  ts=08:19:56  temp=24.8°C  hum=57.4%  valid=✓  suspect=-
  seq=   5  ts=08:19:57  temp=33.7°C  hum=82.5%  valid=✓  suspect=-
  ... (115 bản ghi nữa)


