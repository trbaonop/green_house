"""Node simulator for TH08. TLS verifies broker CA and hostname."""
import argparse, json, random, threading, time
import paho.mqtt.client as mqtt

ap = argparse.ArgumentParser()
ap.add_argument("--host", required=True, help="Broker hostname matching certificate SAN")
ap.add_argument("--port", type=int, default=8883)
ap.add_argument("--user", default="esp32")
ap.add_argument("--password", required=True)
ap.add_argument("--node", default="node01")
ap.add_argument("--ca", required=True, help="CA certificate PEM")
ap.add_argument("--interval", type=float, default=2.0)
a = ap.parse_args()

T_TELE=f"nhom01/{a.node}/telemetry"; T_CMD=f"nhom01/{a.node}/cmd"; T_ACK=f"nhom01/{a.node}/ack"
state={"fsm":"STOPPED","duty":0,"mode":"ok","talking":True,"seq":0}; seen=set(); t0=time.time()
c=mqtt.Client(mqtt.CallbackAPIVersion.VERSION2); c.username_pw_set(a.user,a.password)
c.tls_set(ca_certs=a.ca); c.tls_insecure_set(False)

def ack(cmd_id,result,reason=""):
    c.publish(T_ACK,json.dumps({"cmd_id":cmd_id,"device_id":a.node,"result":result,"reason":reason}),qos=1)
    print("[ACK]",cmd_id[:8],result,reason)

def handle_cmd(d):
    cmd_id=d.get("cmd_id","")
    if d.get("device_id")!=a.node or cmd_id in seen:return
    seen.add(cmd_id)
    if (time.time()-d.get("sent_at",0))*1000>d.get("ttl_ms",10000):
        ack(cmd_id,"rejected","lenh het han");return
    if state["mode"]=="mute": print("[SIM] mute: no ACK");return
    if state["mode"]=="late":time.sleep(8)
    if d.get("action")=="pump_on":
        if state["mode"]=="error" or state["fsm"]=="ERROR":ack(cmd_id,"rejected","node ERROR");return
        state["fsm"],state["duty"]="RUNNING",int(d.get("duty",100));ack(cmd_id,"ok")
    elif d.get("action")=="pump_off":state["fsm"],state["duty"]="STOPPED",0;ack(cmd_id,"ok")
    else:ack(cmd_id,"rejected","action khong ho tro")

def on_connect(cl,ud,flags,rc,props=None):
    print("[MQTT] connected:",rc); cl.subscribe(T_CMD,qos=1)
def on_message(cl,ud,msg):
    try:threading.Thread(target=handle_cmd,args=(json.loads(msg.payload),),daemon=True).start()
    except Exception as e:print("[CMD] invalid:",e)
def telemetry_loop():
    while True:
        time.sleep(a.interval)
        if not state["talking"]:continue
        state["seq"]+=1
        c.publish(T_TELE,json.dumps({"device_id":a.node,"ts":int(time.time()-t0),"seq":state["seq"],
          "temp":round(random.uniform(26,31),1),"hum":round(random.uniform(60,75),1),"rain":0,
          "unit":{"temp":"C","hum":"%","rain":"binary"},"valid":True,"fsm_state":state["fsm"],
          "pump":state["duty"] if state["fsm"]=="RUNNING" else 0}),qos=1)

c.on_connect=on_connect;c.on_message=on_message
c.connect(a.host,a.port);c.loop_start()
threading.Thread(target=telemetry_loop,daemon=True).start()
print("Commands: ok | mute | late | error | silent | talk | quit")
while True:
    cmd=input("> ").strip().lower()
    if cmd=="quit":break
    if cmd in ("ok","mute","late"):state["mode"]=cmd
    elif cmd=="error":state.update(mode="error",fsm="ERROR",duty=0)
    elif cmd=="silent":state["talking"]=False
    elif cmd=="talk":state["talking"]=True
    print("[SIM]",state)
c.loop_stop();c.disconnect()
