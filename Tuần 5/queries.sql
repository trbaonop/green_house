-- =============================================================================
-- queries.md / queries.sql — Bài 5 CSE3069
-- Các truy vấn mẫu SQL cho database greenhouse.db
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Q1. Lấy toàn bộ bản ghi của node01 trong 1 giờ gần nhất
--     (truy vấn mẫu yêu cầu từ đề bài)
-- ---------------------------------------------------------------------------
SELECT *
FROM   telemetry
WHERE  device_id = 'node01'
  AND  ts BETWEEN (strftime('%s','now') - 3600) AND strftime('%s','now')
ORDER  BY ts ASC;

-- ---------------------------------------------------------------------------
-- Q2. Truy vấn theo khoảng thời gian tuyệt đối T0/T1
--     (dùng cho thí nghiệm kiểm tra restart)
-- ---------------------------------------------------------------------------
-- Thay T0 và T1 bằng giá trị epoch thực tế đã ghi trong restart_log.txt
SELECT id, device_id, ts, seq, temp, hum, rain, valid, suspect, received_at
FROM   telemetry
WHERE  device_id = 'node01'
  AND  ts BETWEEN 1730621000 AND 1730624600   -- <-- thay T0, T1
ORDER  BY ts ASC;

-- ---------------------------------------------------------------------------
-- Q3. Đếm số bản ghi theo thiết bị và trạng thái valid/invalid
-- ---------------------------------------------------------------------------
SELECT
    device_id,
    COUNT(*)                        AS total,
    SUM(valid)                      AS valid_count,
    SUM(1 - valid)                  AS invalid_count,
    SUM(suspect)                    AS suspect_count
FROM   telemetry
GROUP  BY device_id;

-- ---------------------------------------------------------------------------
-- Q4. Truy vấn "nặng" dùng cho thí nghiệm tải đồng thời
--     Quét toàn bảng, tính trung bình — chạy song song với ingest_service
-- ---------------------------------------------------------------------------
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

-- ---------------------------------------------------------------------------
-- Q5. Danh sách bản ghi bị từ chối
-- ---------------------------------------------------------------------------
SELECT id, reason, raw_payload, datetime(received_at,'unixepoch','localtime') AS received_local
FROM   rejected_records
ORDER  BY received_at DESC
LIMIT  20;

-- ---------------------------------------------------------------------------
-- Q6. Phát hiện rớt gói (khoảng seq không liên tục)
-- ---------------------------------------------------------------------------
WITH ordered AS (
    SELECT seq,
           LAG(seq) OVER (PARTITION BY device_id ORDER BY ts) AS prev_seq
    FROM   telemetry
    WHERE  device_id = 'node01'
      AND  seq IS NOT NULL
)
SELECT prev_seq + 1  AS gap_start,
       seq - 1       AS gap_end,
       seq - prev_seq - 1 AS missing_count
FROM   ordered
WHERE  seq - prev_seq > 1;
