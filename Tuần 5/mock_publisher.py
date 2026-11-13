#!/usr/bin/env python3
"""
mock_publisher.py — Bài 5 CSE3069
===================================
Phát lại bản tin MQTT giả lập node01 để kiểm tra ingest_service
khi ESP32 chưa sẵn hoặc không ở cùng mạng.

Nguồn dữ liệu: GHI RÕ trong README là dữ liệu giả lập, không phải số đo thật.

Cách dùng:
    python mock_publisher.py              # 60 bản ghi, 1 bản/giây
    python mock_publisher.py --count 200 --interval 0.5
"""

import argparse
import json
import os
import random
import time

import paho.mqtt.client as mqtt

MQTT_BROKER   = os.getenv("MQTT_BROKER_HOST", "localhost")
MQTT_PORT     = int(os.getenv("MQTT_BROKER_PORT", 1883))
MQTT_USERNAME = os.getenv("MQTT_USERNAME", "iot_user")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "iot_password123")
TOPIC         = "nhom01/node01/telemetry"

# Các bản ghi đặc biệt để kiểm tra validation
SPECIAL_RECORDS = [
    # Nhiệt độ NULL (cảm biến lỗi)
    {"device_id": "node01", "ts": None, "seq": None,
     "temp": None, "hum": None, "rain": 0,
     "unit": {"temp": "C", "hum": "%", "rain": "binary"},
     "valid": False, "fsm_state": "ERROR", "pump": 0},
    # Nhiệt độ ngoài dải hợp lý (suspect)
    {"device_id": "node01", "ts": None, "seq": None,
     "temp": 105.0, "hum": 65.0, "rain": 0,
     "unit": {"temp": "C", "hum": "%", "rain": "binary"},
     "valid": True, "fsm_state": "RUNNING", "pump": 80},
    # Thiếu trường device_id (bị từ chối)
    {"ts": None, "seq": None, "temp": 28.0, "hum": 60.0},
]


def make_payload(seq: int, ts: int) -> dict:
    temp = round(random.uniform(24.0, 35.0), 1)
    hum  = round(random.uniform(55.0, 85.0), 1)
    rain = random.choices([0, 1], weights=[8, 2])[0]
    return {
        "device_id": "node01",
        "ts":        ts,
        "seq":       seq,
        "temp":      temp,
        "hum":       hum,
        "rain":      rain,
        "unit":      {"temp": "C", "hum": "%", "rain": "binary"},
        "valid":     True,
        "fsm_state": "RUNNING",
        "pump":      100 if rain == 0 else 0,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--count",    type=int,   default=60,   help="Số bản ghi bình thường")
    p.add_argument("--interval", type=float, default=1.0,  help="Giây giữa hai bản ghi")
    p.add_argument("--inject-special", action="store_true",
                   help="Chèn bản ghi đặc biệt (null, suspect, từ chối) xen kẽ")
    args = p.parse_args()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="mock_publisher")
    client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
    client.loop_start()

    print(f"[MOCK PUBLISHER] Phát {args.count} bản ghi tới {TOPIC}")
    print("[CẢNH BÁO] Đây là dữ liệu giả lập — không phải số đo thật từ mô hình.\n")

    base_ts = int(time.time())
    special_idx = 0

    for i in range(args.count):
        ts  = base_ts + i * int(args.interval)
        seq = i + 1

        # Chèn bản ghi đặc biệt mỗi 10 bản ghi
        if args.inject_special and i > 0 and i % 10 == 0:
            sp = dict(SPECIAL_RECORDS[special_idx % len(SPECIAL_RECORDS)])
            sp["ts"]  = ts
            sp["seq"] = seq
            payload_str = json.dumps(sp)
            client.publish(TOPIC, payload_str, qos=1)
            print(f"  → [SPECIAL #{special_idx+1}] {payload_str[:80]}")
            special_idx += 1
            time.sleep(args.interval * 0.1)

        payload = make_payload(seq, ts)
        payload_str = json.dumps(payload)
        client.publish(TOPIC, payload_str, qos=1)
        print(f"  → seq={seq:>4}  ts={ts}  temp={payload['temp']}°C  hum={payload['hum']}%")
        time.sleep(args.interval)

    client.loop_stop()
    client.disconnect()
    print(f"\n[MOCK PUBLISHER] Hoàn thành. Đã phát {args.count} bản ghi.")


if __name__ == "__main__":
    main()
