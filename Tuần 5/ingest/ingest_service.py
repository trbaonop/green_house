#!/usr/bin/env python3
"""
ingest_service.py — Bài 5 CSE3069: Thu nhận MQTT → SQLite
=========================================================
Subscribe topic nhom01/node01/telemetry, kiểm tra bản ghi,
ghi vào SQLite với lưu trữ bền (WAL mode).

Cách chạy:
    pip install paho-mqtt
    python ingest_service.py

Yêu cầu: broker Mosquitto đang chạy (docker-compose up -d broker)
"""

import json
import logging
import os
import queue
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime

import paho.mqtt.client as mqtt

# ---------------------------------------------------------------------------
# Cấu hình & Nạp biến môi trường từ .env
# ---------------------------------------------------------------------------
def load_env_file() -> dict:
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    res = {}
    if os.path.isfile(env_path):
        with open(env_path, "r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                res[k.strip()] = v.strip().strip("'\"")
    return res

_env_vars = load_env_file()

MQTT_BROKER   = os.getenv("MQTT_BROKER_HOST", "localhost")
MQTT_PORT     = int(os.getenv("MQTT_BROKER_PORT", 1883))
MQTT_USERNAME = os.getenv("MQTT_USERNAME", "iot_user")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "iot_password123")
MQTT_TOPIC    = "nhom01/node01/telemetry"
DB_PATH       = os.path.join(os.path.dirname(__file__), "..", "greenhouse.db")

# Firebase Realtime Database
def normalize_firebase_url(url: str) -> str:
    if not url:
        return ""
    url = url.strip().rstrip("/")
    if "console.firebase.google.com" in url:
        import re
        m = re.search(r"/database/([^/]+)", url)
        if m:
            db_name = m.group(1)
            return f"https://{db_name}.firebaseio.com"
    return url

FIREBASE_URL   = normalize_firebase_url(os.getenv("FIREBASE_DATABASE_URL", _env_vars.get("FIREBASE_DATABASE_URL", "")))
FIREBASE_TOKEN = os.getenv("FIREBASE_LEGACY_TOKEN", _env_vars.get("FIREBASE_LEGACY_TOKEN", ""))

# Dải hợp lý — ngoài dải → đánh dấu suspect, KHÔNG tự sửa
TEMP_MIN, TEMP_MAX = -40.0, 100.0
HUM_MIN,  HUM_MAX  =   0.0, 100.0
PUMP_MIN, PUMP_MAX =   0,   100

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("ingest")

# ---------------------------------------------------------------------------
# Worker đồng bộ Firebase bất đồng bộ (Non-blocking SQLite pipeline)
# ---------------------------------------------------------------------------
_fb_queue = queue.Queue(maxsize=200)

def firebase_sync_worker():
    if not FIREBASE_URL:
        log.info("Không phát hiện cấu hình Firebase trong .env. Chỉ lưu SQLite.")
        return
    log.info("Khởi động worker đồng bộ Firebase Cloud: %s", FIREBASE_URL)

    while True:
        item = _fb_queue.get()
        if item is None:
            break
        payload, suspect = item
        device_id = payload.get("device_id", "node01")
        ts = payload.get("ts", int(time.time()))

        fb_data = {
            "device_id":  device_id,
            "ts":         ts,
            "seq":        payload.get("seq"),
            "temp":       payload.get("temp"),
            "hum":        payload.get("hum"),
            "rain":       payload.get("rain"),
            "unit":       payload.get("unit", {"temp": "C", "hum": "%", "rain": "binary"}),
            "valid":      payload.get("valid"),
            "suspect":    suspect,
            "fsm_state":  payload.get("fsm_state"),
            "pump":       payload.get("pump"),
            "synced_at":  datetime.now().isoformat()
        }

        body = json.dumps(fb_data).encode("utf-8")
        auth_param = f"?auth={FIREBASE_TOKEN}" if FIREBASE_TOKEN else ""

        # 1. Cập nhật bản ghi mới nhất: /greenhouse/nodes/<device_id>/latest.json
        url_latest = f"{FIREBASE_URL}/greenhouse/nodes/{device_id}/latest.json{auth_param}"
        try:
            req1 = urllib.request.Request(url_latest, data=body, method="PUT")
            req1.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(req1, timeout=6) as resp:
                pass

            # 2. Lưu lịch sử theo mốc thời gian: /greenhouse/nodes/<device_id>/history/<ts>.json
            url_hist = f"{FIREBASE_URL}/greenhouse/nodes/{device_id}/history/{ts}.json{auth_param}"
            req2 = urllib.request.Request(url_hist, data=body, method="PUT")
            req2.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(req2, timeout=6) as resp:
                pass

            log.info("☁️ [Firebase] Đã đồng bộ lên Cloud: node=%s seq=%s ts=%s", device_id, payload.get("seq"), ts)
        except Exception as e:
            log.warning("☁️ [Firebase] Lỗi đồng bộ: %s (Dữ liệu SQLite vẫn an toàn)", e)
        finally:
            _fb_queue.task_done()

# ---------------------------------------------------------------------------
# DB helpers (thread-safe: mỗi thread tạo connection riêng)
# ---------------------------------------------------------------------------
_db_lock = threading.Lock()   # serialize writes cho SQLite WAL

def get_conn() -> sqlite3.Connection:
    """Trả về connection SQLite trong WAL mode."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")   # WAL cho phép đọc + ghi song song
    conn.execute("PRAGMA synchronous=NORMAL;") # Cân bằng tốc độ vs an toàn
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Tạo bảng từ schema.sql nếu chưa tồn tại."""
    schema_path = os.path.join(os.path.dirname(__file__), "..", "schema.sql")
    with open(schema_path, "r", encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.commit()
    log.info("DB initialised at %s", os.path.abspath(DB_PATH))

# ---------------------------------------------------------------------------
# Kiểm tra bản ghi
# ---------------------------------------------------------------------------
REQUIRED_FIELDS = {"device_id", "ts", "valid"}

def validate(payload: dict) -> tuple[bool, str, bool]:
    """
    Trả về (accepted, reason, suspect).

    - accepted=False → từ chối (thiếu trường bắt buộc / sai kiểu).
    - accepted=True, suspect=True → giá trị ngoài dải hợp lý (vẫn lưu, đánh dấu).
    - accepted=True, suspect=False → bản ghi sạch.
    """
    # 1. Thiếu trường bắt buộc → từ chối ngay
    missing = REQUIRED_FIELDS - payload.keys()
    if missing:
        return False, f"Thiếu trường bắt buộc: {missing}", False

    # 2. device_id phải là chuỗi không rỗng
    if not isinstance(payload["device_id"], str) or not payload["device_id"].strip():
        return False, "device_id không hợp lệ (rỗng hoặc sai kiểu)", False

    # 3. ts phải là số nguyên dương
    ts = payload.get("ts")
    if not isinstance(ts, (int, float)) or ts <= 0:
        return False, f"ts không hợp lệ: {ts!r}", False

    # 4. Kiểm tra dải hợp lý (không từ chối, chỉ đánh dấu suspect)
    suspect = False
    reasons = []

    temp = payload.get("temp")
    if temp is not None:
        if not isinstance(temp, (int, float)):
            return False, f"temp sai kiểu: {temp!r}", False
        if not (TEMP_MIN <= temp <= TEMP_MAX):
            suspect = True
            reasons.append(f"temp={temp} ngoài [{TEMP_MIN}, {TEMP_MAX}]°C")

    hum = payload.get("hum")
    if hum is not None:
        if not isinstance(hum, (int, float)):
            return False, f"hum sai kiểu: {hum!r}", False
        if not (HUM_MIN <= hum <= HUM_MAX):
            suspect = True
            reasons.append(f"hum={hum} ngoài [{HUM_MIN}, {HUM_MAX}]%")

    pump = payload.get("pump")
    if pump is not None:
        if not isinstance(pump, (int, float)):
            return False, f"pump sai kiểu: {pump!r}", False
        if not (PUMP_MIN <= pump <= PUMP_MAX):
            suspect = True
            reasons.append(f"pump={pump} ngoài [{PUMP_MIN}, {PUMP_MAX}]%")

    if suspect:
        log.warning("Bản ghi nghi ngờ (vẫn lưu): %s", "; ".join(reasons))

    return True, "OK" if not suspect else "; ".join(reasons), suspect


def is_duplicate(conn: sqlite3.Connection, device_id: str, ts: int, seq) -> bool:
    """
    Định nghĩa 'trùng': cùng device_id + cùng ts + cùng seq.
    Nếu seq là None (không có), dùng device_id + ts.
    Giữ bản ghi ĐẦU TIÊN, bỏ bản mới trùng.
    """
    if seq is not None:
        row = conn.execute(
            "SELECT id FROM telemetry WHERE device_id=? AND ts=? AND seq=? LIMIT 1",
            (device_id, int(ts), int(seq))
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT id FROM telemetry WHERE device_id=? AND ts=? AND seq IS NULL LIMIT 1",
            (device_id, int(ts))
        ).fetchone()
    return row is not None


def insert_record(conn: sqlite3.Connection, payload: dict, suspect: bool) -> None:
    unit = payload.get("unit", {})
    if isinstance(unit, str):
        unit = {}
    received_at = int(time.time())

    conn.execute(
        """
        INSERT INTO telemetry
            (device_id, ts, seq, temp, hum, rain,
             unit_temp, unit_hum, unit_rain,
             valid, fsm_state, pump, received_at, suspect)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            payload["device_id"],
            int(payload["ts"]),
            payload.get("seq"),
            payload.get("temp"),
            payload.get("hum"),
            payload.get("rain"),
            unit.get("temp", "C"),
            unit.get("hum", "%"),
            unit.get("rain", "binary"),
            1 if payload.get("valid") else 0,
            payload.get("fsm_state"),
            payload.get("pump"),
            received_at,
            1 if suspect else 0,
        ),
    )
    conn.commit()


def reject_record(conn: sqlite3.Connection, raw: str, reason: str) -> None:
    conn.execute(
        "INSERT INTO rejected_records (raw_payload, reason, received_at) VALUES (?,?,?)",
        (raw, reason, int(time.time())),
    )
    conn.commit()

# ---------------------------------------------------------------------------
# MQTT callbacks
# ---------------------------------------------------------------------------
_conn: sqlite3.Connection | None = None


def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        log.info("Đã kết nối MQTT broker tại %s:%s", MQTT_BROKER, MQTT_PORT)
        client.subscribe(MQTT_TOPIC, qos=1)
        log.info("Subscribe topic: %s", MQTT_TOPIC)
    else:
        log.error("Kết nối thất bại, rc=%s", rc)


def on_message(client, userdata, msg):
    global _conn
    raw = msg.payload.decode("utf-8", errors="replace")
    log.debug("← %s  %s", msg.topic, raw)

    # Parse JSON
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        log.warning("Payload không phải JSON hợp lệ: %s — %s", e, raw[:120])
        with _db_lock:
            reject_record(_conn, raw, f"JSON parse error: {e}")
        return

    # Kiểm tra bản ghi
    accepted, reason, suspect = validate(payload)
    if not accepted:
        log.warning("Từ chối bản ghi: %s", reason)
        with _db_lock:
            reject_record(_conn, raw, reason)
        return

    device_id = payload["device_id"]
    ts = payload["ts"]
    seq = payload.get("seq")

    # Kiểm trùng
    with _db_lock:
        if is_duplicate(_conn, device_id, ts, seq):
            log.info("Bỏ bản ghi trùng device_id=%s ts=%s seq=%s (giữ bản đầu tiên)", device_id, ts, seq)
            return
        insert_record(_conn, payload, suspect)

    # Đẩy bản ghi vào Queue đồng bộ Firebase Realtime Database (không block SQLite)
    if FIREBASE_URL:
        try:
            _fb_queue.put_nowait((payload, suspect))
        except queue.Full:
            log.warning("Firebase queue đầy, bỏ qua một mẫu Cloud để giữ hiệu năng SQLite.")

    status = "SUSPECT" if suspect else ("INVALID" if not payload.get("valid") else "OK")
    log.info("✓ Lưu SQLite [%s] device=%s ts=%s seq=%s temp=%s hum=%s",
             status, device_id, ts, seq, payload.get("temp"), payload.get("hum"))


def on_disconnect(client, userdata, disconnect_flags, rc, properties=None):
    if rc != 0:
        log.warning("Mất kết nối MQTT (rc=%s), sẽ tự kết nối lại...", rc)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    global _conn

    # Khởi tạo DB
    _conn = get_conn()
    init_db(_conn)

    # Khởi động Worker đồng bộ Firebase Cloud (nếu có cấu hình)
    if FIREBASE_URL:
        fb_thread = threading.Thread(target=firebase_sync_worker, daemon=True)
        fb_thread.start()

    # MQTT client
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="ingest_service")
    client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    client.on_connect    = on_connect
    client.on_message    = on_message
    client.on_disconnect = on_disconnect

    client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)

    log.info("Ingest service khởi động. Nhấn Ctrl+C để dừng.")
    try:
        client.loop_forever()
    except KeyboardInterrupt:
        log.info("Dừng service.")
    finally:
        client.disconnect()
        _conn.close()


if __name__ == "__main__":
    main()
