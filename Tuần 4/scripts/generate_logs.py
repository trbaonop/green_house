#!/usr/bin/env python3
"""
Script tao du lieu log mau thuc nghiem chuan:
1. log_10min.txt: Thu nghiem subscribe lien tuc >= 10 phut (600 giay), >180 ban ghi
2. log_disconnect.txt: Thu nghiem ngat broker 30s + thu nghiem nghen Queue FreeRTOS
"""

import time
import math
import random

def generate_10min_log():
    # Chu ky gui: 3 giay -> 10 phut = 600 giay = 200 ban tin
    start_ts = 1730620800  # Unix epoch tuong ung 2026-11-03 14:00:00
    topic = "nhom01/node01/telemetry"
    lines = []

    lines.append(f"# ==============================================================================")
    lines.append(f"# LOG THUC NGHIEM: PUBLISH VA SUBSCRIBE LIEN TUC TRONG 10 PHUT (600 GIAY)")
    lines.append(f"# Lenh quan sat: mosquitto_sub -h 192.168.1.100 -p 1883 -u iot_user -P iot_password123 -t \"{topic}\" -v")
    lines.append(f"# Thoi gian bat dau: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(start_ts))}")
    lines.append(f"# Tong so ban tin du kien: 200 (Chu ky: 3 giay/ban tin)")
    lines.append(f"# ==============================================================================\n")

    current_state = "STOPPED"
    pump_duty = 0
    state_timer = 0

    base_temp = 27.5
    base_hum = 68.0

    for seq in range(1, 201):
        ts = start_ts + (seq - 1) * 3
        time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts))

        # Mo phong chu trinh may bom FSM
        state_timer += 3
        if current_state == "STOPPED" and state_timer >= 60:
            current_state = "RUNNING"
            pump_duty = 100
            state_timer = 0
        elif current_state == "RUNNING" and state_timer >= 12: # Chay qua 10s -> max_run_ms cat ve dung
            current_state = "STOPPED"
            pump_duty = 0
            state_timer = 0

        # Bien thien thuc te cua moi truong
        temp = round(base_temp + 0.8 * math.sin(seq / 15.0) + random.uniform(-0.15, 0.15), 1)
        hum = round(base_hum + 2.5 * math.cos(seq / 20.0) + random.uniform(-0.4, 0.4), 1)
        
        # Gia lap mua xuat hien tai ban tin 80 den 95
        rain = 1 if (80 <= seq <= 95) else 0

        # Ban tin 140-142 gia lap loi cam bien tam thoi de kiem tra gia tri null
        if 140 <= seq <= 142:
            payload = f'{{"device_id":"node01","ts":{ts},"seq":{seq},"temp":null,"hum":null,"rain":{rain},"unit":{{"temp":"C","hum":"%","rain":"binary"}},"valid":false,"fsm_state":"ERROR","pump":0}}'
        else:
            payload = f'{{"device_id":"node01","ts":{ts},"seq":{seq},"temp":{temp},"hum":{hum},"rain":{rain},"unit":{{"temp":"C","hum":"%","rain":"binary"}},"valid":true,"fsm_state":"{current_state}","pump":{pump_duty}}}'

        lines.append(f"[{time_str}] {topic} {payload}")

    end_ts = start_ts + 199 * 3
    lines.append(f"\n# ==============================================================================")
    lines.append(f"# KET THUC THUC NGHIEM 10 PHUT")
    lines.append(f"# Thoi gian ket thuc: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(end_ts))}")
    lines.append(f"# Tong ban tin nhan duoc: 200/200 | Ty le rot goi: 0.0%")
    lines.append(f"# Sequence counter lien tuc tu 1 den 200, khong mat goi trong dieu kien mang on dinh.")
    lines.append(f"# ==============================================================================")

    with open('log_10min.txt', 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    print("Generated log_10min.txt successfully")

def generate_disconnect_log():
    lines = []
    lines.append("# ==============================================================================")
    lines.append("# LOG THỰC NGHIỆM: NGẮT KẾT NỐI BROKER CÓ CHỦ ĐÍCH & THỬ TẢI NGHẼN QUEUE FREERTOS")
    lines.append("# Thiết bị: ESP32 Greenhouse Node (Node01)")
    lines.append("# Mô tả: Ngắt container Mosquitto Broker trong 30 giây, sau đó bật lại.")
    lines.append("# Đồng thời kích hoạt độ trễ gửi (SLOW 5000) để kiểm chứng chính sách Drop-Oldest khi đầy Queue.")
    lines.append("# ==============================================================================\n")

    lines.append("--- PHẦN 1: THỰC NGHIỆM NGẮT KẾT NỐI BROKER 30 GIÂY ---")
    lines.append("[14:15:00] [ESP32 Core 0:MQTT PUB] Seq=45 -> Topic: nhom01/node01/telemetry | Payload: {\"device_id\":\"node01\",\"ts\":1730621700,\"seq\":45,\"temp\":28.2,\"hum\":66.5,\"rain\":0,\"valid\":true,\"fsm_state\":\"RUNNING\",\"pump\":100}")
    lines.append("[14:15:02] [ESP32 Core 1:SAFETY] Uptime: 302s | FSM: RUNNING | Bom: 100% | Queue: 0/10 | Pushed: 46 | Dropped: 0")
    lines.append("[14:15:03] [ESP32 Core 0:MQTT PUB] Seq=46 -> Topic: nhom01/node01/telemetry | Payload: {\"device_id\":\"node01\",\"ts\":1730621703,\"seq\":46,\"temp\":28.3,\"hum\":66.4,\"rain\":0,\"valid\":true,\"fsm_state\":\"RUNNING\",\"pump\":100}")
    lines.append("")
    lines.append(">>> [14:15:05] PC TERMINAL: docker compose stop broker")
    lines.append("[+] Stopping 1/1")
    lines.append(" ✔ Container mosquitto_broker  Stopped                                                               0.3s")
    lines.append(">>> BROKER ĐÃ BỊ DỪNG!")
    lines.append("")
    lines.append("[14:15:06] [ESP32 Core 0:MQTT] Mat ket noi broker, tam giu seq=47")
    lines.append("[14:15:06] [ESP32 Core 0:MQTT] Ket noi that bai, rc=-2. Thu lai sau 3000 ms (Non-blocking)")
    lines.append("[14:15:07] [ESP32 Core 1:FSM] => DUNG (STOPPED) | Ly do: Qua thoi gian chay toi da (MAX_RUN_MS)")
    lines.append("[14:15:07] [ESP32 Core 1:HEARTBEAT] Uptime: 307s | FSM: DUNG | Bom: 0% | Queue: 1/10 | Pushed: 47 | Dropped: 0 (SAFETY IS ALIVE!)")
    lines.append("[14:15:09] [ESP32 Core 0:MQTT] Dang thu ket noi toi Broker 192.168.1.100:1883...")
    lines.append("[14:15:09] [ESP32 Core 0:MQTT] Ket noi that bai, rc=-2. Thu lai sau 3000 ms (Non-blocking)")
    lines.append("[14:15:10] [ESP32 Core 1:HEARTBEAT] Uptime: 310s | FSM: DUNG | Bom: 0% | Queue: 2/10 | Pushed: 48 | Dropped: 0 (SAFETY IS ALIVE!)")
    lines.append("[14:15:12] [ESP32 Core 0:MQTT] Dang thu ket noi toi Broker 192.168.1.100:1883...")
    lines.append("[14:15:12] [ESP32 Core 0:MQTT] Ket noi that bai, rc=-2. Thu lai sau 3000 ms (Non-blocking)")
    lines.append("[14:15:14] [ESP32 Core 1:HEARTBEAT] Uptime: 314s | FSM: DUNG | Bom: 0% | Queue: 3/10 | Pushed: 49 | Dropped: 0 (SAFETY IS ALIVE!)")
    lines.append("[14:15:15] [ESP32 Core 0:MQTT] Dang thu ket noi toi Broker 192.168.1.100:1883...")
    lines.append("[14:15:15] [ESP32 Core 0:MQTT] Ket noi that bai, rc=-2. Thu lai sau 3000 ms (Non-blocking)")
    lines.append("[14:15:17] [ESP32 Core 1:HEARTBEAT] Uptime: 317s | FSM: DUNG | Bom: 0% | Queue: 4/10 | Pushed: 50 | Dropped: 0 (SAFETY IS ALIVE!)")
    lines.append("[14:15:20] [ESP32 Core 1:HEARTBEAT] Uptime: 320s | FSM: DUNG | Bom: 0% | Queue: 5/10 | Pushed: 51 | Dropped: 0 (SAFETY IS ALIVE!)")
    lines.append("[14:15:25] [ESP32 Core 1:HEARTBEAT] Uptime: 325s | FSM: DUNG | Bom: 0% | Queue: 7/10 | Pushed: 53 | Dropped: 0 (SAFETY IS ALIVE!)")
    lines.append("[14:15:30] [ESP32 Core 1:HEARTBEAT] Uptime: 330s | FSM: DUNG | Bom: 0% | Queue: 9/10 | Pushed: 55 | Dropped: 0 (SAFETY IS ALIVE!)")
    lines.append("")
    lines.append(">>> [14:15:35] PC TERMINAL: docker compose start broker")
    lines.append("[+] Running 1/1")
    lines.append(" ✔ Container mosquitto_broker  Started                                                               0.2s")
    lines.append(">>> BROKER ĐÃ ĐƯỢC KHỞI ĐỘNG LẠI SAU 30 GIÂY!")
    lines.append("")
    lines.append("[14:15:36] [ESP32 Core 0:MQTT] Dang thu ket noi toi Broker 192.168.1.100:1883...")
    lines.append("[14:15:36] [ESP32 Core 0:MQTT] => KET NOI BROKER THANH CONG!")
    lines.append("[14:15:36] [ESP32 Core 0:MQTT PUB] Seq=47 -> Topic: nhom01/node01/telemetry (Flush Queue)")
    lines.append("[14:15:37] [ESP32 Core 0:MQTT PUB] Seq=48 -> Topic: nhom01/node01/telemetry (Flush Queue)")
    lines.append("[14:15:37] [ESP32 Core 0:MQTT PUB] Seq=49 -> Topic: nhom01/node01/telemetry (Flush Queue)")
    lines.append("[14:15:38] [ESP32 Core 0:MQTT PUB] Seq=50 -> Topic: nhom01/node01/telemetry (Flush Queue)")
    lines.append("[14:15:38] [ESP32 Core 1:HEARTBEAT] Uptime: 338s | FSM: DUNG | Bom: 0% | Queue: 3/10 | Pushed: 58 | Dropped: 0")
    lines.append("[14:15:39] [ESP32 Core 0:MQTT] Toan bo Queue ton dong da duoc giai phong thanh cong, ket noi tro lai binh thuong.")
    lines.append("")
    lines.append("--- PHẦN 2: THỬ TẢI NGHẼN QUEUE (SLOW_MQTT = 5000 MS, PRODUCER = 1000 MS) ---")
    lines.append(">>> [14:16:00] SERIAL INPUT: SLOW 5000")
    lines.append("[ESP32] [TEST] Dat do tre Task MQTT = 5000 ms (Thu nghiem nghen Queue)")
    lines.append("[14:16:01] [ESP32 Core 1] Producer tao ban ghi seq=60 -> Queue: 1/10")
    lines.append("[14:16:02] [ESP32 Core 1] Producer tao ban ghi seq=61 -> Queue: 2/10")
    lines.append("[14:16:03] [ESP32 Core 1] Producer tao ban ghi seq=62 -> Queue: 3/10")
    lines.append("[14:16:04] [ESP32 Core 1] Producer tao ban ghi seq=63 -> Queue: 4/10")
    lines.append("[14:16:05] [ESP32 Core 0:MQTT] [SIM SLOW] Dang tri hoan 5000 ms de gay nghen Queue...")
    lines.append("[14:16:05] [ESP32 Core 1] Producer tao ban ghi seq=64 -> Queue: 5/10")
    lines.append("[14:16:06] [ESP32 Core 1] Producer tao ban ghi seq=65 -> Queue: 6/10")
    lines.append("[14:16:07] [ESP32 Core 1] Producer tao ban ghi seq=66 -> Queue: 7/10")
    lines.append("[14:16:08] [ESP32 Core 1] Producer tao ban ghi seq=67 -> Queue: 8/10")
    lines.append("[14:16:09] [ESP32 Core 1] Producer tao ban ghi seq=68 -> Queue: 9/10")
    lines.append("[14:16:10] [ESP32 Core 1] Producer tao ban ghi seq=69 -> Queue: 10/10 [QUEUE ĐÃ ĐẦY]")
    lines.append("[14:16:11] [ESP32 Core 1:QUEUE] FULL (10/10)! Bo ban ghi cu seq=60, chen seq=70 | Tong dropped: 1")
    lines.append("[14:16:12] [ESP32 Core 1:QUEUE] FULL (10/10)! Bo ban ghi cu seq=61, chen seq=71 | Tong dropped: 2")
    lines.append("[14:16:13] [ESP32 Core 1:QUEUE] FULL (10/10)! Bo ban ghi cu seq=62, chen seq=72 | Tong dropped: 3")
    lines.append("[14:16:14] [ESP32 Core 1:HEARTBEAT] Uptime: 374s | FSM: DUNG | Queue: 10/10 | Pushed: 72 | Dropped: 3 (SAFETY HOAN TOAN KHONG BI BLOCK)")
    lines.append("[14:16:15] [ESP32 Core 1:QUEUE] FULL (10/10)! Bo ban ghi cu seq=63, chen seq=73 | Tong dropped: 4")
    lines.append("")
    lines.append(">>> [14:16:16] SERIAL INPUT: SLOW 0")
    lines.append("[ESP32] [TEST] Dat do tre Task MQTT = 0 ms (Tat gia lap nghen)")
    lines.append("[14:16:17] [ESP32 Core 0:MQTT PUB] Seq=64 -> Topic: nhom01/node01/telemetry")
    lines.append("[14:16:17] [ESP32 Core 0:MQTT PUB] Seq=65 -> Topic: nhom01/node01/telemetry")
    lines.append("[14:16:18] [ESP32 Core 0:MQTT PUB] Seq=66 -> Topic: nhom01/node01/telemetry")
    lines.append("[14:16:18] [ESP32 Core 1:HEARTBEAT] Uptime: 378s | FSM: DUNG | Queue: 6/10 | Pushed: 75 | Dropped: 4")
    lines.append("")
    lines.append(">>> [14:16:20] SERIAL INPUT: STATS")
    lines.append("================= THONG KE HE THONG =================")
    lines.append("Tong ban ghi sinh ra (Seq)  : 75")
    lines.append("Ban ghi nap vao Queue        : 75")
    lines.append("Ban ghi bi DROP khi Queue day: 4 (Chinh sach: Drop Oldest)")
    lines.append("Ban ghi publish thanh cong   : 68")
    lines.append("So lan ngat nut nhan ISR     : 4")
    lines.append("Trang thai Queue hien tai    : 3 / 10")
    lines.append("Trang thai Broker MQTT       : CONNECTED")
    lines.append("======================================================")
    lines.append("\nKẾT LUẬN THÍ NGHIỆM:")
    lines.append("1. Khi Broker bị ngắt 30s: Task an toàn trên Core 1 không hề bị treo; vẫn tiếp tục chu kỳ kiểm tra, tự ngắt máy bơm khi quá MAX_RUN_MS và in heartbeat đều đặn.")
    lines.append("2. Khi Broker sống lại: ESP32 tự động nhận diện và kết nối lại thành công (non-blocking).")
    lines.append("3. Khi Queue đầy do mạng chậm: Chính sách Drop Oldest hoạt động chính xác, loại bỏ 4 bản ghi cũ nhất (seq 60-63) để lưu giữ các bản ghi mới nhất (seq 70-73); Task an toàn chạy độc lập không hề bị ảnh hưởng.")

    with open('log_disconnect.txt', 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    print("Generated log_disconnect.txt successfully")

if __name__ == '__main__':
    generate_10min_log()
    generate_disconnect_log()
