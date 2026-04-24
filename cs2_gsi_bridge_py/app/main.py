import json
import os
import threading
from typing import Any
from flask import Flask, request, jsonify
import paho.mqtt.client as mqtt

OPTIONS_PATH = "/data/options.json"

def load_options() -> dict[str, Any]:
    defaults = {
        "mqtt_host": "core-mosquitto",
        "mqtt_port": 1883,
        "mqtt_username": "",
        "mqtt_password": "",
        "mqtt_base_topic": "cs2_bridge",
        "discovery_prefix": "homeassistant",
        "publish_discovery": True,
    }
    try:
        with open(OPTIONS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            defaults.update(data)
    except FileNotFoundError:
        print("No /data/options.json found, using defaults", flush=True)
    except Exception as e:
        print(f"Failed reading options: {e}", flush=True)
    return defaults

OPTIONS = load_options()
BASE = OPTIONS["mqtt_base_topic"].rstrip("/")
DISCOVERY = OPTIONS["discovery_prefix"].rstrip("/")
DEVICE_ID = "cs2_gsi_bridge_py"
DEVICE_NAME = "CS2 GSI Bridge"

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
if OPTIONS.get("mqtt_username"):
    client.username_pw_set(OPTIONS.get("mqtt_username"), OPTIONS.get("mqtt_password", ""))

_connected = False

def on_connect(c, userdata, flags, reason_code, properties=None):
    global _connected
    _connected = reason_code == 0
    print(f"MQTT connected: {reason_code}", flush=True)
    if _connected and OPTIONS.get("publish_discovery", True):
        publish_discovery()

def on_disconnect(c, userdata, flags, reason_code, properties=None):
    global _connected
    _connected = False
    print(f"MQTT disconnected: {reason_code}", flush=True)

client.on_connect = on_connect
client.on_disconnect = on_disconnect


def mqtt_loop():
    while True:
        try:
            client.connect(OPTIONS["mqtt_host"], int(OPTIONS["mqtt_port"]), 60)
            client.loop_forever(retry_first_connection=True)
        except Exception as e:
            print(f"MQTT loop error: {e}", flush=True)
            import time
            time.sleep(5)


def publish(topic: str, payload: Any, retain: bool = False):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    client.publish(topic, text, qos=1, retain=retain)


def publish_sensor(object_id: str, name: str, state_topic: str, icon: str, unit: str | None = None):
    payload = {
        "name": name,
        "unique_id": f"{DEVICE_ID}_{object_id}",
        "state_topic": state_topic,
        "icon": icon,
        "device": {
            "identifiers": [DEVICE_ID],
            "name": DEVICE_NAME,
            "manufacturer": "tullhao",
            "model": "CS2 GSI MQTT Bridge (Python)",
        },
    }
    if unit:
        payload["unit_of_measurement"] = unit
    publish(f"{DISCOVERY}/sensor/{DEVICE_ID}/{object_id}/config", payload, retain=True)


def publish_discovery():
    publish_sensor("bomb_state", "Bomb State", f"{BASE}/bomb/state", "mdi:bomb")
    publish_sensor("bomb_countdown", "Bomb Countdown", f"{BASE}/bomb/countdown", "mdi:timer-outline", "s")
    publish_sensor("round_phase", "Round Phase", f"{BASE}/round/phase", "mdi:flag-outline")
    publish_sensor("phase_name", "Phase Name", f"{BASE}/phase/name", "mdi:timeline-clock")
    publish_sensor("phase_time_left", "Phase Time Left", f"{BASE}/phase/time_left", "mdi:timer-sand", "s")
    publish_sensor("blink_interval_ms", "Blink Interval", f"{BASE}/light/blink_interval_ms", "mdi:lightbulb-auto", "ms")
    publish_sensor("recommended_color", "Recommended Color", f"{BASE}/light/recommended_color", "mdi:palette")


def deep_get(data: dict, *keys, default=None):
    cur = data
    for key in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
        if cur is None:
            return default
    return cur


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def calc_blink_interval_ms(bomb_countdown: float) -> int:
    if bomb_countdown <= 0:
        return 0
    if bomb_countdown > 30:
        return 900
    if bomb_countdown > 20:
        return 700
    if bomb_countdown > 12:
        return 500
    if bomb_countdown > 7:
        return 320
    if bomb_countdown > 3:
        return 200
    return 140

app = Flask(__name__)

@app.post("/")
def ingest_root():
    return ingest()

@app.post("/gsi")
def ingest_gsi():
    return ingest()


def ingest():
    data = request.get_json(silent=True) or {}
    round_phase = deep_get(data, "round", "phase", default="")
    round_bomb = deep_get(data, "round", "bomb", default="")
    phase_name = deep_get(data, "map", "phase", default="")
    phase_time_left = to_float(deep_get(data, "phase_countdowns", "phase_ends_in", default=0))
    bomb_state = deep_get(data, "bomb", "state", default=round_bomb)
    bomb_countdown = to_float(deep_get(data, "bomb", "countdown", default=0))

    recommended_color = "white"
    blink_interval_ms = 0
    if str(bomb_state).lower() == "planted":
        recommended_color = "yellow"
        blink_interval_ms = calc_blink_interval_ms(bomb_countdown)
    elif str(round_phase).lower() == "freezetime" or str(phase_name).lower() == "freezetime":
        recommended_color = "white"

    publish(f"{BASE}/state/raw", data)
    publish(f"{BASE}/round/phase", round_phase)
    publish(f"{BASE}/round/bomb", round_bomb)
    publish(f"{BASE}/phase/name", phase_name)
    publish(f"{BASE}/phase/time_left", phase_time_left)
    publish(f"{BASE}/bomb/state", bomb_state)
    publish(f"{BASE}/bomb/countdown", bomb_countdown)
    publish(f"{BASE}/light/recommended_color", recommended_color)
    publish(f"{BASE}/light/blink_interval_ms", blink_interval_ms)

    return jsonify({"ok": True})

if __name__ == "__main__":
    threading.Thread(target=mqtt_loop, daemon=True).start()
    print("Starting CS2 GSI Bridge (Python) on port 3001", flush=True)
    app.run(host="0.0.0.0", port=3001)
