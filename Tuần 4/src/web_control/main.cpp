// ============================================================================
// control.ino - Điều khiển MÁY BƠM (MOTOR1) bằng PWM, máy trạng thái 3 trạng thái
//               DỪNG / CHẠY / LỖI, có web hiển thị trạng thái + giả lập lỗi.
// Board nhà kính ESP32. Chạy được trên ESP32 Arduino core 2.x và 3.x.
//
// SƠ ĐỒ ĐẤU NỐI (theo sơ đồ nhà kính)
//   Bơm   : MOTOR1_INT1 = GPIO5  -> PWM (chân tải duy nhất)
//           MOTOR1_INT2 = GPIO17 -> giữ LOW (chân hướng) -> IC A4950 (U9) -> bơm
//   I2C   : SDA = GPIO33, SCL = GPIO32; Arduino/ATmega8 là slave 0x08 giữ DHT11
//           (SENSOR_FROM_I2C = 0 thì đọc trực tiếp DHT11 ở GPIO14)
//   Nút   : GPIO36 (chỉ vào, KHÔNG có pull-up nội -> cần pull-up ngoài trên board)
//   LED   : LED_G = GPIO27 (sáng khi CHẠY), LED_R = GPIO26 (sáng khi LỖI)
//   Các chân tải khác (MOTOR2 = 23, 22; ARRAY_LED = 12, 13) ép LOW lúc khởi động
//
// DANH SÁCH LỆNH SERIAL (115200 baud, kết thúc bằng Enter) - cũng dùng được từ web
//   ON        : DỪNG -> CHẠY
//   OFF       : CHẠY -> DỪNG
//   DUTY <n>  : đặt duty 0..100 (%), áp dụng ngay nếu đang CHẠY (mặc định 100)
//   STATS     : in số lần ngắt nút, số tick, chu kỳ trung bình, độ lệch
//   STATUS    : in trạng thái, duty, nhiệt độ, độ ẩm
//   Lệnh sai khi đang CHẠY -> LỖI -> giữ ERROR_HOLD_MS rồi về DỪNG
//
// WEB (http://<IP in ra Serial>/)
//   /api/status        : JSON trạng thái
//   /api/cmd?c=<lệnh>  : gửi lệnh như Serial (ON, OFF, DUTY 50, hoặc lệnh sai)
//   /api/sim?mode=0..2 : giả lập lỗi cảm biến: 0 tắt, 1 mất tín hiệu, 2 ngoài dải
//
// Thư viện: "DHT sensor library" (Adafruit) + "Adafruit Unified Sensor"
// ============================================================================

#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <Wire.h>
#include <DHT.h>
#include <stdarg.h>
#include "esp_timer.h"

// ---------------------------- CẤU HÌNH --------------------------------------
#define SENSOR_FROM_I2C   1          // 1 = đọc qua I2C từ Arduino; 0 = đọc DHT11 ở GPIO14

const char*    WIFI_SSID        = "VJU Office";
const char*    WIFI_PASS        = "VJuOffice@2023";
const char*    AP_SSID          = "ESP32-trbao";   // AP dự phòng nếu không vào được WiFi
const char*    AP_PASS          = "12345678";

const int      PIN_DHT          = 14;
const int      PIN_SDA          = 33;
const int      PIN_SCL          = 32;
const int      BUTTON_PIN       = 36;   // cần pull-up ngoài
const int      LOAD_PIN         = 5;    // MOTOR1_INT1: chân PWM của bơm
const int      DIR_PIN          = 17;   // MOTOR1_INT2: giữ LOW (-1 nếu không dùng)
const int      LED_RED_PIN      = 26;   // LED_R: báo LỖI
const int      LED_GREEN_PIN    = 27;   // LED_G: báo CHẠY
static const int OFF_PINS[]     = {23, 22, 12, 13};  // MOTOR2, ARRAY_LED: ép LOW

const uint8_t  I2C_SLAVE_ADDR   = 0x08;

const uint32_t MAX_RUN_MS       = 10000UL; 
const uint32_t ERROR_HOLD_MS    = 3000UL;   // thời gian giữ LỖI
const uint32_t DEBOUNCE_MS      = 50;       // chống rung nút (thí nghiệm: 0 và 50)
const uint32_t SENSOR_READ_MS   = 2000UL;   // chu kỳ đọc cảm biến

const float    TEMP_MIN = 0,  TEMP_MAX = 50;   // dải hợp lệ của DHT11
const float    HUM_MIN  = 20, HUM_MAX  = 90;

const uint32_t PWM_FREQ_HZ      = 1000;     // 1 kHz
const uint8_t  PWM_RES_BITS     = 8;        // 8 bit (0..255)
const uint8_t  PWM_CHANNEL      = 0;        // chỉ dùng cho core 2.x
const uint8_t  DEFAULT_DUTY_PCT = 100;

const uint64_t TIMER_PERIOD_US  = 1000000ULL; // chu kỳ timer phần cứng: 1 giây
// -----------------------------------------------------------------------------

#if defined(ESP_ARDUINO_VERSION_MAJOR) && (ESP_ARDUINO_VERSION_MAJOR >= 3)
  #define CORE_3X 1
#else
  #define CORE_3X 0
#endif

// ---------------------------- Trạng thái ------------------------------------
enum State { ST_STOPPED, ST_RUNNING, ST_ERROR };
State    state        = ST_STOPPED;
uint8_t  dutyPct      = DEFAULT_DUTY_PCT;
uint32_t runStartMs   = 0;
uint32_t errorStartMs = 0;
uint32_t lastSensorMs = 0;
float    lastTemp = NAN, lastHum = NAN;
uint8_t  sensorErr = 0;        // 0 = tốt, 1 = mất tín hiệu, 2 = ngoài dải
uint8_t  prevSensorErr = 0;
uint8_t  simMode = 0;          // 0 = tắt, 1 = mất tín hiệu, 2 = ngoài dải
char     lastReason[56] = "";  // lý do LỖI gần nhất (hiện trên web)

#if !SENSOR_FROM_I2C
DHT dht(PIN_DHT, DHT11);
#endif
WebServer server(80);

// ---------------------------- Nhật ký sự kiện (cho web + Serial) ------------
const int LOG_N = 10;
char logBuf[LOG_N][88];
int  logHead = 0, logCount = 0;

void logf(const char* fmt, ...) {
  char msg[64];
  va_list ap; va_start(ap, fmt); vsnprintf(msg, sizeof msg, fmt, ap); va_end(ap);
  Serial.println(msg);
  for (char* p = msg; *p; p++)                       // làm sạch để nhúng an toàn vào JSON
    if (*p == '"' || *p == '\\') *p = '\'';
    else if ((unsigned char)*p < 32) *p = ' ';
  uint32_t s = millis() / 1000;
  snprintf(logBuf[logHead], sizeof logBuf[0], "[%02lu:%02lu] %s",
           (unsigned long)(s / 60 % 100), (unsigned long)(s % 60), msg);
  logHead = (logHead + 1) % LOG_N;
  if (logCount < LOG_N) logCount++;
}

// ---------------------------- Biến dùng chung với ISR -----------------------
volatile uint32_t btnRawCount      = 0;     // tổng số lần ngắt (kể cả rung)
volatile uint32_t btnAcceptedCount = 0;     // số lần được chấp nhận sau chống rung
volatile bool     btnPressedFlag   = false; // cờ báo cho loop()
volatile int64_t  lastBtnUs        = -1000000LL;

portMUX_TYPE      timerMux     = portMUX_INITIALIZER_UNLOCKED;
volatile int64_t  lastTickUs   = 0;
volatile int64_t  sumPeriodUs  = 0;         // tổng các chu kỳ thực tế
volatile uint32_t tickCount    = 0;
volatile uint32_t periodCount  = 0;

hw_timer_t* timer = nullptr;

// ---------------------------- ISR -------------------------------------------
// ISR nút: đếm ngắt thô, lọc rung theo thời gian, đặt cờ
void IRAM_ATTR onButton() {
  btnRawCount++;
  int64_t now = esp_timer_get_time();
  if (now - lastBtnUs >= (int64_t)DEBOUNCE_MS * 1000LL) {
    lastBtnUs = now;
    btnAcceptedCount++;
    btnPressedFlag = true;
  }
}

// ISR timer: chỉ ghi timestamp, tính chu kỳ thực tế và cộng dồn
void IRAM_ATTR onTimer() {
  int64_t now = esp_timer_get_time();
  portENTER_CRITICAL_ISR(&timerMux);
  if (lastTickUs != 0) {
    sumPeriodUs += (now - lastTickUs);
    periodCount++;
  }
  lastTickUs = now;
  tickCount++;
  portEXIT_CRITICAL_ISR(&timerMux);
}

// ---------------------------- PWM (cách duy nhất bật/tắt bơm) ---------------
void pwmInit() {
#if CORE_3X
  ledcAttach(LOAD_PIN, PWM_FREQ_HZ, PWM_RES_BITS);
#else
  ledcSetup(PWM_CHANNEL, PWM_FREQ_HZ, PWM_RES_BITS);
  ledcAttachPin(LOAD_PIN, PWM_CHANNEL);
#endif
}

void pwmWriteRaw(uint32_t raw) {
#if CORE_3X
  ledcWrite(LOAD_PIN, raw);
#else
  ledcWrite(PWM_CHANNEL, raw);
#endif
}

// CHẠY -> theo duty; DỪNG/LỖI -> duty 0 (tắt). Đồng thời cập nhật LED báo.
void applyLoad() {
  uint32_t maxRaw = (1UL << PWM_RES_BITS) - 1;
  uint32_t raw = (state == ST_RUNNING) ? (dutyPct * maxRaw + 50) / 100 : 0;
  pwmWriteRaw(raw);
  digitalWrite(LED_GREEN_PIN, state == ST_RUNNING);
  digitalWrite(LED_RED_PIN,   state == ST_ERROR);
}

// ---------------------------- Chuyển trạng thái -----------------------------
void startRun() {
  state = ST_RUNNING;
  runStartMs = millis();
  applyLoad();
  logf("[CHAY] duty=%u%%", dutyPct);
}

void stopRun(const char* reason) {
  state = ST_STOPPED;
  applyLoad();
  logf("[DUNG] %s", reason);
}

void enterError(const char* reason) {
  state = ST_ERROR;
  errorStartMs = millis();
  strncpy(lastReason, reason, sizeof lastReason - 1);
  lastReason[sizeof lastReason - 1] = 0;
  applyLoad();                                  // tắt bơm ngay
  logf("[LOI] %s", reason);
}

// ---------------------------- Đọc cảm biến ----------------------------------
// Trả về true nếu đọc được; false -> t, h = NAN. Có chèn lỗi giả lập theo simMode.
bool readSensor(float &t, float &h) {
  bool ok;
#if SENSOR_FROM_I2C
  // GIẢ ĐỊNH (từ khung 01 05 02 08 trong code TH02): 4 byte = nhiệt độ*10, độ ẩm*10,
  // mỗi giá trị 16 bit big-endian; 0xFFFF = số đo không hợp lệ. Cần đối chiếu sketch Arduino.
  uint8_t n = Wire.requestFrom(I2C_SLAVE_ADDR, (uint8_t)4);
  if (n < 4) {
    while (Wire.available()) Wire.read();
    t = NAN; h = NAN; ok = false;
  } else {
    uint8_t b[4];
    for (int i = 0; i < 4; i++) b[i] = Wire.read();
    uint16_t rt = (b[0] << 8) | b[1];
    uint16_t rh = (b[2] << 8) | b[3];
    if (rt == 0xFFFF || rh == 0xFFFF) { t = NAN; h = NAN; ok = false; }
    else { t = rt / 10.0f; h = rh / 10.0f; ok = true; }
  }
#else
  h = dht.readHumidity();
  t = dht.readTemperature();
  ok = !(isnan(h) || isnan(t));
  if (!ok) { t = NAN; h = NAN; }
#endif
  if (simMode == 1) { t = NAN; h = NAN; return false; }   // giả lập mất tín hiệu
  if (simMode == 2) { t = 85; h = 5;    return true;  }   // giả lập ngoài dải
  return ok;
}

// ---------------------------- Lệnh (Serial + web) ---------------------------
// Lệnh sai: đang CHẠY thì vào LỖI, còn lại chỉ báo
void badCommand(const String& s) {
  if (state == ST_RUNNING) enterError("lenh sai khi dang CHAY");
  else logf("Lenh khong hop le: '%s'", s.c_str());
}

void printStats() {
  portENTER_CRITICAL(&timerMux);                // int64 không nguyên tử -> sao chép trong vùng tới hạn
  int64_t  sum = sumPeriodUs;
  uint32_t ticks = tickCount;
  uint32_t periods = periodCount;
  portEXIT_CRITICAL(&timerMux);

  Serial.println("---- STATS ----");
  Serial.printf("Ngat nut tho      : %lu\n", (unsigned long)btnRawCount);
  Serial.printf("Ngat nut chap nhan: %lu (DEBOUNCE_MS=%lu)\n",
                (unsigned long)btnAcceptedCount, (unsigned long)DEBOUNCE_MS);
  Serial.printf("So tick timer     : %lu\n", (unsigned long)ticks);
  if (periods == 0) {
    Serial.println("Chu ky trung binh : chua du du lieu (can >= 2 tick)");
  } else {
    double avg = (double)sum / (double)periods;
    double dev = avg - (double)TIMER_PERIOD_US;
    Serial.printf("Chu ky dat        : %llu us\n", (unsigned long long)TIMER_PERIOD_US);
    Serial.printf("Chu ky trung binh : %.2f us\n", avg);
    Serial.printf("Do lech           : %+.2f us (%+.5f %%)\n",
                  dev, dev * 100.0 / (double)TIMER_PERIOD_US);
  }
}

void printStatus() {
  const char* names[] = {"DUNG", "CHAY", "LOI"};
  Serial.printf("Trang thai: %s | duty=%u%% | T=%.1f C | H=%.1f %%\n",
                names[state], dutyPct, lastTemp, lastHum);
}

void handleLine(String s) {
  s.trim();
  s.toUpperCase();
  if (s.length() == 0) return;

  if (s == "ON") {
    if (state == ST_STOPPED)      startRun();
    else if (state == ST_RUNNING) logf("Dang CHAY roi");
    else                          logf("Dang LOI, cho ve DUNG roi thu lai");
  } else if (s == "OFF") {
    if (state == ST_RUNNING) stopRun("lenh OFF");
    else                     logf("Khong dang CHAY");
  } else if (s == "STATS") {
    printStats();
  } else if (s == "STATUS") {
    printStatus();
  } else if (s.startsWith("DUTY")) {
    // Cú pháp: DUTY <0..100>
    bool ok = (s.length() > 5 && s.charAt(4) == ' ');
    String arg = ok ? s.substring(5) : "";
    arg.trim();
    if (arg.length() == 0 || arg.length() > 3) ok = false;
    for (size_t i = 0; ok && i < arg.length(); i++)
      if (!isDigit(arg.charAt(i))) ok = false;
    int v = ok ? arg.toInt() : -1;
    if (!ok || v < 0 || v > 100) {
      badCommand(s);
    } else {
      dutyPct = (uint8_t)v;
      applyLoad();                              // áp dụng ngay nếu đang CHẠY
      logf("Duty = %u%%%s", dutyPct, state == ST_RUNNING ? " (ap dung ngay)" : " (ap dung khi CHAY)");
    }
  } else {
    badCommand(s);
  }
}

void readSerial() {
  static String buf;
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      if (buf.length()) { handleLine(buf); buf = ""; }
    } else if (buf.length() < 40) {
      buf += c;
    }
  }
}

// ---------------------------- Trang web -------------------------------------
const char INDEX_HTML[] PROGMEM = R"rawliteral(
<!DOCTYPE html><html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Điều khiển máy bơm</title>
<style>
:root{color-scheme:light;--bg:#f2f5f2;--card:#fff;--tx:#17231a;--mut:#68766b;--bd:#dce5dd;--s0:#75847a;--s1:#198754;--s2:#d94841;--shadow:0 8px 24px #17231a0a}
@media(prefers-color-scheme:dark){:root{color-scheme:dark;--bg:#111713;--card:#1b241d;--tx:#e7efe8;--mut:#a0afa3;--bd:#303d33;--s0:#77867b;--s1:#36a66b;--s2:#ed625a;--shadow:0 8px 24px #0003}}
*{box-sizing:border-box}
body{margin:0;padding:clamp(12px,3vw,28px);background:var(--bg);color:var(--tx);font:15px/1.5 system-ui,"Segoe UI",Roboto,sans-serif}
.wrap{width:min(100%,900px);margin:auto}
header{margin:0 0 18px}
h1{font-size:clamp(21px,4vw,27px);line-height:1.2;margin:0}
.subtitle{margin:5px 0 0;color:var(--mut);font-size:13px}
.card{background:var(--card);border:1px solid var(--bd);border-radius:16px;padding:clamp(14px,2.5vw,20px);margin-bottom:14px;box-shadow:var(--shadow)}
.lbl{font-size:12px;color:var(--mut)}.val{font-size:26px;font-weight:700;letter-spacing:-.03em}.u{font-size:13px;color:var(--mut);font-weight:400;letter-spacing:0}
.pills{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
.pill{padding:12px 6px;text-align:center;border-radius:11px;border:1px solid var(--bd);font-weight:700;font-size:15px;color:var(--mut);background:var(--bg);transition:background .2s,color .2s,border-color .2s}
.pill.on{color:#fff}
.pill.on.p0{background:var(--s0);border-color:var(--s0)}
.pill.on.p1{background:var(--s1);border-color:var(--s1)}
.pill.on.p2{background:var(--s2);border-color:var(--s2);animation:bl 1s infinite}
@keyframes bl{50%{opacity:.7}}
#detail{display:block;margin-top:12px}
.bar{height:7px;background:var(--bd);border-radius:8px;overflow:hidden;margin-top:12px}
.bar>div{height:100%;width:0;background:var(--s1);transition:width .9s linear}
.row{display:flex;flex-wrap:wrap;gap:10px;align-items:center}
.duty-control{display:flex;align-items:center;gap:10px;flex:1;min-width:200px}
.grid{display:grid;gap:10px;grid-template-columns:repeat(auto-fit,minmax(150px,1fr))}
.metric{padding:12px;border-radius:12px;background:var(--bg);min-width:0}
select,button,input{min-height:42px;padding:9px 13px;border-radius:10px;border:1px solid var(--bd);background:var(--bg);color:var(--tx);font:inherit}
button{cursor:pointer;font-weight:650;transition:filter .15s,transform .15s}
button:hover{filter:brightness(.96)}button:active{transform:translateY(1px)}
button:focus-visible,select:focus-visible,input:focus-visible,summary:focus-visible{outline:2px solid var(--s1);outline-offset:2px}
button.g{background:var(--s1);color:#fff;border:0}button.r{background:var(--s2);color:#fff;border:0}
input[type=range]{padding:0;flex:1;min-width:100px;accent-color:var(--s1)}
.sim{border-color:var(--s2)}
.section-title{font-size:14px;font-weight:700}
.chart-legend{display:flex;flex-wrap:wrap;gap:14px;margin-top:12px;color:var(--mut);font-size:12px}
.legend-item{display:flex;align-items:center;gap:6px}
.legend-line{width:18px;height:3px;border-radius:2px;background:var(--s1)}
.legend-line.humidity{background:#e59b28}
.chart-wrap{height:220px;margin-top:10px}
#chart{display:block;width:100%;height:100%}
pre{margin:8px 0 0;font:12px/1.55 ui-monospace,Consolas,monospace;white-space:pre-wrap;overflow-wrap:anywhere}
.json-card{padding:0;overflow:hidden}
.json-card summary{padding:16px 20px;cursor:pointer;font-size:14px;font-weight:700}
.json-card pre{max-height:320px;overflow:auto;margin:0;padding:0 20px 18px}
#msg{display:block;min-height:20px;color:var(--s2);font-weight:600}
@media(max-width:600px){
  .duty-control{flex-basis:100%}
  .sim-controls{align-items:stretch}
  .sim-controls select{flex:1;min-width:0}
  .sim-controls button{width:100%}
  .grid{grid-template-columns:repeat(2,minmax(0,1fr))}
}
@media(max-width:360px){.grid{grid-template-columns:1fr}.pill{font-size:13px}}
</style></head><body><div class="wrap">
<header><h1>Điều khiển máy bơm</h1><p class="subtitle">MOTOR1 · Bảng điều khiển ESP32</p></header>
<div class="card">
  <div class="pills" aria-label="Trạng thái máy bơm"><div class="pill p0" id="p0">DỪNG</div><div class="pill p1" id="p1">CHẠY</div><div class="pill p2" id="p2">LỖI</div></div>
  <div class="lbl" style="margin-top:10px" id="detail">--</div>
  <div class="bar"><div id="bar"></div></div>
</div>
<div class="card">
  <div class="row"><button class="g" onclick="cmd('ON')">Bật (ON)</button>
  <button onclick="cmd('OFF')">Tắt (OFF)</button>
  <label class="duty-control">Duty <input type="range" id="duty" min="0" max="100" value="100" oninput="$('dv').textContent=this.value" onchange="cmd('DUTY '+this.value)"> <b><span id="dv">100</span>%</b></label></div>
</div>
<div class="card grid">
  <div class="metric"><div class="lbl">Nhiệt độ</div><div class="val"><span id="t">--</span><span class="u"> °C</span></div></div>
  <div class="metric"><div class="lbl">Độ ẩm</div><div class="val"><span id="h">--</span><span class="u"> %</span></div></div>
  <div class="metric"><div class="lbl">Cảm biến</div><div class="val" style="font-size:15px" id="sen">--</div></div>
</div>
<div class="card">
  <div class="section-title">Biểu đồ nhiệt độ và độ ẩm</div>
  <div class="chart-wrap"><canvas id="chart" role="img" aria-label="Biểu đồ nhiệt độ và độ ẩm trong 2 phút gần nhất"></canvas></div>
  <div class="chart-legend"><span class="legend-item"><span class="legend-line"></span>Nhiệt độ (°C)</span><span class="legend-item"><span class="legend-line humidity"></span>Độ ẩm (%)</span><span id="chart-range">Đang chờ dữ liệu</span></div>
</div>
<div class="card" id="simcard">
  <div class="row sim-controls"><b>Giả lập lỗi</b>
    <select id="sim" onchange="setSim(this.value)">
      <option value="0">Tắt (đọc thật)</option>
      <option value="1">Mất tín hiệu cảm biến (NaN)</option>
      <option value="2">Số đo ngoài dải</option></select>
    <button class="r" onclick="cmd('LENH_SAI')">Gửi lệnh sai</button></div>
  <div class="lbl" style="margin-top:6px">Lỗi chỉ đưa bơm vào LỖI khi đang CHẠY; lệnh sai khi DỪNG chỉ ghi nhật ký.</div>
</div>
<div class="card grid">
  <div class="metric"><div class="lbl">Ngắt nút (thô / chấp nhận)</div><div class="val" style="font-size:18px"><span id="raw">0</span> / <span id="acc">0</span></div></div>
  <div class="metric"><div class="lbl">Tick timer</div><div class="val" style="font-size:18px" id="tk">0</div></div>
  <div class="metric"><div class="lbl">Chu kỳ TB / độ lệch (µs)</div><div class="val" style="font-size:18px"><span id="avg">--</span> / <span id="dev">--</span></div></div>
</div>
<div class="card"><div class="section-title">Nhật ký</div><pre id="log"></pre></div>
<details class="card json-card"><summary>JSON trạng thái</summary><pre id="json">Đang tải...</pre></details>
<div class="lbl" id="msg"></div>
</div>
<script>
const $=id=>document.getElementById(id);
const NAMES=['DỪNG','CHẠY','LỖI'],SENS=['Bình thường','Mất tín hiệu (NaN)','Ngoài dải'];
const chartHistory=[];
const CHART_MAX_POINTS=60,CHART_SAMPLE_MS=2000;
let polling=false,lastChartSample=0;
async function cmd(c){
  try{
    const response=await fetch('/api/cmd?c='+encodeURIComponent(c));
    if(!response.ok)throw new Error('HTTP '+response.status);
    $('msg').textContent='Đã gửi lệnh '+c;
    poll();
  }catch(e){$('msg').textContent='Không gửi được lệnh tới ESP32';}
}
async function setSim(v){
  try{
    const response=await fetch('/api/sim?mode='+v);
    if(!response.ok)throw new Error('HTTP '+response.status);
    poll();
  }catch(e){$('msg').textContent='Không gửi được chế độ giả lập tới ESP32';}
}
function render(j){
  for(let i=0;i<3;i++)$('p'+i).className='pill p'+i+(j.state===i?' on':'');
  let d='Trạng thái: '+NAMES[j.state]+' · duty '+j.duty+'%';
  if(j.state===1)d+=' · còn '+(j.left/1000).toFixed(1)+' s (tối đa '+(j.lim/1000)+' s)';
  if(j.state===2)d+=' · '+j.reason+' · về DỪNG sau '+(j.left/1000).toFixed(1)+' s';
  $('detail').textContent=d;
  $('bar').style.width=(j.state?Math.max(0,j.left/j.lim*100):0)+'%';
  $('bar').style.background=j.state===2?'var(--s2)':'var(--s1)';
  $('t').textContent=j.t==null?'--':j.t.toFixed(1);
  $('h').textContent=j.h==null?'--':j.h.toFixed(0);
  $('sen').textContent=SENS[j.sensor]+(j.sim?' (giả lập)':'');
  $('sen').style.color=j.sensor?'var(--s2)':'var(--s1)';
  if(document.activeElement!==$('sim'))$('sim').value=j.sim;
  $('simcard').className='card'+(j.sim?' sim':'');
  if(document.activeElement!==$('duty')){$('duty').value=j.duty;$('dv').textContent=j.duty;}
  $('raw').textContent=j.raw;$('acc').textContent=j.acc;$('tk').textContent=j.ticks;
  $('avg').textContent=j.avg==null?'--':j.avg.toFixed(1);
  $('dev').textContent=j.dev==null?'--':j.dev.toFixed(1);
  $('log').textContent=j.log.join('\n');
  const now=Date.now();
  if(!lastChartSample||now-lastChartSample>=CHART_SAMPLE_MS){
    chartHistory.push({time:now,t:Number.isFinite(j.t)?j.t:null,h:Number.isFinite(j.h)?j.h:null});
    if(chartHistory.length>CHART_MAX_POINTS)chartHistory.shift();
    lastChartSample=now;
    drawChart();
  }
}
function drawChart(){
  const canvas=$('chart'),rect=canvas.getBoundingClientRect();
  if(!rect.width||!rect.height)return;
  const ratio=window.devicePixelRatio||1;
  const width=Math.round(rect.width*ratio),height=Math.round(rect.height*ratio);
  if(canvas.width!==width||canvas.height!==height){canvas.width=width;canvas.height=height;}
  const ctx=canvas.getContext('2d');
  ctx.setTransform(ratio,0,0,ratio,0,0);
  const w=rect.width,h=rect.height,left=38,right=38,top=10,bottom=28;
  const plotW=w-left-right,plotH=h-top-bottom;
  const styles=getComputedStyle(document.documentElement);
  const grid=styles.getPropertyValue('--bd').trim(),muted=styles.getPropertyValue('--mut').trim();
  const tempColor=styles.getPropertyValue('--s1').trim(),humidityColor='#e59b28';
  ctx.clearRect(0,0,w,h);
  ctx.font='11px system-ui, sans-serif';
  ctx.textBaseline='middle';
  for(let i=0;i<=4;i++){
    const y=top+plotH*i/4;
    ctx.beginPath();ctx.strokeStyle=grid;ctx.lineWidth=1;ctx.moveTo(left,y);ctx.lineTo(w-right,y);ctx.stroke();
    ctx.fillStyle=muted;ctx.textAlign='right';ctx.fillText(String(50-i*12.5),left-6,y);
    ctx.textAlign='left';ctx.fillText(String(100-i*25),w-right+6,y);
  }
  const points=chartHistory;
  for(let i=0;i<3;i++){
    const index=points.length<2?0:Math.round((points.length-1)*i/2);
    const x=left+(points.length<2?plotW/2:plotW*index/(points.length-1));
    const label=points.length?new Date(points[index].time).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit',second:'2-digit'}):'--:--';
    ctx.fillStyle=muted;ctx.textAlign=i===0?'left':i===2?'right':'center';ctx.fillText(label,x,h-bottom+15);
  }
  function drawSeries(key,max,color){
    ctx.beginPath();ctx.strokeStyle=color;ctx.lineWidth=2;ctx.lineJoin='round';ctx.lineCap='round';
    let active=false;
    points.forEach((point,index)=>{
      const value=point[key];
      if(value===null){active=false;return;}
      const x=left+(points.length<2?plotW/2:plotW*index/(points.length-1));
      const y=top+plotH*(1-Math.max(0,Math.min(max,value))/max);
      if(active)ctx.lineTo(x,y);else{ctx.moveTo(x,y);active=true;}
    });
    ctx.stroke();
  }
  drawSeries('t',50,tempColor);
  drawSeries('h',100,humidityColor);
  if(points.length){
    const first=new Date(points[0].time),last=new Date(points[points.length-1].time);
    $('chart-range').textContent=points.length>1
      ?first.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'})+' – '+last.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'})
      :'Đang thu thập dữ liệu';
  }
}
window.addEventListener('resize',drawChart);
async function poll(){
  if(polling)return;
  polling=true;
  try{
    const response=await fetch('/api/status',{cache:'no-store'});
    if(!response.ok)throw new Error('HTTP '+response.status);
    const j=await response.json();
    render(j);$('json').textContent=JSON.stringify(j,null,2);$('msg').textContent='';
  }
  catch(e){$('msg').textContent='Mất kết nối tới ESP32';}
  finally{polling=false;}
}
poll();setInterval(poll,1000);
</script></body></html>
)rawliteral";

// ---------------------------- API web ---------------------------------------
String numOrNull(float v, int d) { return isnan(v) ? String("null") : String(v, d); }

void handleStatus() {
  portENTER_CRITICAL(&timerMux);
  int64_t  sum = sumPeriodUs;
  uint32_t ticks = tickCount;
  uint32_t periods = periodCount;
  portEXIT_CRITICAL(&timerMux);

  uint32_t now = millis();
  int32_t left = 0, lim = 1;
  if (state == ST_RUNNING) { lim = MAX_RUN_MS;    left = (int32_t)(MAX_RUN_MS - (now - runStartMs)); }
  if (state == ST_ERROR)   { lim = ERROR_HOLD_MS; left = (int32_t)(ERROR_HOLD_MS - (now - errorStartMs)); }
  if (left < 0) left = 0;

  String j;
  j.reserve(1200);
  j += "{\"state\":" + String((int)state) + ",\"duty\":" + String(dutyPct);
  j += ",\"t\":" + numOrNull(lastTemp, 1) + ",\"h\":" + numOrNull(lastHum, 1);
  j += ",\"sensor\":" + String(sensorErr) + ",\"sim\":" + String(simMode);
  j += ",\"left\":" + String(left) + ",\"lim\":" + String(lim);
  j += ",\"reason\":\"" + String(lastReason) + "\"";
  j += ",\"raw\":" + String((unsigned long)btnRawCount) + ",\"acc\":" + String((unsigned long)btnAcceptedCount);
  j += ",\"ticks\":" + String((unsigned long)ticks);
  if (periods) {
    double avg = (double)sum / periods;
    j += ",\"avg\":" + String(avg, 2) + ",\"dev\":" + String(avg - (double)TIMER_PERIOD_US, 2);
  } else {
    j += ",\"avg\":null,\"dev\":null";
  }
  j += ",\"log\":[";
  for (int i = 0; i < logCount; i++) {                      // mới nhất trước
    int k = (logHead - 1 - i + LOG_N * 2) % LOG_N;
    if (i) j += ',';
    j += "\"" + String(logBuf[k]) + "\"";
  }
  j += "]}";
  server.sendHeader("Cache-Control", "no-store");
  server.send(200, "application/json", j);
}

void handleCmd() {
  String c = server.arg("c");
  if (c.length() > 40) c = c.substring(0, 40);
  handleLine(c);
  server.send(200, "application/json", "{\"ok\":1}");
}

void handleSim() {
  if (server.hasArg("mode")) simMode = constrain(server.arg("mode").toInt(), 0, 2);
  logf("# SIM mode=%d", simMode);
  server.send(200, "application/json", "{\"sim\":" + String(simMode) + "}");
}

void connectWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.printf("# Dang ket noi WiFi %s ", WIFI_SSID);
  uint32_t t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 12000) { delay(300); Serial.print("."); }
  Serial.println();
  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("# IP: http://");
    Serial.println(WiFi.localIP());
  } else {
    WiFi.mode(WIFI_AP);
    WiFi.softAP(AP_SSID, AP_PASS);
    Serial.print("# WiFi that bai -> AP '");
    Serial.print(AP_SSID);
    Serial.print("' http://");
    Serial.println(WiFi.softAPIP());
  }
}

// ---------------------------- Khởi tạo timer phần cứng ----------------------
void timerInit() {
#if CORE_3X
  timer = timerBegin(1000000);                  // 1 MHz -> 1 tick = 1 us
  timerAttachInterrupt(timer, &onTimer);
  timerAlarm(timer, TIMER_PERIOD_US, true, 0);  // lặp lại
#else
  timer = timerBegin(0, 80, true);              // 80 MHz / 80 = 1 MHz
  timerAttachInterrupt(timer, &onTimer, true);
  timerAlarmWrite(timer, TIMER_PERIOD_US, true);
  timerAlarmEnable(timer);
#endif
}

// ---------------------------- setup / loop ----------------------------------
void setup() {
  Serial.begin(115200);
  delay(500);

  // Đưa các chân tải không dùng về LOW để an toàn
  for (int p : OFF_PINS) { pinMode(p, OUTPUT); digitalWrite(p, LOW); }
  if (DIR_PIN >= 0) { pinMode(DIR_PIN, OUTPUT); digitalWrite(DIR_PIN, LOW); }
  pinMode(LED_RED_PIN, OUTPUT);
  pinMode(LED_GREEN_PIN, OUTPUT);

  pwmInit();
  applyLoad();                                  // DỪNG -> duty 0

#if SENSOR_FROM_I2C
  Wire.begin(PIN_SDA, PIN_SCL);
#else
  dht.begin();
#endif

  pinMode(BUTTON_PIN, INPUT);                   // GPIO36 không có pull-up nội
  attachInterrupt(digitalPinToInterrupt(BUTTON_PIN), onButton, FALLING);

  timerInit();

  connectWiFi();
  server.on("/", []() {
    server.sendHeader("Cache-Control", "no-store");
    server.send_P(200, "text/html; charset=utf-8", INDEX_HTML);
  });
  server.on("/api/status", handleStatus);
  server.on("/api/cmd", handleCmd);
  server.on("/api/sim", handleSim);
  server.onNotFound([]() { server.send(404, "text/plain", ""); });
  server.begin();

  logf("San sang. Lenh: ON, OFF, DUTY <0-100>, STATS, STATUS");
}

void loop() {
  server.handleClient();
  readSerial();

  // Nút nhấn: đảo DỪNG <-> CHẠY
  if (btnPressedFlag) {
    btnPressedFlag = false;
    if (state == ST_STOPPED)      startRun();
    else if (state == ST_RUNNING) stopRun("nut nhan");
  }

  // Đọc cảm biến định kỳ
  uint32_t now = millis();
  if (now - lastSensorMs >= SENSOR_READ_MS) {
    lastSensorMs = now;
    float t, h;
    if (readSensor(t, h)) {
      lastTemp = t; lastHum = h;
      bool inRange = (t >= TEMP_MIN && t <= TEMP_MAX && h >= HUM_MIN && h <= HUM_MAX);
      sensorErr = inRange ? 0 : 2;
    } else {
      lastTemp = NAN; lastHum = NAN;
      sensorErr = 1;
    }

    if (sensorErr) {
      if (state == ST_RUNNING) {
        enterError(sensorErr == 1
                   ? (simMode ? "cam bien mat tin hieu (gia lap)" : "cam bien mat tin hieu (NaN)")
                   : (simMode ? "so do ngoai dai (gia lap)"       : "so do ngoai dai"));
      } else if (sensorErr != prevSensorErr) {
        logf("Canh bao: cam bien %s", sensorErr == 1 ? "mat tin hieu" : "ngoai dai");
      }
    } else if (prevSensorErr) {
      logf("Cam bien da binh thuong");
    }
    prevSensorErr = sensorErr;
  }

  // Quá thời gian chạy tối đa
  if (state == ST_RUNNING && (millis() - runStartMs >= MAX_RUN_MS)) {
    stopRun("qua MAX_RUN_MS");
  }

  // Hết thời gian giữ LỖI -> về DỪNG
  if (state == ST_ERROR && (millis() - errorStartMs >= ERROR_HOLD_MS)) {
    state = ST_STOPPED;
    applyLoad();
    logf("[DUNG] het thoi gian giu LOI");
  }
}