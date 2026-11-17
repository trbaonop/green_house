"""API Bai 6: nhan telemetry/ack tu MQTT, phuc vu dashboard, gui lenh co cmd_id.

Chay:  pip install -r requirements.txt
       python app.py
Dat file nay trong thu muc api/, dashboard/index.html nam canh thu muc api/.
"""
import json, os, secrets, sqlite3, threading, time, uuid
from datetime import datetime
from functools import wraps

import paho.mqtt.client as mqtt
from flask import Flask, jsonify, request, send_from_directory

MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
NODE_ID = os.getenv("NODE_ID", "node01")          # phai trung DEVICE_ID trong config.h
T_TELE = os.getenv("T_TELE", f"nhom01/{NODE_ID}/telemetry")
T_CMD = os.getenv("T_CMD", f"nhom01/{NODE_ID}/cmd")
T_ACK = os.getenv("T_ACK", f"nhom01/{NODE_ID}/ack")

ACK_TIMEOUT_S = 5      # qua han neu khong co ack
CMD_TTL_S = 10         # han hieu luc lenh (doc lap voi timeout cho ack)
STALE_S = 30           # du lieu cu neu qua 30 giay

# Doi mat khau truoc khi nop/bao ve. role: operator duoc gui lenh, viewer chi xem.
USERS = {
    "admin": {"password": os.getenv("ADMIN_PASS", "admin123"), "role": "operator"},
    "viewer": {"password": os.getenv("VIEWER_PASS", "viewer123"), "role": "viewer"},
}
TOKEN_TTL_S = 3600

BASE = os.path.dirname(os.path.abspath(__file__))
DASH = os.path.join(BASE, "..", "dashboard")
app = Flask(__name__, static_folder=None)

lock = threading.Lock()
db = sqlite3.connect(os.path.join(BASE, "dashboard.db"), check_same_thread=False)
db.row_factory = sqlite3.Row
db.executescript("""
CREATE TABLE IF NOT EXISTS telemetry(
  id INTEGER PRIMARY KEY AUTOINCREMENT, seq INT, ts INT, temp REAL, hum REAL,
  rain INT, valid INT, fsm_state TEXT, pump INT, recv_epoch REAL);
CREATE TABLE IF NOT EXISTS commands(
  cmd_id TEXT PRIMARY KEY, action TEXT, duty INT, username TEXT,
  sent_at REAL, status TEXT, reason TEXT, ack_at REAL, late INT DEFAULT 0);
""")
db.commit()

tokens = {}  # token -> {user, role, exp}


def log(fname, line):
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(os.path.join(BASE, fname), "a", encoding="utf-8") as f:
        f.write(f"[{stamp}] {line}\n")


# ------------------------------------------------------------------ MQTT
def on_connect(client, userdata, flags, reason_code, properties=None):
    print("[MQTT] connected:", reason_code)
    client.subscribe([(T_TELE, 1), (T_ACK, 1)])


def on_message(client, userdata, msg):
    try:
        d = json.loads(msg.payload)
    except Exception:
        return
    if msg.topic == T_TELE:
        with lock:
            db.execute(
                "INSERT INTO telemetry(seq,ts,temp,hum,rain,valid,fsm_state,pump,recv_epoch)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (d.get("seq"), d.get("ts"), d.get("temp"), d.get("hum"), d.get("rain"),
                 int(bool(d.get("valid"))), d.get("fsm_state"), d.get("pump"), time.time()))
            db.commit()
    elif msg.topic == T_ACK:
        handle_ack(d)


def handle_ack(d):
    cmd_id, node, result = d.get("cmd_id"), d.get("device_id"), d.get("result")
    if node != NODE_ID:
        log("cmd_ack_log.txt", f"ACK BO QUA: sai node ({node}) cmd_id={cmd_id}")
        return
    with lock:
        row = db.execute("SELECT * FROM commands WHERE cmd_id=?", (cmd_id,)).fetchone()
        if row is None:
            log("cmd_ack_log.txt", f"ACK BO QUA: cmd_id la {cmd_id}")
            return
        if row["status"] not in ("pending", "timeout"):
            return  # ack trung, khong doi trang thai
        late = 1 if row["status"] == "timeout" else 0
        if result == "ok":
            status = "confirmed"
        elif result == "rejected":
            status = "rejected"
        else:
            log("cmd_ack_log.txt", f"ACK KHONG HOP LE cmd_id={cmd_id}: {d}")
            return
        db.execute("UPDATE commands SET status=?, reason=?, ack_at=?, late=? WHERE cmd_id=?",
                   (status, d.get("reason"), time.time(), late, cmd_id))
        db.commit()
    log("cmd_ack_log.txt",
        f"ACK cmd_id={cmd_id} node={node} result={result} late={late} reason={d.get('reason')}")


mq = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
if os.getenv("MQTT_USER"):
    mq.username_pw_set(os.getenv("MQTT_USER"), os.getenv("MQTT_PASS", ""))
mq.on_connect = on_connect
mq.on_message = on_message
mq.reconnect_delay_set(1, 10)


def mqtt_start():
    try:
        mq.connect_async(MQTT_HOST, MQTT_PORT)
        mq.loop_start()
    except Exception as e:
        print("[MQTT] loi:", e)


# ------------------------------------------------------------------ Auth
def current_user():
    h = request.headers.get("Authorization", "")
    tok = h[7:] if h.startswith("Bearer ") else ""
    info = tokens.get(tok)
    if info and info["exp"] > time.time():
        return info
    return None


def require_operator(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        u = current_user()
        ip = request.remote_addr
        if u is None:
            log("log_denied.txt", f"TU CHOI 401 ip={ip} POST {request.path}: token khong hop le/het han")
            return jsonify(error="unauthorized", status="rejected"), 401
        if u["role"] != "operator":
            log("log_denied.txt",
                f"TU CHOI 403 ip={ip} user={u['user']} role={u['role']} POST {request.path}: "
                f"khong du quyen, KHONG publish xuong broker")
            return jsonify(error="forbidden", status="rejected"), 403
        request.user = u
        return fn(*a, **kw)
    return wrapper


@app.post("/api/login")
def login():
    d = request.get_json(silent=True) or {}
    u = USERS.get(d.get("username"))
    if not u or u["password"] != d.get("password"):
        log("log_denied.txt", f"DANG NHAP SAI ip={request.remote_addr} user={d.get('username')}")
        return jsonify(error="sai tai khoan hoac mat khau"), 401
    tok = secrets.token_urlsafe(24)
    tokens[tok] = {"user": d["username"], "role": u["role"], "exp": time.time() + TOKEN_TTL_S}
    return jsonify(token=tok, user=d["username"], role=u["role"])


# ------------------------------------------------------------------ Du lieu
@app.get("/api/latest")
def latest():
    with lock:
        r = db.execute("SELECT * FROM telemetry ORDER BY id DESC LIMIT 1").fetchone()
    base = {"mqtt_connected": mq.is_connected(), "node": NODE_ID, "stale_after_s": STALE_S,
            "unit": {"temp": "°C", "hum": "%", "rain": ""}}
    if r is None:
        return jsonify(has_data=False, **base)
    age = time.time() - r["recv_epoch"]
    return jsonify(
        has_data=True, stale=age > STALE_S, age_s=round(age, 1),
        updated_at=datetime.fromtimestamp(r["recv_epoch"]).strftime("%H:%M:%S %d/%m/%Y"),
        seq=r["seq"], temp=r["temp"], hum=r["hum"], rain=r["rain"],
        valid=bool(r["valid"]), fsm_state=r["fsm_state"], pump=r["pump"], **base)


# ------------------------------------------------------------------ Lenh
def cmd_view(row):
    return {"cmd_id": row["cmd_id"], "action": row["action"], "duty": row["duty"],
            "user": row["username"], "status": row["status"], "reason": row["reason"],
            "late": bool(row["late"]), "sent_at": datetime.fromtimestamp(row["sent_at"]).strftime("%H:%M:%S")}


def refresh_timeouts():
    now = time.time()
    with lock:
        rows = db.execute("SELECT cmd_id FROM commands WHERE status='pending' AND sent_at<?",
                          (now - ACK_TIMEOUT_S,)).fetchall()
        for r in rows:
            db.execute("UPDATE commands SET status='timeout' WHERE cmd_id=?", (r["cmd_id"],))
            log("log_timeout.txt",
                f"QUA HAN cmd_id={r['cmd_id']}: khong co ack sau {ACK_TIMEOUT_S}s "
                f"(chua ket luan node tat hay chua, chi la chua xac nhan)")
        db.commit()


@app.post("/api/command")
@require_operator
def command():
    d = request.get_json(silent=True) or {}
    action = d.get("action")
    if action not in ("pump_on", "pump_off"):
        return jsonify(error="action phai la pump_on hoac pump_off"), 400
    try:
        duty = int(d.get("duty", 100))
    except (TypeError, ValueError):
        return jsonify(error="duty khong hop le"), 400
    if not 0 <= duty <= 100:
        return jsonify(error="duty phai trong 0..100"), 400
    if not mq.is_connected():
        return jsonify(error="API chua ket noi duoc broker, lenh KHONG duoc gui"), 503

    cmd_id = str(uuid.uuid4())
    now = time.time()
    payload = {"cmd_id": cmd_id, "device_id": NODE_ID, "action": action, "duty": duty,
               "sent_at": int(now), "ttl_ms": CMD_TTL_S * 1000}
    with lock:
        db.execute("INSERT INTO commands(cmd_id,action,duty,username,sent_at,status) VALUES(?,?,?,?,?,'pending')",
                   (cmd_id, action, duty, request.user["user"], now))
        db.commit()
    info = mq.publish(T_CMD, json.dumps(payload), qos=1, retain=False)  # khong retained
    log("cmd_ack_log.txt", f"CMD cmd_id={cmd_id} user={request.user['user']} {action} duty={duty} "
                           f"-> {T_CMD} (mid={info.mid})")
    # 202: server da nhan va gui di. KHONG co nghia thiet bi da thuc hien.
    return jsonify(cmd_id=cmd_id, status="pending"), 202


@app.get("/api/command/<cmd_id>")
def command_status(cmd_id):
    refresh_timeouts()
    with lock:
        row = db.execute("SELECT * FROM commands WHERE cmd_id=?", (cmd_id,)).fetchone()
    if row is None:
        return jsonify(error="not found"), 404
    return jsonify(cmd_view(row))


@app.get("/api/commands")
def commands():
    refresh_timeouts()
    with lock:
        rows = db.execute("SELECT * FROM commands ORDER BY sent_at DESC LIMIT 10").fetchall()
    return jsonify([cmd_view(r) for r in rows])


# ------------------------------------------------------------------ Trang chu
@app.get("/")
def home():
    return send_from_directory(DASH, "index.html")


if __name__ == "__main__":
    mqtt_start()
    app.run(host="0.0.0.0", port=5000, debug=False)
