"""TH08 - API nhà kính: RBAC + MQTT TLS + telemetry/ACK/command."""
import json, os, secrets, sqlite3, threading, time, uuid, hashlib, hmac, ssl
from datetime import datetime
from functools import wraps

import paho.mqtt.client as mqtt
from flask import Flask, jsonify, request, send_from_directory

MQTT_HOST = os.getenv("MQTT_HOST", "localhost")  # Phai khop SAN cua broker certificate
MQTT_PORT = int(os.getenv("MQTT_PORT", "8883"))
MQTT_USER = os.getenv("MQTT_USER", "greenhouse_api")
MQTT_PASS = os.getenv("MQTT_PASS", "")
MQTT_CA_CERT = os.getenv("MQTT_CA_CERT", "")
NODE_ID = os.getenv("NODE_ID", "node01")
T_TELE = os.getenv("T_TELE", f"nhom01/{NODE_ID}/telemetry")
T_CMD = os.getenv("T_CMD", f"nhom01/{NODE_ID}/cmd")
T_ACK = os.getenv("T_ACK", f"nhom01/{NODE_ID}/ack")

CONFIG = {
    "ack_timeout_s": int(os.getenv("ACK_TIMEOUT_S", "5")),
    "cmd_ttl_s": int(os.getenv("CMD_TTL_S", "10")),
    "stale_s": int(os.getenv("STALE_S", "30")),
}
TOKEN_TTL_S = 3600
BASE = os.path.dirname(os.path.abspath(__file__))
DASH = os.path.abspath(os.path.join(BASE, "..", "dashboard"))
DB_PATH = os.getenv("DB_PATH", os.path.join(BASE, "dashboard.db"))
app = Flask(__name__, static_folder=None)

lock = threading.RLock()
db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.row_factory = sqlite3.Row
db.executescript("""
CREATE TABLE IF NOT EXISTS telemetry(
 id INTEGER PRIMARY KEY AUTOINCREMENT, seq INT, ts INT, temp REAL, hum REAL,
 rain INT, valid INT, fsm_state TEXT, pump INT, recv_epoch REAL);
CREATE TABLE IF NOT EXISTS commands(
 cmd_id TEXT PRIMARY KEY, action TEXT, duty INT, username TEXT,
 sent_at REAL, status TEXT, reason TEXT, ack_at REAL, late INT DEFAULT 0);
CREATE TABLE IF NOT EXISTS users(
 username TEXT PRIMARY KEY, password_hash TEXT NOT NULL, role TEXT NOT NULL
 CHECK(role IN ('operator','admin')));
""")
db.commit()

tokens = {}  # token -> {user, role, exp}
users_lock = threading.RLock()

def password_hash(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 240000)
    return salt.hex() + "$" + digest.hex()

def verify_password(password, stored):
    try:
        salt_hex, digest_hex = stored.split("$", 1)
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 240000)
        return hmac.compare_digest(candidate.hex(), digest_hex)
    except Exception:
        return False

def log(fname, line):
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(os.path.join(BASE, fname), "a", encoding="utf-8") as f:
        f.write(f"[{stamp}] {line}\n")

def bootstrap_user(env_user, env_pass, role):
    if env_user and env_pass:
        with users_lock:
            db.execute("INSERT OR IGNORE INTO users(username,password_hash,role) VALUES(?,?,?)",
                       (env_user, password_hash(env_pass), role))
            db.commit()

bootstrap_user(os.getenv("ADMIN_USERNAME", "admin"), os.getenv("ADMIN_PASSWORD"), "admin")
bootstrap_user(os.getenv("OPERATOR_USERNAME", "operator"), os.getenv("OPERATOR_PASSWORD"), "operator")

def current_user():
    header = request.headers.get("Authorization", "")
    token = header[7:] if header.startswith("Bearer ") else ""
    info = tokens.get(token)
    if info and info["exp"] > time.time():
        return info
    if token:
        tokens.pop(token, None)
    return None

def require_roles(*roles):
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = current_user()
            if user is None:
                log("log_denied.txt", f"DENY 401 ip={request.remote_addr} {request.method} {request.path}: token missing/invalid")
                return jsonify(error="unauthorized"), 401
            if roles and user["role"] not in roles:
                log("log_denied.txt", f"DENY 403 ip={request.remote_addr} user={user['user']} role={user['role']} {request.method} {request.path}")
                return jsonify(error="forbidden"), 403
            request.user = user
            log("log_allowed.txt", f"ALLOW user={user['user']} role={user['role']} {request.method} {request.path}")
            return fn(*args, **kwargs)
        return wrapper
    return deco

# ---------------- MQTT callbacks
def handle_ack(data):
    cmd_id, node, result = data.get("cmd_id"), data.get("device_id"), data.get("result")
    if node != NODE_ID:
        log("cmd_ack_log.txt", f"ACK ignored: wrong node={node} cmd_id={cmd_id}")
        return
    with lock:
        row = db.execute("SELECT * FROM commands WHERE cmd_id=?", (cmd_id,)).fetchone()
        if row is None or row["status"] not in ("pending", "timeout"):
            log("cmd_ack_log.txt", f"ACK ignored: unknown/duplicate cmd_id={cmd_id}")
            return
        if result not in ("ok", "rejected"):
            log("cmd_ack_log.txt", f"ACK invalid cmd_id={cmd_id}: {data}")
            return
        late = int(row["status"] == "timeout")
        status = "confirmed" if result == "ok" else "rejected"
        db.execute("UPDATE commands SET status=?,reason=?,ack_at=?,late=? WHERE cmd_id=?",
                   (status, data.get("reason"), time.time(), late, cmd_id))
        db.commit()
    log("cmd_ack_log.txt", f"ACK cmd_id={cmd_id} node={node} result={result} late={late}")

def on_connect(client, userdata, flags, reason_code, properties=None):
    print("[MQTT] connected:", reason_code)
    if reason_code == 0:
        client.subscribe([(T_TELE, 1), (T_ACK, 1)])

def on_message(client, userdata, msg):
    try:
        data = json.loads(msg.payload.decode("utf-8"))
    except Exception:
        log("cmd_ack_log.txt", f"MQTT invalid JSON topic={msg.topic}")
        return
    if msg.topic == T_TELE:
        if data.get("device_id") not in (None, NODE_ID):
            log("cmd_ack_log.txt", f"Telemetry ignored: wrong device_id={data.get('device_id')}")
            return
        with lock:
            db.execute("INSERT INTO telemetry(seq,ts,temp,hum,rain,valid,fsm_state,pump,recv_epoch) VALUES(?,?,?,?,?,?,?,?,?)",
                       (data.get("seq"), data.get("ts"), data.get("temp"), data.get("hum"), data.get("rain"),
                        int(bool(data.get("valid"))), data.get("fsm_state"), data.get("pump"), time.time()))
            db.commit()
    elif msg.topic == T_ACK:
        handle_ack(data)

mq = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
if MQTT_USER:
    mq.username_pw_set(MQTT_USER, MQTT_PASS)
if not MQTT_CA_CERT:
    print("[WARN] MQTT_CA_CERT chua duoc dat; API se khong ket noi MQTT de tranh TLS khong xac thuc.")
else:
    mq.tls_set(ca_certs=MQTT_CA_CERT, cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLS_CLIENT)
    mq.tls_insecure_set(False)
mq.on_connect = on_connect
mq.on_message = on_message
mq.reconnect_delay_set(1, 10)

def mqtt_start():
    if not MQTT_CA_CERT:
        return
    try:
        mq.connect_async(MQTT_HOST, MQTT_PORT)
        mq.loop_start()
    except Exception as exc:
        print("[MQTT] connection error:", exc)

# ---------------- Auth
@app.post("/api/login")
def login():
    data = request.get_json(silent=True) or {}
    username, password = data.get("username", ""), data.get("password", "")
    with users_lock:
        row = db.execute("SELECT username,password_hash,role FROM users WHERE username=?", (username,)).fetchone()
    if not row or not verify_password(password, row["password_hash"]):
        log("log_denied.txt", f"LOGIN_DENIED ip={request.remote_addr} username={username}")
        return jsonify(error="sai tai khoan hoac mat khau"), 401
    token = secrets.token_urlsafe(32)
    tokens[token] = {"user": row["username"], "role": row["role"], "exp": time.time() + TOKEN_TTL_S}
    log("log_allowed.txt", f"LOGIN_ALLOW user={username} role={row['role']}")
    return jsonify(token=token, user=username, role=row["role"], expires_in=TOKEN_TTL_S)

@app.post("/api/logout")
@require_roles("operator", "admin")
def logout():
    header = request.headers.get("Authorization", "")
    tokens.pop(header[7:], None)
    return jsonify(ok=True)

# ---------------- Telemetry and command APIs
@app.get("/api/latest")
@require_roles("operator", "admin")
def latest():
    with lock:
        row = db.execute("SELECT * FROM telemetry ORDER BY id DESC LIMIT 1").fetchone()
    base = {"mqtt_connected": mq.is_connected(), "node": NODE_ID, "stale_after_s": CONFIG["stale_s"],
            "unit": {"temp": "°C", "hum": "%", "rain": ""}}
    if row is None:
        return jsonify(has_data=False, **base)
    age = time.time() - row["recv_epoch"]
    return jsonify(has_data=True, stale=age > CONFIG["stale_s"], age_s=round(age, 1),
                    updated_at=datetime.fromtimestamp(row["recv_epoch"]).strftime("%H:%M:%S %d/%m/%Y"),
                    seq=row["seq"], temp=row["temp"], hum=row["hum"], rain=row["rain"],
                    valid=bool(row["valid"]), fsm_state=row["fsm_state"], pump=row["pump"], **base)

def refresh_timeouts():
    now = time.time()
    with lock:
        rows = db.execute("SELECT cmd_id FROM commands WHERE status='pending' AND sent_at<?",
                          (now - CONFIG["ack_timeout_s"],)).fetchall()
        for row in rows:
            db.execute("UPDATE commands SET status='timeout' WHERE cmd_id=?", (row["cmd_id"],))
            log("log_timeout.txt", f"TIMEOUT cmd_id={row['cmd_id']} after {CONFIG['ack_timeout_s']}s; execution unknown")
        db.commit()

def cmd_view(row):
    return {"cmd_id": row["cmd_id"], "action": row["action"], "duty": row["duty"],
            "user": row["username"], "status": row["status"], "reason": row["reason"],
            "late": bool(row["late"]),
            "sent_at": datetime.fromtimestamp(row["sent_at"]).strftime("%H:%M:%S")}

@app.post("/api/command")
@require_roles("operator", "admin")
def command():
    data = request.get_json(silent=True) or {}
    action = data.get("action")
    if action not in ("pump_on", "pump_off"):
        return jsonify(error="action phai la pump_on hoac pump_off"), 400
    try:
        duty = int(data.get("duty", 100))
    except (TypeError, ValueError):
        return jsonify(error="duty khong hop le"), 400
    if not 0 <= duty <= 100:
        return jsonify(error="duty phai trong 0..100"), 400
    if not mq.is_connected():
        return jsonify(error="MQTT TLS chua ket noi; lenh chua duoc gui"), 503
    cmd_id, now = str(uuid.uuid4()), time.time()
    payload = {"cmd_id": cmd_id, "device_id": NODE_ID, "action": action, "duty": duty,
               "sent_at": int(now), "ttl_ms": CONFIG["cmd_ttl_s"] * 1000}
    with lock:
        db.execute("INSERT INTO commands(cmd_id,action,duty,username,sent_at,status) VALUES(?,?,?,?,?,'pending')",
                   (cmd_id, action, duty, request.user["user"], now))
        db.commit()
    info = mq.publish(T_CMD, json.dumps(payload), qos=1, retain=False)
    if info.rc != mqtt.MQTT_ERR_SUCCESS:
        with lock:
            db.execute("UPDATE commands SET status='rejected',reason='MQTT publish failed' WHERE cmd_id=?", (cmd_id,))
            db.commit()
        return jsonify(error="MQTT publish failed", cmd_id=cmd_id), 503
    log("cmd_ack_log.txt", f"CMD cmd_id={cmd_id} user={request.user['user']} action={action} duty={duty} topic={T_CMD}")
    return jsonify(cmd_id=cmd_id, status="pending"), 202

@app.get("/api/command/<cmd_id>")
@require_roles("operator", "admin")
def command_status(cmd_id):
    refresh_timeouts()
    with lock:
        row = db.execute("SELECT * FROM commands WHERE cmd_id=?", (cmd_id,)).fetchone()
    if row is None:
        return jsonify(error="not found"), 404
    return jsonify(cmd_view(row))

@app.get("/api/commands")
@require_roles("operator", "admin")
def commands():
    refresh_timeouts()
    with lock:
        rows = db.execute("SELECT * FROM commands ORDER BY sent_at DESC LIMIT 10").fetchall()
    return jsonify([cmd_view(row) for row in rows])

# ---------------- Admin-only APIs
@app.get("/api/admin/config")
@require_roles("admin")
def admin_config():
    return jsonify(node_id=NODE_ID, mqtt_host=MQTT_HOST, mqtt_port=MQTT_PORT,
                   mqtt_tls=bool(MQTT_CA_CERT), telemetry_topic=T_TELE, command_topic=T_CMD,
                   ack_topic=T_ACK, **CONFIG)

@app.post("/api/admin/config")
@require_roles("admin")
def update_config():
    data = request.get_json(silent=True) or {}
    for key in ("ack_timeout_s", "cmd_ttl_s", "stale_s"):
        if key in data:
            try:
                value = int(data[key])
                if not 1 <= value <= 3600:
                    raise ValueError()
            except (TypeError, ValueError):
                return jsonify(error=f"{key} phai la so nguyen 1..3600"), 400
            CONFIG[key] = value
    log("log_allowed.txt", f"CONFIG_UPDATED by={request.user['user']} values={CONFIG}")
    return jsonify(ok=True, **CONFIG)

@app.get("/api/admin/users")
@require_roles("admin")
def list_users():
    with users_lock:
        rows = db.execute("SELECT username,role FROM users ORDER BY username").fetchall()
    return jsonify([{"username": r["username"], "role": r["role"]} for r in rows])

@app.post("/api/admin/users")
@require_roles("admin")
def create_user():
    data = request.get_json(silent=True) or {}
    username, password, role = data.get("username", "").strip(), data.get("password", ""), data.get("role")
    if not username or len(username) > 64 or len(password) < 10 or role not in ("operator", "admin"):
        return jsonify(error="username 1..64 ky tu; password toi thieu 10 ky tu; role operator/admin"), 400
    try:
        with users_lock:
            db.execute("INSERT INTO users(username,password_hash,role) VALUES(?,?,?)",
                       (username, password_hash(password), role))
            db.commit()
    except sqlite3.IntegrityError:
        return jsonify(error="username da ton tai"), 409
    log("log_allowed.txt", f"USER_CREATED by={request.user['user']} username={username} role={role}")
    return jsonify(ok=True), 201

@app.delete("/api/admin/users/<username>")
@require_roles("admin")
def delete_user(username):
    if username == request.user["user"]:
        return jsonify(error="khong the tu xoa tai khoan dang dang nhap"), 400
    with users_lock:
        row = db.execute("SELECT role FROM users WHERE username=?", (username,)).fetchone()
        if not row:
            return jsonify(error="user not found"), 404
        if row["role"] == "admin":
            count = db.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0]
            if count <= 1:
                return jsonify(error="khong the xoa admin cuoi cung"), 400
        db.execute("DELETE FROM users WHERE username=?", (username,))
        db.commit()
    for tok, info in list(tokens.items()):
        if info["user"] == username:
            tokens.pop(tok, None)
    log("log_allowed.txt", f"USER_DELETED by={request.user['user']} username={username}")
    return jsonify(ok=True)

@app.get("/")
def home():
    return send_from_directory(DASH, "index.html")

if __name__ == "__main__":
    if not os.getenv("ADMIN_PASSWORD") or not os.getenv("OPERATOR_PASSWORD"):
        print("[WARN] Hay dat ADMIN_PASSWORD va OPERATOR_PASSWORD qua environment truoc khi chay.")
    mqtt_start()
    app.run(host="0.0.0.0", port=5000, debug=False)
