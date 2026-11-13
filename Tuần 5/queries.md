# Truy vấn mẫu — Bài 5: Lưu và phân tích dữ liệu

Tài liệu này tổng hợp các truy vấn SQL mẫu trên cơ sở dữ liệu `greenhouse.db` (SQLite) cho hệ thống giám sát và điều khiển nhà kính.

---

## 1. Truy vấn mẫu theo đề bài: Lấy toàn bộ bản ghi của node01 trong 1 giờ gần nhất

```sql
SELECT *
FROM   telemetry
WHERE  device_id = 'node01'
  AND  ts BETWEEN (strftime('%s', 'now') - 3600) AND strftime('%s', 'now')
ORDER  BY ts ASC;
```

*Giải thích:*
- Sử dụng `strftime('%s', 'now')` để lấy Unix epoch hiện tại của hệ thống.
- Lọc theo `device_id = 'node01'` và khoảng thời gian 3600 giây (1 giờ).
- Tận dụng index `idx_telemetry_device_ts (device_id, ts)`.

---

## 2. Truy vấn theo khoảng thời gian tuyệt đối $T_0$ và $T_1$ (Kiểm tra lưu trữ bền)

```sql
SELECT id, device_id, ts, seq, temp, hum, rain, valid, suspect, received_at
FROM   telemetry
WHERE  device_id = 'node01'
  AND  ts BETWEEN 1794298793 AND 1794298913
ORDER  BY ts ASC;
```

*Giải thích:*
- Chốt khoảng thời gian $T_0$ và $T_1$ cụ thể.
- Chạy trước và sau khi khởi động lại dịch vụ/DB để đối chiếu số lượng bản ghi và tính toàn vẹn dữ liệu.

---

## 3. Thống kê số lượng bản ghi theo thiết bị và cờ hợp lệ (Data Quality)

```sql
SELECT
    device_id,
    COUNT(*)                        AS total_records,
    SUM(valid)                      AS valid_count,
    SUM(1 - valid)                  AS invalid_count,
    SUM(suspect)                    AS suspect_count
FROM   telemetry
GROUP  BY device_id;
```

*Giải thích:*
- Phân loại rõ ràng:
  - `valid = 1`: Số đo cảm biến tốt, hợp lệ.
  - `valid = 0`: Cảm biến lỗi (`temp` hoặc `hum` là `null`).
  - `suspect = 1`: Số đo nằm ngoài dải đo hợp lý (ví dụ $temp > 100^\circ\text{C}$), vẫn lưu nguyên giá trị, không sửa bậy thành 0.

---

## 4. Truy vấn nặng dùng trong thí nghiệm tải đồng thời (Concurrent Load Test)

```sql
SELECT
    device_id,
    COUNT(*)               AS n,
    ROUND(AVG(temp),  2)   AS avg_temp_C,
    ROUND(AVG(hum),   2)   AS avg_hum_pct,
    MIN(temp)              AS min_temp,
    MAX(temp)              AS max_temp,
    MIN(ts)                AS ts_first,
    MAX(ts)                AS ts_last
FROM   telemetry
WHERE  valid = 1
GROUP  BY device_id;
```

*Giải thích:*
- Quét toàn bảng, tính toán tổng hợp (Aggregate functions `AVG`, `MIN`, `MAX`) trên các điểm dữ liệu hợp lệ.
- Chạy lặp lại liên tục để kiểm tra xem việc đọc nặng có làm chậm quá trình ghi bản ghi mới từ MQTT hay không.

---

## 5. Truy vấn kiểm tra các bản ghi bị từ chối (Rejected Records)

```sql
SELECT id, reason, raw_payload, datetime(received_at, 'unixepoch', 'localtime') AS received_local
FROM   rejected_records
ORDER  BY received_at DESC
LIMIT  20;
```

*Giải thích:*
- Xem nhật ký các bản tin sai schema, thiếu `device_id`, thiếu `ts` hoặc JSON lỗi cú pháp.

---

## 6. Truy vấn phát hiện rớt gói (Packet Loss / Sequence Gap Detection)

```sql
WITH ordered AS (
    SELECT seq,
           LAG(seq) OVER (PARTITION BY device_id ORDER BY ts) AS prev_seq
    FROM   telemetry
    WHERE  device_id = 'node01'
      AND  seq IS NOT NULL
)
SELECT prev_seq + 1       AS gap_start,
       seq - 1            AS gap_end,
       seq - prev_seq - 1 AS missing_count
FROM   ordered
WHERE  seq - prev_seq > 1;
```

*Giải thích:*
- Dùng Window Function `LAG(seq)` để tìm khoảng trống thứ tự gói tin giữa 2 bản ghi liên tiếp.
