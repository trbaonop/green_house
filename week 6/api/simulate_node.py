"""Gia lap node nha kinh tren PC de thu dashboard/API truoc khi co ESP32.

Chay:  python simulate_node.py --host <broker> --user esp32 --password '...'
Lenh go trong luc chay:
  ok      ack thanh cong (mac dinh)
  mute    nhan lenh nhung KHONG gui ack  -> dashboard phai hien "Qua han"
  late    nhan lenh, doi 8 giay roi moi ack -> "Xac nhan muon"
  error   node o trang thai ERROR -> tu choi lenh bat bom (rejected)
  silent  ngung gui telemetry -> dashboard phai hien "du lieu da cu"
  talk    gui telemetry tro lai
  quit
Chuong trinh nay chi de kiem giao dien/API. Ket qua cuoi phai dieu khien co cau that.
"""
import argparse, json, random, threading, time

import paho.mqtt.client as mqtt

ap = argparse.ArgumentParser()
ap.add_argument("--host", default="localhost")
ap.add_argument("--port", type=int, default=1883)
ap.add_argument("--user", default="")
ap.add_argument("--password", default="")
ap.add_argument("--node", default="node01")
ap.add_argument("--interval", type=float, default=2.0)
a = ap.parse_args()

T_TELE = f"nhom01/{a.node}/telemetry"
T_CMD = f"nhom01/{a.node}/cmd"
T_ACK = f"nhom01/{a.node}/ack"

state = {"fsm": "STOPPED", "duty": 0, "mode": "ok", "talking": True, "seq": 0}
seen = set()          # cmd_id da xu ly: khong thuc thi lai khi trung
t0 = time.time()
c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
if a.user:
    c.username_pw_set(a.user, a.password)


def ack(cmd_id, result, reason=""):
    c.publish(T_ACK, json.dumps({"cmd_id": cmd_id, "device_id": a.node,
                                 "result": result, "reason": reason}), qos=1)
    print(f"[ACK] {cmd_id[:8]} {result} {reason}")


def handle_cmd(d):
    cmd_id = d.get("cmd_id", "")
    if d.get("device_id") != a.node:
        return
    if cmd_id in seen:
        print(f"[CMD] {cmd_id[:8]} trung -> bo qua, khong thuc thi lai")
        return
    seen.add(cmd_id)
    age = time.time() - d.get("sent_at", 0)
    if age * 1000 > d.get("ttl_ms", 10000):
        print(f"[CMD] {cmd_id[:8]} qua han hieu luc ({age:.1f}s) -> khong thuc thi")
        ack(cmd_id, "rejected", "lenh het han hieu luc")
        return
    print(f"[CMD] {cmd_id[:8]} {d.get('action')} duty={d.get('duty')}")
    if state["mode"] == "mute":
        print("[SIM] mute: khong gui ack")
        return
    if state["mode"] == "late":
        time.sleep(8)
    if d.get("action") == "pump_on":
        if state["mode"] == "error" or state["fsm"] == "ERROR":
            state["fsm"] = "ERROR"
            ack(cmd_id, "rejected", "node dang o trang thai ERROR")
            return
        state["fsm"], state["duty"] = "RUNNING", int(d.get("duty", 100))
        ack(cmd_id, "ok")
    elif d.get("action") == "pump_off":
        state["fsm"], state["duty"] = "STOPPED", 0
        ack(cmd_id, "ok")
    else:
        ack(cmd_id, "rejected", "action khong ho tro")


def on_connect(cl, ud, flags, rc, props=None):
    print("[MQTT] connected:", rc)
    cl.subscribe(T_CMD, qos=1)


def on_message(cl, ud, msg):
    try:
        d = json.loads(msg.payload)
    except Exception:
        return
    threading.Thread(target=handle_cmd, args=(d,), daemon=True).start()


def telemetry_loop():
    while True:
        time.sleep(a.interval)
        if not state["talking"]:
            continue
        state["seq"] += 1
        t = round(random.uniform(26, 31), 1)
        h = round(random.uniform(60, 75), 1)
        c.publish(T_TELE, json.dumps({
            "device_id": a.node, "ts": int(time.time() - t0), "seq": state["seq"],
            "temp": t, "hum": h, "rain": 0,
            "unit": {"temp": "C", "hum": "%", "rain": "binary"},
            "valid": True, "fsm_state": state["fsm"],
            "pump": state["duty"] if state["fsm"] == "RUNNING" else 0}))


c.on_connect = on_connect
c.on_message = on_message
c.connect(a.host, a.port)
c.loop_start()
threading.Thread(target=telemetry_loop, daemon=True).start()
print(__doc__)
while True:
    w = input("> ").strip().lower()
    if w == "quit":
        break
    if w in ("ok", "mute", "late"):
        state["mode"] = w
        if state["fsm"] == "ERROR":
            state["fsm"] = "STOPPED"
    elif w == "error":
        state["mode"], state["fsm"], state["duty"] = "error", "ERROR", 0
    elif w == "silent":
        state["talking"] = False
    elif w == "talk":
        state["talking"] = True
    print("[SIM] mode =", state["mode"], "| fsm =", state["fsm"], "| talking =", state["talking"])
c.loop_stop()
