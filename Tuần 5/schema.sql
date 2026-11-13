-- =============================================================================
-- schema.sql — Bài 5 CSE3069: Lưu và phân tích dữ liệu
-- Database: SQLite
-- Mô tả: Lưu bản ghi telemetry từ MQTT topic nhom01/node01/telemetry
-- =============================================================================

-- Bảng chính lưu dữ liệu telemetry
CREATE TABLE IF NOT EXISTS telemetry (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id   TEXT    NOT NULL,                   -- Mã thiết bị, bắt buộc
    ts          INTEGER NOT NULL,                   -- Unix epoch (giây), bắt buộc
    seq         INTEGER,                            -- Số thứ tự bản tin (phát hiện rớt gói)
    temp        REAL,                               -- Nhiệt độ °C, NULL nếu cảm biến lỗi
    hum         REAL,                               -- Độ ẩm %, NULL nếu cảm biến lỗi
    rain        INTEGER,                            -- 0=khô, 1=mưa, NULL nếu cảm biến lỗi
    unit_temp   TEXT    DEFAULT 'C',
    unit_hum    TEXT    DEFAULT '%',
    unit_rain   TEXT    DEFAULT 'binary',
    valid       INTEGER NOT NULL DEFAULT 0,         -- 1=hợp lệ, 0=nghi ngờ/lỗi
    fsm_state   TEXT,                               -- RUNNING / STOPPED / ERROR
    pump        INTEGER,                            -- Công suất bơm 0–100%
    received_at INTEGER NOT NULL,                   -- Thời điểm service nhận (epoch giây, do server ghi)
    suspect     INTEGER NOT NULL DEFAULT 0          -- 1 = giá trị ngoài dải hợp lý (không tự sửa)
);

-- Index để truy vấn theo thiết bị + thời gian nhanh
CREATE INDEX IF NOT EXISTS idx_telemetry_device_ts
    ON telemetry (device_id, ts);

-- Index để truy vấn theo thời gian nhận
CREATE INDEX IF NOT EXISTS idx_telemetry_received_at
    ON telemetry (received_at);

-- =============================================================================
-- Bảng log các bản ghi bị từ chối (để kiểm tra sau)
-- =============================================================================
CREATE TABLE IF NOT EXISTS rejected_records (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_payload TEXT    NOT NULL,   -- Payload JSON gốc
    reason      TEXT    NOT NULL,   -- Lý do từ chối
    received_at INTEGER NOT NULL
);
