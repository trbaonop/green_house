#!/usr/bin/env python3
"""
Script vẽ sơ đồ task/queue và nơi triển khai (task_queue.png)
cho Bài thực hành 04 - CSE3069.
"""

import matplotlib.pyplot as plt
import matplotlib.patches as patches

def draw_diagram():
    fig, ax = plt.subplots(figsize=(14, 8), dpi=300)
    ax.set_xlim(0, 14)
    ax.set_ylim(0, 8.5)
    ax.axis('off')

    # Background color
    fig.patch.set_facecolor('#F8F9FA')
    ax.set_facecolor('#F8F9FA')

    # Title
    ax.text(7, 8.1, "KIẾN TRÚC FREERTOS TASK, QUEUE VÀ NƠI TRIỂN KHAI (BÀI 4 - MQTT)",
            ha='center', va='center', fontsize=15, fontweight='bold', color='#1A202C')
    ax.text(7, 7.7, "CSE3069 · Đa tác vụ non-blocking trên ESP32 Dual Core kết nối Docker Mosquitto Broker",
            ha='center', va='center', fontsize=10.5, color='#4A5568')

    # Box 1: Hardware Layer / Greenhouse Model (Left Bottom)
    box_hw = patches.FancyBboxPatch((0.5, 0.6), 3.2, 2.3, boxstyle="round,pad=0.15",
                                    ec='#CBD5E1', fc='#FFFFFF', lw=1.5)
    ax.add_patch(box_hw)
    ax.text(2.1, 2.65, "PHẦN CỨNG NHÀ KÍNH", ha='center', va='center', fontsize=11, fontweight='bold', color='#2B6CB0')
    hw_text = (
        "• DHT11 (I2C / GPIO14): T & H\n"
        "• Rain Sensor (GPIO34): 0/1\n"
        "• Pump PWM (GPIO5) / Dir (GPIO17)\n"
        "• Button (GPIO36 - Emergency)\n"
        "• LEDs (GPIO26 Red, GPIO27 Green)"
    )
    ax.text(0.7, 1.5, hw_text, ha='left', va='center', fontsize=9, color='#2D3748', linespacing=1.4)

    # Box 2: ESP32 Dual-Core (Container)
    box_esp = patches.FancyBboxPatch((0.5, 3.2), 7.8, 4.2, boxstyle="round,pad=0.2",
                                     ec='#2B6CB0', fc='#EDF2F7', lw=2)
    ax.add_patch(box_esp)
    ax.text(4.4, 7.15, "NODE ESP32-WROOM (Mô hình nhà kính - FreeRTOS Dual-Core)",
            ha='center', va='center', fontsize=12, fontweight='bold', color='#1A365D')

    # Sub-box 2.1: Core 1 - Safety FSM Task
    box_core1 = patches.FancyBboxPatch((0.8, 3.5), 3.3, 3.3, boxstyle="round,pad=0.15",
                                       ec='#319795', fc='#E6FFFA', lw=1.5)
    ax.add_patch(box_core1)
    ax.text(2.45, 6.55, "CORE 1: Task Safety FSM\n(Priority 2 - Chu kỳ 100ms)",
            ha='center', va='center', fontsize=10.5, fontweight='bold', color='#234E52')
    c1_desc = (
        "1. Quét nút nhấn khẩn cấp\n"
        "2. Đọc cảm biến DHT11 & Rain\n"
        "3. Kiểm tra dải an toàn (NaN/Range)\n"
        "4. Điều khiển FSM: RUN/STOP/ERR\n"
        "5. Tự ngắt bơm sau MAX_RUN (10s)\n"
        "6. Ghi record vào Queue (timeout = 0)"
    )
    ax.text(1.0, 4.85, c1_desc, ha='left', va='center', fontsize=8.8, color='#1D4044', linespacing=1.35)

    # Sub-box 2.2: FreeRTOS Queue (Center of ESP32)
    box_queue = patches.FancyBboxPatch((4.35, 4.2), 1.3, 2.3, boxstyle="square,pad=0.1",
                                       ec='#DD6B20', fc='#FEEBC8', lw=1.8)
    ax.add_patch(box_queue)
    ax.text(5.0, 6.2, "FreeRTOS\nQUEUE", ha='center', va='center', fontsize=10, fontweight='bold', color='#9C4221')
    q_desc = (
        "Kích thước:\n"
        "10 bản ghi\n\n"
        "Chính sách:\n"
        "DROP\nOLDEST\n"
        "(Non-blocking)"
    )
    ax.text(5.0, 4.95, q_desc, ha='center', va='center', fontsize=8, color='#7B341E', fontweight='bold', linespacing=1.2)

    # Sub-box 2.3: Core 0 - MQTT Task
    box_core0 = patches.FancyBboxPatch((5.85, 3.5), 2.25, 3.3, boxstyle="round,pad=0.15",
                                       ec='#805AD5', fc='#FAF5FF', lw=1.5)
    ax.add_patch(box_core0)
    ax.text(6.97, 6.55, "CORE 0: Task MQTT\n(Priority 1 - Background)",
            ha='center', va='center', fontsize=10, fontweight='bold', color='#44337A')
    c0_desc = (
        "1. Duy trì Wi-Fi Station\n"
        "2. Non-blocking reconnect\n"
        "3. Lấy record từ Queue\n"
        "4. Đóng gói JSON (null vs 0)\n"
        "5. Publish lên Broker\n"
        "6. Thử tải: vTaskDelay"
    )
    ax.text(6.0, 4.85, c0_desc, ha='left', va='center', fontsize=8.8, color='#322659', linespacing=1.35)

    # Box 3: Network / Protocol
    ax.text(8.8, 5.35, "TCP/IP Port 1883\nTopic: nhom01/node01/telemetry\nAuth: user/pass (QoS 0/1)",
            ha='center', va='center', fontsize=8.5, color='#4A5568', style='italic')

    # Box 4: Server Layer / Docker Broker (Right Top)
    box_broker = patches.FancyBboxPatch((9.6, 4.0), 3.9, 3.3, boxstyle="round,pad=0.2",
                                        ec='#38A169', fc='#F0FFF4', lw=2)
    ax.add_patch(box_broker)
    ax.text(11.55, 6.95, "DOCKER MQTT BROKER\n(PC / Raspberry Pi / VPS)",
            ha='center', va='center', fontsize=11.5, fontweight='bold', color='#1C4532')
    broker_desc = (
        "• Container: eclipse-mosquitto:2.0.18\n"
        "• docker-compose.yml quản lý service\n"
        "• Port 1883 exposed\n"
        "• password_file (Hashed, bí mật tách rời)\n"
        "• allow_anonymous false\n"
        "• Dữ liệu persistent volume (data & log)"
    )
    ax.text(9.85, 5.2, broker_desc, ha='left', va='center', fontsize=8.8, color='#22543D', linespacing=1.35)

    # Box 5: Subscriber / Testing Machine (Right Bottom)
    box_sub = patches.FancyBboxPatch((9.6, 0.6), 3.9, 2.7, boxstyle="round,pad=0.2",
                                     ec='#4A5568', fc='#FFFFFF', lw=1.5)
    ax.add_patch(box_sub)
    ax.text(11.55, 3.0, "MÁY QUAN SÁT & ĐỐI CHIẾU",
            ha='center', va='center', fontsize=11, fontweight='bold', color='#2D3748')
    sub_desc = (
        "• Tool: mosquitto_sub / MQTT Explorer\n"
        "• mosquitto_sub -t 'nhom01/node01/telemetry' -v\n"
        "• Ghi log liên tục 10 phút -> log_10min.txt\n"
        "• Thử nghiệm ngắt broker 30s -> log_disconnect.txt\n"
        "• Kiểm đếm sequence number (seq) để dò rớt gói"
    )
    ax.text(9.85, 1.7, sub_desc, ha='left', va='center', fontsize=8.8, color='#2D3748', linespacing=1.35)

    # Connecting Arrows
    # Hardware <-> Core 1
    ax.annotate('', xy=(2.1, 3.45), xytext=(2.1, 2.95),
                arrowprops=dict(arrowstyle="<->", color="#2B6CB0", lw=2))
    ax.text(2.2, 3.2, "Sensors / Actuators", fontsize=8, color="#2B6CB0")

    # Core 1 -> Queue
    ax.annotate('', xy=(4.3, 5.15), xytext=(4.15, 5.15),
                arrowprops=dict(arrowstyle="-|>", color="#DD6B20", lw=2.2))
    ax.text(4.22, 5.35, "Push (xQueueSend)", fontsize=7.5, color="#DD6B20", ha='center')

    # Queue -> Core 0
    ax.annotate('', xy=(5.8, 5.15), xytext=(5.65, 5.15),
                arrowprops=dict(arrowstyle="-|>", color="#805AD5", lw=2.2))
    ax.text(5.72, 5.35, "Pop (xQueueReceive)", fontsize=7.5, color="#805AD5", ha='center')

    # Core 0 -> Broker
    ax.annotate('', xy=(9.55, 5.65), xytext=(8.15, 5.65),
                arrowprops=dict(arrowstyle="-|>", color="#3182CE", lw=2.5))
    ax.text(8.85, 5.9, "MQTT PUBLISH", fontsize=9, fontweight='bold', color="#2B6CB0", ha='center')

    # Broker -> Subscriber
    ax.annotate('', xy=(11.55, 3.35), xytext=(11.55, 3.95),
                arrowprops=dict(arrowstyle="-|>", color="#38A169", lw=2.5))
    ax.text(11.65, 3.65, "SUBSCRIBE", fontsize=9, fontweight='bold', color="#2F855A")

    plt.tight_layout()
    plt.savefig('task_queue.png', bbox_inches='tight')
    print("Successfully generated task_queue.png")

if __name__ == '__main__':
    draw_diagram()
