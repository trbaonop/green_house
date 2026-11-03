// ==============================================================================
// CSE3069 · PHÁT TRIỂN ỨNG DỤNG IOT — BÀI THỰC HÀNH 04 / 10
// Đề tài: Gửi dữ liệu qua MQTT - FreeRTOS Đa Tác Vụ & Queue Giới Hạn (Non-blocking)
// Tích hợp: WebServer hiển thị JSON trực tiếp trên trình duyệt Web (Port 80)
// ==============================================================================

#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <Wire.h>
#include <DHT.h>
#include "esp_timer.h"
#include "../include/config.h"

// ------------------------------------------------------------------------------
// ĐỐI TƯỢNG TOÀN CỤC & TÀI NGUYÊN FREERTOS
// ------------------------------------------------------------------------------
WiFiClient espClient;
PubSubClient mqttClient(espClient);
WebServer server(80);

#if !SENSOR_FROM_I2C
DHT dht(PIN_DHT, DHT11);
#endif

// Hàng đợi FreeRTOS trao đổi dữ liệu giữa Task An toàn và Task MQTT
QueueHandle_t xTelemetryQueue = NULL;
TaskHandle_t  xTaskSafetyHandle = NULL;
TaskHandle_t  xTaskMQTTHandle = NULL;

// Trạng thái FSM máy bơm & cảm biến
FsmState currentState = ST_STOPPED;
uint8_t  currentDuty  = DEFAULT_DUTY_PCT;
uint32_t runStartMs   = 0;
uint32_t errorStartMs = 0;
uint8_t  simMode      = 0;  // 0: binh thuong, 1: mat tin hieu (NaN), 2: ngoai dai

// Giả lập mạng chậm gây nghẽn Queue (ms)
uint32_t simulateSlowMqttMs = 0;

// Thống kê hệ thống
volatile uint32_t statSeqCounter      = 0;
volatile uint32_t statQueuePushed     = 0;
volatile uint32_t statQueueDropped    = 0;
volatile uint32_t statMqttPublished   = 0;
volatile uint32_t statBtnInterrupts   = 0;

// Chuỗi JSON mới nhất để phục vụ Web API
String latestJsonPayload = "{\"status\":\"waiting_data\"}";

// Biến ISR nút nhấn
volatile bool     btnPressedFlag = false;
volatile int64_t  lastBtnUs      = -1000000LL;
const uint32_t    DEBOUNCE_MS    = 50;

// ------------------------------------------------------------------------------
// ISR NÚT NHẤN AN TOÀN
// ------------------------------------------------------------------------------
void IRAM_ATTR onButtonISR() {
    statBtnInterrupts++;
    int64_t now = esp_timer_get_time();
    if (now - lastBtnUs >= (int64_t)DEBOUNCE_MS * 1000LL) {
        lastBtnUs = now;
        btnPressedFlag = true;
    }
}

// ------------------------------------------------------------------------------
// ĐIỀU KHIỂN PHẦN CỨNG BƠM & LED (PWM)
// ------------------------------------------------------------------------------
void pwmInit() {
#if defined(ESP_ARDUINO_VERSION_MAJOR) && (ESP_ARDUINO_VERSION_MAJOR >= 3)
    ledcAttach(LOAD_PIN, PWM_FREQ_HZ, PWM_RES_BITS);
#else
    ledcSetup(PWM_CHANNEL, PWM_FREQ_HZ, PWM_RES_BITS);
    ledcAttachPin(LOAD_PIN, PWM_CHANNEL);
#endif
}

void pwmWriteRaw(uint32_t raw) {
#if defined(ESP_ARDUINO_VERSION_MAJOR) && (ESP_ARDUINO_VERSION_MAJOR >= 3)
    ledcWrite(LOAD_PIN, raw);
#else
    ledcWrite(PWM_CHANNEL, raw);
#endif
}

void applyLoadState() {
    uint32_t maxRaw = (1UL << PWM_RES_BITS) - 1;
    uint32_t raw = (currentState == ST_RUNNING) ? (currentDuty * maxRaw + 50) / 100 : 0;
    pwmWriteRaw(raw);
    digitalWrite(LED_GREEN_PIN, currentState == ST_RUNNING ? HIGH : LOW);
    digitalWrite(LED_RED_PIN,   currentState == ST_ERROR   ? HIGH : LOW);
}

void fsmStartRun() {
    currentState = ST_RUNNING;
    runStartMs = millis();
    applyLoadState();
    Serial.printf("[FSM] => CHAY (RUNNING) | Duty: %u%% | Max: %lu ms\n", currentDuty, MAX_RUN_MS);
}

void fsmStopRun(const char* reason) {
    currentState = ST_STOPPED;
    applyLoadState();
    Serial.printf("[FSM] => DUNG (STOPPED) | Ly do: %s\n", reason);
}

void fsmEnterError(const char* reason) {
    currentState = ST_ERROR;
    errorStartMs = millis();
    applyLoadState(); // Tat bom ngay lap tuc
    Serial.printf("[FSM] => LOI (ERROR) | Ly do: %s | Giu %lu ms\n", reason, ERROR_HOLD_MS);
}

// ------------------------------------------------------------------------------
// ĐỌC CẢM BIẾN (DHT11 & MƯA)
// ------------------------------------------------------------------------------
bool readGreenhouseSensors(float &temp, float &hum, int &rain) {
    bool ok = false;
#if SENSOR_FROM_I2C
    uint8_t n = Wire.requestFrom(I2C_SLAVE_ADDR, (uint8_t)4);
    if (n >= 4) {
        uint8_t b[4];
        for (int i = 0; i < 4; i++) b[i] = Wire.read();
        uint16_t rt = (b[0] << 8) | b[1];
        uint16_t rh = (b[2] << 8) | b[3];
        if (rt == 0xFFFF || rh == 0xFFFF) {
            temp = NAN; hum = NAN; ok = false;
        } else {
            temp = rt / 10.0f; hum = rh / 10.0f; ok = true;
        }
    } else {
        while (Wire.available()) Wire.read();
        temp = NAN; hum = NAN; ok = false;
    }
#else
    hum = dht.readHumidity();
    temp = dht.readTemperature();
    ok = !(isnan(hum) || isnan(temp));
#endif

    // Đọc cảm biến mưa (GPIO34)
    int rawRain = digitalRead(PIN_RAIN);
    rain = (rawRain == LOW) ? 1 : 0;

    // Chế độ giả lập lỗi theo lệnh SIM
    if (simMode == 1) { // Giả lập mất tín hiệu cảm biến (NaN)
        temp = NAN; hum = NAN; rain = -1;
        return false;
    }
    if (simMode == 2) { // Giả lập ngoài dải an toàn
        temp = 88.5f; hum = 5.0f; rain = 0;
        return true;
    }

    return ok;
}

// ------------------------------------------------------------------------------
// TASK 1 (CORE 1): VÒNG ĐIỀU KHIỂN AN TOÀN FSM & ĐỌC CẢM BIẾN
// ------------------------------------------------------------------------------
void vTaskSafetyFSM(void *pvParameters) {
    Serial.println("[CORE 1] Task Safety FSM khoi tao thanh cong.");
    uint32_t lastSensorRead = 0;
    uint32_t lastHeartbeat  = 0;

    for (;;) {
        uint32_t now = millis();

        // 1. Xử lý nút nhấn khẩn cấp / chuyển chế độ
        if (btnPressedFlag) {
            btnPressedFlag = false;
            if (currentState == ST_STOPPED) {
                fsmStartRun();
            } else if (currentState == ST_RUNNING) {
                fsmStopRun("Nut nhan khan cap");
            }
        }

        // 2. Kiểm tra giới hạn thời gian chạy tối đa (MAX_RUN_MS) của bơm
        if (currentState == ST_RUNNING && (now - runStartMs >= MAX_RUN_MS)) {
            fsmStopRun("Qua thoi gian chay toi da (MAX_RUN_MS)");
        }

        // 3. Kiểm tra tự phục hồi từ trạng thái LỖI về DỪNG sau ERROR_HOLD_MS
        if (currentState == ST_ERROR && (now - errorStartMs >= ERROR_HOLD_MS)) {
            fsmStopRun("Het thoi gian giu LOI -> Phuc hoi ve DUNG");
        }

        // 4. Chu kỳ đọc cảm biến và đẩy bản ghi vào Queue (SENSOR_INTERVAL_MS)
        if (now - lastSensorRead >= SENSOR_INTERVAL_MS) {
            lastSensorRead = now;

            float t = NAN, h = NAN;
            int r = -1;
            bool sensorOk = readGreenhouseSensors(t, h, r);
            bool inRange = sensorOk && (t >= TEMP_MIN && t <= TEMP_MAX && h >= HUM_MIN && h <= HUM_MAX);
            bool isValid = inRange && (r >= 0);

            // Nếu đang CHẠY mà phát hiện lỗi cảm biến -> Chuyển ngay sang LỖI
            if (!isValid && currentState == ST_RUNNING) {
                fsmEnterError(isnan(t) ? "Cam bien mat tin hieu (NaN)" : "So do ngoai dai an toan");
            }

            // Đóng gói bản ghi TelemetryData
            TelemetryData record;
            record.seq_id    = ++statSeqCounter;
            record.timestamp = (uint32_t)(now / 1000);
            record.temp      = isValid ? t : NAN;
            record.hum       = isValid ? h : NAN;
            record.rain      = r;
            record.valid     = isValid;
            record.fsm_state = (uint8_t)currentState;
            record.pump_duty = (currentState == ST_RUNNING) ? currentDuty : 0;

            // Đẩy vào Queue với timeout = 0 (tuyệt đối không block task an toàn!)
            if (xQueueSend(xTelemetryQueue, &record, 0) != pdPASS) {
                // CHÍNH SÁCH KHI ĐẦY: DROP OLDEST (Bỏ bản ghi cũ nhất)
                TelemetryData discarded;
                if (xQueueReceive(xTelemetryQueue, &discarded, 0) == pdPASS) {
                    statQueueDropped++;
                    Serial.printf("[CORE 1:QUEUE] FULL (10/10)! Bo ban ghi cu seq=%u, chen seq=%u | Tong dropped: %u\n",
                                  discarded.seq_id, record.seq_id, statQueueDropped);
                }
                xQueueSend(xTelemetryQueue, &record, 0);
            } else {
                statQueuePushed++;
            }
        }

        // 5. Heartbeat định kỳ chứng minh Task an toàn liên tục hoạt động bình thường
        if (now - lastHeartbeat >= HEARTBEAT_INTERVAL_MS) {
            lastHeartbeat = now;
            UBaseType_t qCount = uxQueueMessagesWaiting(xTelemetryQueue);
            const char* stateNames[] = {"DUNG", "CHAY", "LOI"};
            Serial.printf("[HEARTBEAT Core 1] Uptime: %lus | FSM: %s | Bom: %u%% | Queue: %u/10 | Pushed: %u | Dropped: %u\n",
                          now / 1000, stateNames[currentState],
                          (currentState == ST_RUNNING ? currentDuty : 0),
                          (unsigned int)qCount, statQueuePushed, statQueueDropped);
        }

        vTaskDelay(pdMS_TO_TICKS(SAFETY_LOOP_DELAY_MS));
    }
}

// ------------------------------------------------------------------------------
// TRANG WEB & WEB API (PORT 80)
// ------------------------------------------------------------------------------
const char INDEX_HTML[] PROGMEM = R"rawliteral(
<!DOCTYPE html><html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ESP32 MQTT & Telemetry Dashboard</title>
<style>
:root{--bg:#f8fafc;--card:#ffffff;--tx:#1e293b;--mut:#64748b;--bd:#e2e8f0;--pri:#0284c7;--ok:#16a34a;--err:#dc2626}
body{font-family:system-ui,-apple-system,sans-serif;margin:0;padding:20px;background:var(--bg);color:var(--tx)}
.wrap{max-width:800px;margin:auto}
.card{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:16px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,0.05)}
h1{font-size:22px;margin:0 0 12px;color:var(--pri)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px}
.lbl{font-size:12px;color:var(--mut);text-transform:uppercase;font-weight:600}
.val{font-size:26px;font-weight:700;margin-top:4px}
pre{background:#0f172a;color:#38bdf8;padding:14px;border-radius:8px;font-size:13px;overflow-x:auto;white-space:pre-wrap}
.badge{display:inline-block;padding:4px 10px;border-radius:9999px;font-size:12px;font-weight:700}
.badge-ok{background:#dcfce7;color:var(--ok)}
.badge-err{background:#fee2e2;color:var(--err)}
</style></head><body><div class="wrap">
<h1>ESP32 Nhà Kính: Telemetry & MQTT Monitor</h1>
<div class="card grid">
  <div><div class="lbl">Nhiệt độ</div><div class="val" id="t">-- °C</div></div>
  <div><div class="lbl">Độ ẩm</div><div class="val" id="h">-- %</div></div>
  <div><div class="lbl">Cảm biến mưa</div><div class="val" id="rain">--</div></div>
  <div><div class="lbl">Máy bơm</div><div class="val" id="pump">--</div></div>
</div>
<div class="card">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
    <b>Bản tin JSON Telemetry hiện hành (Topic: nhom01/node01/telemetry)</b>
    <span id="badge" class="badge badge-ok">Đang nhận dữ liệu</span>
  </div>
  <pre id="jsonViewer">Đang tải dữ liệu JSON...</pre>
  <div style="font-size:12px;color:var(--mut);margin-top:8px">
    API đường dẫn thô: <a href="/api/status" target="_blank" style="color:var(--pri)">/api/status</a> hoặc <a href="/api/telemetry" target="_blank" style="color:var(--pri)">/api/telemetry</a>
  </div>
</div>
</div>
<script>
async function update(){
  try{
    const res = await fetch('/api/status');
    const data = await res.json();
    document.getElementById('jsonViewer').textContent = JSON.stringify(data, null, 2);
    document.getElementById('t').textContent = data.temp !== null ? data.temp + ' °C' : 'LỖI (null)';
    document.getElementById('h').textContent = data.hum !== null ? data.hum + ' %' : 'LỖI (null)';
    document.getElementById('rain').textContent = data.rain === 1 ? 'CÓ MƯA 🌧️' : (data.rain === 0 ? 'KHÔ RÁO ☀️' : 'LỖI');
    document.getElementById('pump').textContent = data.pump + '% (' + data.fsm_state + ')';
    const badge = document.getElementById('badge');
    if(data.valid){
      badge.className = 'badge badge-ok';
      badge.textContent = 'Dữ liệu hợp lệ (valid=true)';
    }else{
      badge.className = 'badge badge-err';
      badge.textContent = 'Cảnh báo lỗi (valid=false)';
    }
  }catch(e){
    document.getElementById('jsonViewer').textContent = 'Mất kết nối tới ESP32...';
  }
}
setInterval(update, 1500);
update();
</script></body></html>
)rawliteral";

void setupWebServer() {
    server.on("/", []() {
        server.send_P(200, "text/html; charset=utf-8", INDEX_HTML);
    });

    server.on("/api/status", []() {
        server.sendHeader("Access-Control-Allow-Origin", "*");
        server.send(200, "application/json", latestJsonPayload);
    });

    server.on("/api/telemetry", []() {
        server.sendHeader("Access-Control-Allow-Origin", "*");
        server.send(200, "application/json", latestJsonPayload);
    });

    server.begin();
    Serial.println("[WEB] Web Server khoi dong tai cong 80!");
    Serial.printf("[WEB] Mo trinh duyet truy cap: http://%s/\n", WiFi.localIP().toString().c_str());
    Serial.printf("[WEB] API xem truc tiep JSON: http://%s/api/status\n", WiFi.localIP().toString().c_str());
}

// ------------------------------------------------------------------------------
// TASK 2 (CORE 0): QUẢN LÝ WI-FI, KẾT NỐI MQTT BROKER & PUBLISH DỮ LIỆU
// ------------------------------------------------------------------------------
void maintainWiFi() {
    if (WiFi.status() == WL_CONNECTED) return;

    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    Serial.printf("[CORE 0:WIFI] Dang ket noi toi SSID: %s...\n", WIFI_SSID);

    uint32_t startAttempt = millis();
    while (WiFi.status() != WL_CONNECTED && (millis() - startAttempt < 8000)) {
        vTaskDelay(pdMS_TO_TICKS(300));
        Serial.print(".");
    }
    Serial.println();

    if (WiFi.status() == WL_CONNECTED) {
        Serial.printf("[CORE 0:WIFI] Ket noi thanh cong! IP: %s\n", WiFi.localIP().toString().c_str());
        setupWebServer();
    } else {
        Serial.println("[CORE 0:WIFI] Ket noi that bai, se thu lai o chu ky sau.");
    }
}

void maintainMQTT() {
    if (mqttClient.connected()) return;
    if (WiFi.status() != WL_CONNECTED) return;

    static uint32_t lastReconnectAttempt = 0;
    uint32_t now = millis();

    if (now - lastReconnectAttempt >= MQTT_RECONNECT_MS) {
        lastReconnectAttempt = now;
        Serial.printf("[CORE 0:MQTT] Dang thu ket noi toi Broker %s:%d...\n", MQTT_BROKER_IP, MQTT_BROKER_PORT);

        String clientId = "ESP32_Greenhouse_" + String(DEVICE_ID) + "_" + String((uint32_t)ESP.getEfuseMac(), HEX);
        if (mqttClient.connect(clientId.c_str(), MQTT_USER, MQTT_PASS)) {
            Serial.println("[CORE 0:MQTT] => KET NOI BROKER THANH CONG!");
        } else {
            Serial.printf("[CORE 0:MQTT] Ket noi that bai, rc=%d. Thu lai sau %lu ms (Non-blocking)\n",
                          mqttClient.state(), MQTT_RECONNECT_MS);
        }
    }
}

void vTaskMQTT(void *pvParameters) {
    Serial.println("[CORE 0] Task MQTT khoi tao thanh cong.");
    mqttClient.setServer(MQTT_BROKER_IP, MQTT_BROKER_PORT);
    mqttClient.setBufferSize(512);

    for (;;) {
        // Duy trì mạng (non-blocking)
        maintainWiFi();
        maintainMQTT();

        if (mqttClient.connected()) {
            mqttClient.loop();
        }

        // Lấy bản ghi từ Queue (chờ tối đa 100ms)
        TelemetryData record;
        if (xQueueReceive(xTelemetryQueue, &record, pdMS_TO_TICKS(100)) == pdPASS) {
            
            // THỬ NGHIỆM NGHẼN MẠNG: Nếu bật simulateSlowMqttMs, cố tình sleep task MQTT
            if (simulateSlowMqttMs > 0) {
                Serial.printf("[CORE 0:MQTT] [SIM SLOW] Dang tri hoan %u ms de gay nghen Queue...\n", simulateSlowMqttMs);
                vTaskDelay(pdMS_TO_TICKS(simulateSlowMqttMs));
            }

            // Tạo JSON payload chuẩn theo tài liệu topic_payload.md
            JsonDocument doc;
            doc["device_id"] = DEVICE_ID;
            doc["ts"]        = record.timestamp;
            doc["seq"]       = record.seq_id;

            // Phân biệt NULL và 0
            if (isnan(record.temp) || !record.valid) {
                doc["temp"] = nullptr;
            } else {
                doc["temp"] = serialized(String(record.temp, 1));
            }

            if (isnan(record.hum) || !record.valid) {
                doc["hum"] = nullptr;
            } else {
                doc["hum"] = serialized(String(record.hum, 1));
            }

            if (record.rain < 0) {
                doc["rain"] = nullptr;
            } else {
                doc["rain"] = record.rain;
            }

            JsonObject unit = doc["unit"].to<JsonObject>();
            unit["temp"] = "C";
            unit["hum"]  = "%";
            unit["rain"] = "binary";

            doc["valid"] = record.valid;

            const char* stateNames[] = {"STOPPED", "RUNNING", "ERROR"};
            doc["fsm_state"] = stateNames[record.fsm_state];
            doc["pump"]      = record.pump_duty;

            char jsonBuf[384];
            serializeJson(doc, jsonBuf, sizeof(jsonBuf));

            // Cập nhật biến toàn cục để Web API hiển thị ngay
            latestJsonPayload = String(jsonBuf);

            if (mqttClient.connected()) {
                bool published = mqttClient.publish(TOPIC_TELEMETRY, jsonBuf);
                if (published) {
                    statMqttPublished++;
                    Serial.printf("[CORE 0:MQTT PUB] Seq=%u -> Topic: %s | Payload: %s\n",
                                  record.seq_id, TOPIC_TELEMETRY, jsonBuf);
                } else {
                    Serial.printf("[CORE 0:MQTT ERR] Publish that bai cho seq=%u\n", record.seq_id);
                }
            } else {
                Serial.printf("[CORE 0:MQTT] Mat ket noi broker, tam giu seq=%u\n", record.seq_id);
            }
        }

        vTaskDelay(pdMS_TO_TICKS(20));
    }
}

// ------------------------------------------------------------------------------
// XỬ LÝ LỆNH TỪ SERIAL CONSOLE (ĐIỀU KHIỂN & THỰC NGHIỆM)
// ------------------------------------------------------------------------------
void handleSerialCmd(String cmd) {
    cmd.trim();
    if (cmd.length() == 0) return;
    String upper = cmd;
    upper.toUpperCase();

    if (upper == "ON") {
        if (currentState == ST_STOPPED) fsmStartRun();
        else Serial.println("Khong the bat vi khong o trang thai DUNG");
    } else if (upper == "OFF") {
        if (currentState == ST_RUNNING) fsmStopRun("Lenh Serial OFF");
        else Serial.println("Khong dang CHAY");
    } else if (upper.startsWith("DUTY")) {
        int v = upper.substring(4).toInt();
        if (v >= 0 && v <= 100) {
            currentDuty = (uint8_t)v;
            applyLoadState();
            Serial.printf("Dat duty = %u%%\n", currentDuty);
        } else {
            Serial.println("Duty khong hop le (0..100)");
        }
    } else if (upper.startsWith("SIM")) {
        simMode = (uint8_t)upper.substring(3).toInt();
        Serial.printf("[SIM] Dat che do gia lap cam bien = %u\n", simMode);
    } else if (upper.startsWith("SLOW")) {
        simulateSlowMqttMs = upper.substring(4).toInt();
        Serial.printf("[TEST] Dat do tre Task MQTT = %u ms (Thu nghiem nghen Queue)\n", simulateSlowMqttMs);
    } else if (upper == "STATS") {
        Serial.println("================= THONG KE HE THONG =================");
        Serial.printf("Tong ban ghi sinh ra (Seq)  : %u\n", statSeqCounter);
        Serial.printf("Ban ghi nap vao Queue        : %u\n", statQueuePushed);
        Serial.printf("Ban ghi bi DROP khi Queue day: %u (Chinh sach: Drop Oldest)\n", statQueueDropped);
        Serial.printf("Ban ghi publish thanh cong   : %u\n", statMqttPublished);
        Serial.printf("So lan ngat nut nhan ISR     : %u\n", statBtnInterrupts);
        Serial.printf("Trang thai Queue hien tai    : %u / %d\n",
                      (unsigned int)uxQueueMessagesWaiting(xTelemetryQueue), TELEMETRY_QUEUE_LEN);
        Serial.printf("Trang thai Broker MQTT       : %s\n", mqttClient.connected() ? "CONNECTED" : "DISCONNECTED");
        Serial.println("======================================================");
    } else {
        Serial.printf("Lenh khong ro: '%s'. Cac lenh ho tro: ON, OFF, DUTY <0-100>, SIM <0-2>, SLOW <ms>, STATS\n", cmd.c_str());
    }
}

// ------------------------------------------------------------------------------
// ARDUINO SETUP & LOOP
// ------------------------------------------------------------------------------
void setup() {
    Serial.begin(115200);
    delay(500);
    Serial.println("\n\n=== CSE3069: BAI THUC HANH 04 - MQTT & FREERTOS ===");

    // Cấu hình chân tải và đèn
    for (int p : UNUSED_PINS) { pinMode(p, OUTPUT); digitalWrite(p, LOW); }
    if (DIR_PIN >= 0) { pinMode(DIR_PIN, OUTPUT); digitalWrite(DIR_PIN, LOW); }
    pinMode(LED_RED_PIN, OUTPUT);
    pinMode(LED_GREEN_PIN, OUTPUT);
    pinMode(PIN_RAIN, INPUT);

    pwmInit();
    applyLoadState();

    // Cấu hình cảm biến
#if SENSOR_FROM_I2C
    Wire.begin(PIN_SDA, PIN_SCL);
#else
    dht.begin();
#endif

    // Cấu hình ngắt nút nhấn an toàn
    pinMode(BUTTON_PIN, INPUT);
    attachInterrupt(digitalPinToInterrupt(BUTTON_PIN), onButtonISR, FALLING);

    // Tạo FreeRTOS Queue có giới hạn cố định 10 phần tử
    xTelemetryQueue = xQueueCreate(TELEMETRY_QUEUE_LEN, sizeof(TelemetryData));
    if (xTelemetryQueue == NULL) {
        Serial.println("[ERROR] Khong the khoi tao FreeRTOS Queue!");
        while (1) delay(1000);
    }
    Serial.printf("[INIT] FreeRTOS Queue khoi tao voi kich thuoc %d phan tu.\n", TELEMETRY_QUEUE_LEN);

    // Khởi tạo Task Safety FSM trên Core 1 (Ưu tiên cao hơn: Priority 2)
    xTaskCreatePinnedToCore(
        vTaskSafetyFSM,
        "SafetyFSM_Task",
        4096,
        NULL,
        2,
        &xTaskSafetyHandle,
        1
    );

    // Khởi tạo Task MQTT & Network trên Core 0 (Priority 1)
    xTaskCreatePinnedToCore(
        vTaskMQTT,
        "MQTT_Task",
        4096,
        NULL,
        1,
        &xTaskMQTTHandle,
        0
    );

    Serial.println("[INIT] He thong san sang. Nhap lenh ON, OFF, STATS, SLOW <ms> de dieu khien.");
}

void loop() {
    // Phục vụ các yêu cầu HTTP từ trình duyệt Web (Port 80)
    server.handleClient();

    // Đọc Serial Console trong loop() thông thường
    while (Serial.available()) {
        String line = Serial.readStringUntil('\n');
        handleSerialCmd(line);
    }
    vTaskDelay(pdMS_TO_TICKS(50));
}
