#pragma once
#include <Arduino.h>

// ==============================================================================
// CẤU HÌNH HỆ THỐNG & PHẦN CỨNG (ESP32 GreenHouse Kit)
// ==============================================================================

// Cấu hình mạng Wi-Fi
#ifndef WIFI_SSID
#define WIFI_SSID             "FPT Telecom"
#endif

#ifndef WIFI_PASSWORD
#define WIFI_PASSWORD         "diep1976"
#endif

// Cấu hình MQTT Broker (Địa chỉ IPv4 của máy tính chạy Broker trên mạng FPT Telecom)
#define MQTT_BROKER_IP        "192.168.1.8"
#define MQTT_BROKER_PORT      1883
#define MQTT_USER             "iot_user"
#define MQTT_PASS             "iot_password123"

// Quy chuẩn Topic & Node
#define TOPIC_TELEMETRY       "nhom01/node01/telemetry"
#define DEVICE_ID             "node01"
#define GROUP_ID              "nhom01"

// Cấu hình chân phần cứng (Cấu hình độc lập ESP32 + DHT11)
#define SENSOR_FROM_I2C       0       // 0: Đọc DHT11 trực tiếp (chỉ cần ESP32 + DHT11)
#define PIN_DHT               14      // Chân DATA của DHT11 nối với GPIO14 (chân D14 trên ESP32)
#define PIN_SDA               33      // I2C SDA (nếu dùng bo mở rộng)
#define PIN_SCL               32      // I2C SCL
#define I2C_SLAVE_ADDR        0x08    // Địa chỉ I2C Slave

#define HAS_RAIN_SENSOR       0       // 0: Không có cảm biến mưa rời -> Mặc định trời khô (rain = 0)
#define PIN_RAIN              34      // Cảm biến mưa (nếu có gắn thêm)

#define BUTTON_PIN            0       // Dùng ngay nút BOOT (GPIO0) trên bo ESP32 làm nút an toàn / khẩn cấp!
#define LOAD_PIN              5       // Xuất xung PWM mô phỏng bơm (GPIO5)
#define DIR_PIN               17      // Chân hướng giữ LOW
#define LED_RED_PIN           26      // LED đỏ ngoài (nếu có)
#define LED_GREEN_PIN         27      // LED xanh ngoài (nếu có)
#define LED_ONBOARD_PIN       2       // Đèn LED tích hợp sẵn trên bo ESP32 (GPIO2) báo trạng thái RUNNING

static const int UNUSED_PINS[] = {23, 22, 12, 13}; // MOTOR2, ARRAY_LED ép LOW

// Ngưỡng cảm biến DHT11 hợp lệ
#define TEMP_MIN              0.0f
#define TEMP_MAX              50.0f
#define HUM_MIN               20.0f
#define HUM_MAX               90.0f

// Chu kỳ và giới hạn thời gian (ms)
#define MAX_RUN_MS            10000UL // Thời gian bơm chạy tối đa (10s) -> tự ngắt
#define ERROR_HOLD_MS         3000UL  // Thời gian giữ trạng thái LỖI trước khi về DỪNG
#define SENSOR_INTERVAL_MS    2000UL  // Chu kỳ đọc cảm biến và tạo bản ghi (2s)
#define SAFETY_LOOP_DELAY_MS  50UL    // Chu kỳ tick của Task an toàn (Core 1)
#define MQTT_RECONNECT_MS     3000UL  // Chu kỳ thử kết nối lại MQTT khi mất kết nối
#define HEARTBEAT_INTERVAL_MS 2000UL  // Chu kỳ in heartbeat kiểm chứng Core 1 sống

// Kích thước Queue FreeRTOS
#define TELEMETRY_QUEUE_LEN   10      // Kích thước cố định 10 bản ghi theo đề bài

// Cấu hình PWM cho máy bơm
#define PWM_FREQ_HZ           1000
#define PWM_RES_BITS          8
#define PWM_CHANNEL           0
#define DEFAULT_DUTY_PCT      100

// ==============================================================================
// ĐỊNH NGHĨA KIỂU DỮ LIỆU & STRUCT
// ==============================================================================
enum FsmState {
    ST_STOPPED = 0,
    ST_RUNNING = 1,
    ST_ERROR   = 2
};

// Cấu trúc một bản ghi dữ liệu gửi qua FreeRTOS Queue
typedef struct {
    uint32_t seq_id;          // Số thứ tự tăng dần để kiểm đếm rớt gói
    uint32_t timestamp;       // Timestamp (epoch hoặc millis)
    float    temp;            // Nhiệt độ (°C) - NaN nếu lỗi
    float    hum;             // Độ ẩm (%) - NaN nếu lỗi
    int      rain;            // 0: Khô, 1: Có mưa, -1: Lỗi (NaN/Disconnect)
    bool     valid;           // true nếu tất cả cảm biến hợp lệ
    uint8_t  fsm_state;       // 0: STOPPED, 1: RUNNING, 2: ERROR
    uint8_t  pump_duty;       // 0 - 100%
} TelemetryData;
