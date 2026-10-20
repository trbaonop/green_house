/*
 * [BAN MILLIS] lay mau bang millis(): loop() chay lien tuc, web va nut phan hoi ngay.
 * TH02 - ESP32: doc DHT11 + web ve bieu do duong + xuat CSV + gia lap loi cam bien
 *
 * Chan (theo so do nha kinh): DHT11=GPIO14, SDA=33, SCL=32, ADC=39, nut=36, LED=26
 * Web: / (bieu do), /api/data?since=N, /api/sim?mode=0..3, /export.csv
 * Gia lap loi: 0=tat, 1=mat tin hieu (DHT_READ_FAIL), 2=ngoai dai (OUT_OF_RANGE), 3=ngau nhien, 4=spike (nhieu dot bien, van hop le)
 * Bo loc: trung binh truot 5 mau (avg5) va trung vi 5 mau (med5), chi tinh tren mau hop le
 *   -> dong gia lap duoc danh dau sim=1 trong CSV de khong lan voi loi that.
 * Serial CSV: time_ms,boot_id,seq,temp_c,hum_pct,valid,error,temp_avg5,hum_avg5,temp_med5,hum_med5,dt_ms,sim
 *   (time_ms = ms ke tu luc khoi dong, khong phai gio lich)
 * SENSOR_FROM_I2C=1: so do lay tu Arduino (DHT11 o D8 cua Arduino) qua I2C 0x08, khong doc GPIO14.
 * Lenh Serial: 'i' = quet I2C, 'a' = doc ADC, 'r' = doc tho 8 byte tu slave I2C 0x08
 */
#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <Wire.h>
#include <Preferences.h>
#include <DHT.h>

const char* WIFI_SSID = "VJU Office";
const char* WIFI_PASS = "VJuOffice@2023";
const char* AP_SSID   = "ESP32-trbao";
const char* AP_PASS   = "12345678";

#define PIN_DHT 14
#define PIN_SDA 33
#define PIN_SCL 32
#define PIN_ADC 39
#define PIN_BTN 36   // GPIO36 khong co pull-up noi bo: can pull-up ngoai
#define PIN_LED 26
#define SENSOR_FROM_I2C 1   // 1 = lay so do tu Arduino (slave I2C 0x08); 0 = doc DHT11 truc tiep o GPIO14

// Chan dieu khien dong co/bom (MOTOR1: 5,17 | MOTOR2: 23,22 | ARRAY_LED: 12,13). Ep LOW de TAT.
static const int OFF_PINS[] = {5, 17, 23, 22, 12, 13};

const uint32_t PERIOD_MS = 2000;   // chu ky lay mau (DHT11 >= 1 s)
const int N = 5;                   // trung binh truot
const int CAP = 1000;              // so mau luu trong RAM
const int MAX_ROWS = 300;          // toi da so dong tra ve moi lan

struct Sample { uint32_t ms, dt; float t, h, ta, ha, tm, hm; uint16_t rt, rh; uint8_t ok, err, sim; };
Sample buf[CAP];
uint32_t seq = 0, bootId = 0, lastMs = 0, prevMs = 0, lastBtnMs = 0;
const char* ERR[] = {"", "DHT_READ_FAIL", "OUT_OF_RANGE", "I2C_NO_RESPONSE"};
uint8_t rawFrame[4] = {0, 0, 0, 0};
uint8_t simMode = 0;

DHT dht(PIN_DHT, DHT11);
WebServer server(80);
float tb[N], hb[N];
int cnt = 0, idx = 0;
bool btnPrev = HIGH, ledOn = false;

// ===================== Trang web =====================
const char INDEX_HTML[] PROGMEM = R"rawliteral(
<!DOCTYPE html><html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>TH02 - Nhiệt độ & độ ẩm</title>
<style>
:root{--bg:#f4f6f4;--card:#fff;--tx:#1d2a1f;--mut:#6b7a6e;--bd:#dfe6df;--ok:#2e8b57;--bad:#d9534f;--t:#e07b39;--h:#2f7fc1}
@media(prefers-color-scheme:dark){:root{--bg:#141a15;--card:#1e271f;--tx:#e6efe7;--mut:#93a396;--bd:#2d392f}}
*{box-sizing:border-box}body{margin:0;padding:16px;background:var(--bg);color:var(--tx);font-family:system-ui,Segoe UI,Roboto,sans-serif}
.wrap{max-width:900px;margin:auto}h1{font-size:20px;margin:0 0 12px}
.card{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:14px;margin-bottom:12px}
.stats{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(130px,1fr))}
.lbl{font-size:12px;color:var(--mut)}.val{font-size:26px;font-weight:600}.u{font-size:13px;color:var(--mut);font-weight:400}
canvas{width:100%;height:210px;display:block}
.bar{display:flex;flex-wrap:wrap;gap:10px;align-items:center}
select,button{padding:8px 12px;border-radius:8px;border:1px solid var(--bd);background:var(--bg);color:var(--tx);font-size:14px}
button.p{background:var(--ok);color:#fff;border:0;cursor:pointer}
.lg i{display:inline-block;width:14px;height:3px;margin:0 4px 3px 10px;vertical-align:middle}
.sim{border-color:var(--bad)}
</style></head><body><div class="wrap">
<h1>Nhiệt độ & độ ẩm (DHT11)</h1>
<div class="stats card">
  <div><div class="lbl">Nhiệt độ</div><div class="val"><span id="t">--</span><span class="u"> °C</span></div></div>
  <div><div class="lbl">Độ ẩm</div><div class="val"><span id="h">--</span><span class="u"> %</span></div></div>
  <div><div class="lbl">Mẫu hợp lệ / lỗi</div><div class="val"><span id="cok">0</span> / <span id="cer" style="color:var(--bad)">0</span></div></div>
  <div><div class="lbl">Trạng thái</div><div class="val" style="font-size:16px" id="st">--</div></div>
</div>
<div class="card">
  <div class="bar" style="margin-bottom:8px">
    <label>Cửa sổ <select id="win" onchange="win=+this.value;draw()">
      <option value="60">60 mẫu</option><option value="120" selected>120 mẫu</option>
      <option value="300">300 mẫu</option><option value="0">Tất cả</option></select></label>
    <label><input type="checkbox" id="avg" checked onchange="draw()"> Trung bình trượt 5</label>
    <label><input type="checkbox" id="med" checked onchange="draw()"> Trung vị 5</label>
    <button class="p" onclick="location.href='/data.csv'">Tải data.csv</button>
    <button class="p" onclick="savePng()">Tải chart.png</button>
    <button onclick="location.href='/export.csv'">CSV đầy đủ</button>
  </div>
  <div class="lbl lg"><i style="background:var(--bad);height:10px;width:4px;opacity:.5;margin-left:0"></i>Mẫu lỗi · nét liền = số đo gốc · nét đứt = trung bình trượt 5 · chấm = trung vị 5</div>
</div>
<div class="card"><div class="lbl"><b style="color:var(--t)">Nhiệt độ (°C)</b></div><canvas id="cvT"></canvas></div>
<div class="card"><div class="lbl"><b style="color:var(--h)">Độ ẩm (%)</b></div><canvas id="cvH"></canvas></div>
<div class="card" id="simcard">
  <div class="bar"><b>Giả lập lỗi cảm biến</b>
    <select id="sim" onchange="setSim(this.value)">
      <option value="0">Tắt (đọc thật)</option>
      <option value="1">Mất tín hiệu (DHT_READ_FAIL)</option>
      <option value="2">Giá trị ngoài dải (OUT_OF_RANGE)</option>
      <option value="3">Ngẫu nhiên ~25%</option>
      <option value="4">Nhiễu đột biến (spike, vẫn hợp lệ)</option></select></div>
  <div class="lbl" style="margin-top:6px">Mẫu giả lập được đánh dấu <b>sim=1</b> trong CSV.</div>
</div>
<div class="lbl" id="msg"></div>
</div>
<script>
const $=id=>document.getElementById(id);
let D=[],last=0,win=120;
// moi dong: [seq, ms, t, h, ta, ha, ok, err, sim]
async function poll(){
  try{
    const r=await fetch('/api/data?since='+last),j=await r.json();
    if(j.seq<last){D=[];last=0;return poll();}
    j.rows.forEach(x=>D.push(x));
    if(j.rows.length)last=j.rows[j.rows.length-1][0];
    if(D.length>1000)D=D.slice(-1000);
    if(document.activeElement!==$('sim'))$('sim').value=j.sim;
    $('simcard').className='card'+(j.sim?' sim':'');
    $('msg').textContent='';
    stats();draw();
  }catch(e){$('msg').textContent='Mất kết nối tới ESP32';}
}
function stats(){
  let ok=0,er=0;D.forEach(r=>r[6]?ok++:er++);
  $('cok').textContent=ok;$('cer').textContent=er;
  const l=D[D.length-1];if(!l)return;
  $('t').textContent=l[2]==null?'--':l[2].toFixed(1);
  $('h').textContent=l[3]==null?'--':l[3].toFixed(0);
  const names=['Bình thường','DHT_READ_FAIL','OUT_OF_RANGE','I2C_NO_RESPONSE'];
  $('st').textContent=names[l[7]]+(l[8]?' (giả lập)':'');
  $('st').style.color=l[6]?'var(--ok)':'var(--bad)';
}
const setSim=v=>fetch('/api/sim?mode='+v);
const fmt=ms=>{let s=Math.floor(ms/1000),h=Math.floor(s/3600),m=Math.floor(s%3600/60);s%=60;
  return (h?h+':':'')+String(m).padStart(2,'0')+':'+String(s).padStart(2,'0');};
function draw(){
  const rows=win?D.slice(-win):D;
  chart('cvT',rows,2,4,9,'--t',1);
  chart('cvH',rows,3,5,10,'--h',0);
}
function chart(id,rows,kr,ka,km,colVar,dg){
  const c=$(id),dpr=window.devicePixelRatio||1,W=c.clientWidth,H=c.clientHeight;
  c.width=W*dpr;c.height=H*dpr;const g=c.getContext('2d');g.scale(dpr,dpr);
  const cs=getComputedStyle(document.body),mut=cs.getPropertyValue('--mut'),bd=cs.getPropertyValue('--bd'),
        col=cs.getPropertyValue(colVar),bad=cs.getPropertyValue('--bad');
  g.font='11px system-ui';g.fillStyle=mut;
  if(rows.length<2){g.fillText('Đang chờ dữ liệu...',20,30);return;}
  const L=44,R=12,T=8,B=24,pw=W-L-R,ph=H-T-B,x0=rows[0][1],x1=rows[rows.length-1][1];
  const X=ms=>L+(ms-x0)/((x1-x0)||1)*pw;
  let lo=1e9,hi=-1e9;
  rows.forEach(r=>[kr,ka,km].forEach(k=>{if(r[k]!=null){lo=Math.min(lo,r[k]);hi=Math.max(hi,r[k]);}}));
  if(lo>hi){lo=0;hi=1}if(hi-lo<2){lo-=1;hi+=1}const p=(hi-lo)*.1;lo-=p;hi+=p;
  g.strokeStyle=bd;g.lineWidth=1;g.textAlign='right';
  for(let i=0;i<=4;i++){
    const y=T+ph*i/4;g.beginPath();g.moveTo(L,y);g.lineTo(W-R,y);g.stroke();
    g.fillStyle=mut;g.fillText((hi-(hi-lo)*i/4).toFixed(dg),L-4,y+4);
  }
  g.textAlign='center';g.fillStyle=mut;
  for(let i=0;i<=4;i++)g.fillText(fmt(x0+(x1-x0)*i/4),L+pw*i/4,H-6);
  g.globalAlpha=.25;g.fillStyle=bad;
  rows.forEach(r=>{if(!r[6])g.fillRect(X(r[1])-2,T,4,ph);});
  g.globalAlpha=1;
  const line=(k,dash)=>{
    g.beginPath();g.strokeStyle=col;g.lineWidth=2;g.setLineDash(dash);let pen=false;
    rows.forEach(r=>{if(r[k]==null){pen=false;return;}
      const y=T+ph-(r[k]-lo)/(hi-lo)*ph;pen?g.lineTo(X(r[1]),y):g.moveTo(X(r[1]),y);pen=true;});
    g.stroke();g.setLineDash([]);};
  line(kr,[]);
  if($('avg').checked)line(ka,[6,4]);
  if($('med').checked)line(km,[2,4]);
}
function savePng(){
  draw();
  const a=$('cvT'),b=$('cvH'),dpr=window.devicePixelRatio||1,pad=26*dpr;
  const cs=getComputedStyle(document.body),o=document.createElement('canvas');
  o.width=a.width;o.height=a.height+b.height+pad*2;
  const g=o.getContext('2d');
  g.fillStyle=cs.getPropertyValue('--card');g.fillRect(0,0,o.width,o.height);
  g.fillStyle=cs.getPropertyValue('--tx');g.font='bold '+(13*dpr)+'px system-ui';
  g.fillText('Nhiệt độ (°C): nét liền = gốc, nét đứt = trung bình trượt 5, chấm = trung vị 5',8*dpr,18*dpr);
  g.drawImage(a,0,pad);
  g.fillText('Độ ẩm (%): nét liền = gốc, nét đứt = trung bình trượt 5, chấm = trung vị 5',8*dpr,pad+a.height+18*dpr);
  g.drawImage(b,0,pad*2+a.height);
  o.toBlob(bl=>{const u=URL.createObjectURL(bl),l=document.createElement('a');
    l.href=u;l.download='chart.png';l.click();setTimeout(()=>URL.revokeObjectURL(u),1000);});
}
window.addEventListener('resize',draw);
poll();setInterval(poll,2000);
</script></body></html>
)rawliteral";

// ===================== Cam bien =====================
void field(float v, int d) { if (!isnan(v)) Serial.print(v, d); }

void scanI2c() {
  int found = 0;
  Serial.printf("# I2C scan SDA=%d SCL=%d\n", PIN_SDA, PIN_SCL);
  for (uint8_t a = 1; a < 127; a++) {
    Wire.beginTransmission(a);
    if (Wire.endTransmission() == 0) { Serial.printf("# I2C found 0x%02X\n", a); found++; }
  }
  Serial.printf("# I2C: %d thiet bi\n", found);
}

void readAdc() {
  int raw = analogRead(PIN_ADC);
  Serial.printf("# ADC GPIO%d raw=%d volt=%.2fV%s\n", PIN_ADC, raw, raw * 3.3f / 4095,
                (raw < 5 || raw > 4090) ? " (cham gioi han dai do)" : "");
}

#define I2C_SLAVE_ADDR 0x08   // nghi la ATmega8/Arduino giu DHT11 (chua kiem chung)
// Doc tho 8 byte tu slave de biet khung du lieu (lenh Serial 'r')
void probeSlave() {
  uint8_t n = Wire.requestFrom((uint8_t)I2C_SLAVE_ADDR, (uint8_t)8);
  Serial.printf("# I2C 0x%02X tra ve %d byte:", I2C_SLAVE_ADDR, n);
  while (Wire.available()) Serial.printf(" %02X", Wire.read());
  Serial.println();
}

// Bo loc trung vi: sap xep cua so n mau hop le roi lay phan tu giua (loai bo nhieu dot bien)
float median(const float *src, int n) {
  float v[N];
  for (int i = 0; i < n; i++) v[i] = src[i];
  for (int i = 1; i < n; i++) {            // insertion sort
    float x = v[i]; int j = i - 1;
    while (j >= 0 && v[j] > x) { v[j + 1] = v[j]; j--; }
    v[j + 1] = x;
  }
  return (n % 2) ? v[n / 2] : (v[n / 2 - 1] + v[n / 2]) / 2.0f;
}

// Doc 4 byte tu Arduino. GIA DINH (suy ra tu khung 01 05 02 08): nhiet do*10 va do am*10,
// moi gia tri 16 bit big-endian  =>  0x0105 = 26.1 C, 0x0208 = 52.0 %. Can doi chieu voi sketch Arduino.
// Tra ve: 0 = ok, 1 = slave khong tra loi, 2 = slave bao so do khong hop le (0xFFFF)
uint8_t readSlave(float &t, float &h) {
  uint8_t n = Wire.requestFrom((uint8_t)I2C_SLAVE_ADDR, (uint8_t)4);
  if (n < 4) { while (Wire.available()) Wire.read(); t = NAN; h = NAN; return 1; }
  for (int i = 0; i < 4; i++) rawFrame[i] = Wire.read();
  uint16_t rt = (rawFrame[0] << 8) | rawFrame[1];
  uint16_t rh = (rawFrame[2] << 8) | rawFrame[3];
  if (rt == 0xFFFF || rh == 0xFFFF) { t = NAN; h = NAN; return 2; }
  t = rt / 10.0f; h = rh / 10.0f;
  return 0;
}

void sample() {
  uint32_t now = millis();
  Sample &m = buf[seq % CAP];
  m.ms = now;
  m.dt = seq ? now - prevMs : 0;
  prevMs = now;

  float h, t;
#if SENSOR_FROM_I2C
  uint8_t st = readSlave(t, h);
#else
  h = dht.readHumidity(); t = dht.readTemperature();
  uint8_t st = (isnan(h) || isnan(t)) ? 2 : 0;
#endif

  // Gia lap loi (danh dau sim=1)
  m.sim = 0;
  bool inject = (simMode == 1 || simMode == 2) || (simMode == 3 && random(100) < 25);
  if (inject) {
    m.sim = 1;
    int kind = (simMode == 3) ? random(1, 3) : simMode;
    if (kind == 1) { t = NAN; h = NAN; }
    else { t = 85; h = 5; }
  }
  if (simMode == 4 && random(100) < 20) {   // nhieu dot bien nhung van trong dai hop le
    m.sim = 1;
    t += random(2) ? 8 : -8;
    h += random(2) ? 15 : -15;
  }

  // So nguyen goc (x10) cho data.csv; 0xFFFF = khong co so do
  m.rt = isnan(t) ? 0xFFFF : (uint16_t)lroundf(t * 10);
  m.rh = isnan(h) ? 0xFFFF : (uint16_t)lroundf(h * 10);
  m.err = (st == 1 && !m.sim) ? 3
        : ((st == 2 || isnan(t) || isnan(h)) ? 1 : ((t < 0 || t > 50 || h < 20 || h > 90) ? 2 : 0));
  m.ok = (m.err == 0);
  m.ta = NAN; m.ha = NAN; m.tm = NAN; m.hm = NAN;
  if (m.ok) {
    tb[idx] = t; hb[idx] = h;
    idx = (idx + 1) % N;
    if (cnt < N) cnt++;
    float st = 0, sh = 0;
    for (int i = 0; i < cnt; i++) { st += tb[i]; sh += hb[i]; }
    m.ta = st / cnt; m.ha = sh / cnt;
    m.tm = median(tb, cnt); m.hm = median(hb, cnt);
  } else {
    t = NAN; h = NAN;          // loi -> null, khong ghi 0
  }
  m.t = t; m.h = h;
  seq++;

  Serial.printf("%lu,%lu,%lu,", (unsigned long)m.ms, (unsigned long)bootId, (unsigned long)seq);
  field(m.t, 1);  Serial.print(',');
  field(m.h, 1);  Serial.print(',');
  Serial.printf("%d,%s,", m.ok, ERR[m.err]);
  field(m.ta, 2); Serial.print(',');
  field(m.ha, 2); Serial.print(',');
  field(m.tm, 1); Serial.print(',');
  field(m.hm, 1); Serial.print(',');
  Serial.printf("%lu,%d\n", (unsigned long)m.dt, m.sim);
}

// Ngat GPIO chi ghi thoi diem bam nut de DO DO TRE (khong phai ngat timer)
volatile uint32_t pressMs = 0;
volatile bool pressed = false;
void IRAM_ATTR onButton() {
  uint32_t n = millis();
  if (n - pressMs > 200) { pressMs = n; pressed = true; }
}

void handleInputs() {
  if (pressed) {
    uint32_t p = pressMs, now = millis();
    pressed = false;
    ledOn = !ledOn;
    digitalWrite(PIN_LED, ledOn);
    Serial.printf("# BTN press=%lu handled=%lu latency=%lu ms\n",
                  (unsigned long)p, (unsigned long)now, (unsigned long)(now - p));
  }
  while (Serial.available()) {
    char c = Serial.read();
    if (c == 'i') scanI2c();
    else if (c == 'a') readAdc();
    else if (c == 'r') probeSlave();
  }
}

// ===================== API =====================
String num(float v, int d, const char* nul) { return isnan(v) ? String(nul) : String(v, d); }

void handleData() {
  uint32_t since = server.arg("since").toInt();
  uint32_t first = (seq > CAP) ? seq - CAP + 1 : 1;
  uint32_t start = max(since + 1, first);
  if (seq >= start && seq - start + 1 > MAX_ROWS) start = seq - MAX_ROWS + 1;

  String j;
  j.reserve(MAX_ROWS * 48 + 80);
  j += "{\"seq\":" + String(seq) + ",\"sim\":" + String(simMode) + ",\"rows\":[";
  for (uint32_t q = start; q <= seq; q++) {
    const Sample &m = buf[(q - 1) % CAP];
    if (q != start) j += ',';
    j += "[" + String(q) + "," + String(m.ms) + "," + num(m.t, 1, "null") + "," + num(m.h, 1, "null") +
         "," + num(m.ta, 2, "null") + "," + num(m.ha, 2, "null") + "," + String(m.ok) + "," +
         String(m.err) + "," + String(m.sim) + "," + num(m.tm, 1, "null") + "," + num(m.hm, 1, "null") + "]";
  }
  j += "]}";
  server.sendHeader("Cache-Control", "no-store");
  server.send(200, "application/json", j);
}

void handleSim() {
  if (server.hasArg("mode")) simMode = constrain(server.arg("mode").toInt(), 0, 4);
  Serial.printf("# SIM mode=%d\n", simMode);
  server.send(200, "application/json", "{\"sim\":" + String(simMode) + "}");
}

void handleExport() {
  server.sendHeader("Content-Disposition", "attachment; filename=th02.csv");
  server.setContentLength(CONTENT_LENGTH_UNKNOWN);
  server.send(200, "text/csv", "");
  String s = "time_ms,boot_id,seq,temp_c,hum_pct,valid,error,temp_avg5,hum_avg5,temp_med5,hum_med5,dt_ms,sim\n";
  uint32_t first = (seq > CAP) ? seq - CAP + 1 : 1;
  for (uint32_t q = first; q <= seq; q++) {
    const Sample &m = buf[(q - 1) % CAP];
    s += String(m.ms) + "," + String(bootId) + "," + String(q) + "," + num(m.t, 1, "") + "," +
         num(m.h, 1, "") + "," + String(m.ok) + "," + ERR[m.err] + "," + num(m.ta, 2, "") + "," +
         num(m.ha, 2, "") + "," + num(m.tm, 1, "") + "," + num(m.hm, 1, "") + "," + String(m.dt) + "," + String(m.sim) + "\n";
    if (s.length() > 1500) { server.sendContent(s); s = ""; }
  }
  server.sendContent(s);
  server.sendContent("");
}

// data.csv theo mau de bai: timestamp,raw,converted,unit,valid (moi mau 2 dong: nhiet do, do am)
void handleDataCsv() {
  server.sendHeader("Content-Disposition", "attachment; filename=data.csv");
  server.setContentLength(CONTENT_LENGTH_UNKNOWN);
  server.send(200, "text/csv", "");
  String s = "timestamp,raw,converted,unit,valid\n";
  uint32_t first = (seq > CAP) ? seq - CAP + 1 : 1;
  for (uint32_t q = first; q <= seq; q++) {
    const Sample &m = buf[(q - 1) % CAP];
    bool hasT = (m.rt != 0xFFFF), hasH = (m.rh != 0xFFFF);
    s += String(m.ms) + "," + (hasT ? String(m.rt) : String("")) + "," +
         (hasT ? String(m.rt / 10.0f, 1) : String("")) + ",C," + String(m.ok) + "\n";
    s += String(m.ms) + "," + (hasH ? String(m.rh) : String("")) + "," +
         (hasH ? String(m.rh / 10.0f, 1) : String("")) + ",%," + String(m.ok) + "\n";
    if (s.length() > 1500) { server.sendContent(s); s = ""; }
  }
  server.sendContent(s);
  server.sendContent("");
}

// ===================== setup / loop =====================
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

void setup() {
  Serial.begin(115200);
  for (int p : OFF_PINS) { pinMode(p, OUTPUT); digitalWrite(p, LOW); }   // tat may bom/dong co
  pinMode(PIN_BTN, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(PIN_BTN), onButton, FALLING);
  pinMode(PIN_LED, OUTPUT);
  analogReadResolution(12);

  Preferences p;
  p.begin("th02", false);
  bootId = p.getUInt("boot", 0) + 1;
  p.putUInt("boot", bootId);
  p.end();

  Wire.begin(PIN_SDA, PIN_SCL);
#if !SENSOR_FROM_I2C
  pinMode(PIN_DHT, INPUT);               // khong bat pull-up noi bo de thu dung duong keo len cua mach
  delay(50);
  Serial.printf("# GPIO%d nhan roi = %d (1 = DATA duoc keo len; 0 = bi keo xuong/chap/chua co nguon)\n",
                PIN_DHT, digitalRead(PIN_DHT));
  dht.begin();
#endif
  Serial.printf("# boot_id=%lu mode=%s period=%lums\n", (unsigned long)bootId, "MILLIS", (unsigned long)PERIOD_MS);
  Serial.println("time_ms,boot_id,seq,temp_c,hum_pct,valid,error,temp_avg5,hum_avg5,temp_med5,hum_med5,dt_ms,sim");
  scanI2c();
  probeSlave();
  readAdc();

  connectWiFi();
  server.on("/", []() { server.send_P(200, "text/html; charset=utf-8", INDEX_HTML); });
  server.on("/api/data", handleData);
  server.on("/api/sim", handleSim);
  server.on("/export.csv", handleExport);
  server.on("/data.csv", handleDataCsv);
  server.onNotFound([]() { server.send(404, "text/plain", ""); });
  server.begin();
}

void loop() {
  server.handleClient();
  handleInputs();
  uint32_t now = millis();
  if (now - lastMs >= PERIOD_MS) {
    lastMs = now;
    sample();
    if (seq % 5 == 0) {
      readAdc();
#if SENSOR_FROM_I2C
      Serial.printf("# RAW %02X %02X %02X %02X\n", rawFrame[0], rawFrame[1], rawFrame[2], rawFrame[3]);
#endif
    }
  }
}
