import json
import os
import secrets
from time import time
from secrets import token_hex

# Patch the hardcoded deviceId before any midea_beautiful imports
# The fixed default causes Midea's session limit (error 65027) to trigger
import midea_beautiful.cloud as _mba_cloud
_mba_cloud.CLOUD_API_DEVICE_ID = secrets.token_hex(8)

import requests
from flask import Flask, jsonify
from midea_beautiful import connect_to_cloud
from midea_beautiful.appliance import DehumidifierAppliance
from midea_beautiful.cloud import _decode_from_csv, _encode_as_csv

ACCOUNT = os.environ["MIDEA_ACCOUNT"]
PASSWORD = os.environ["MIDEA_PASSWORD"]
PORT = int(os.environ.get("PORT", "8099"))
CACHE_FILE = os.environ.get("CACHE_FILE", "/app/data/appliance.json")

app = Flask(__name__)
_cloud = None
_appliance_config = None


def _get_cloud():
    global _cloud
    if _cloud is None:
        _cloud = connect_to_cloud(
            account=ACCOUNT,
            password=PASSWORD,
            appname="MSmartHome",
        )
    return _cloud


def _discover_appliance(cloud) -> dict:
    """Fetch appliance list from cloud and return the first dehumidifier found."""
    raw = cloud.api_request("/v1/appliance/user/list/get", {})
    appliances = raw.get("list", [])
    if not appliances:
        raise RuntimeError("No appliances found on this account")

    # Prefer a dehumidifier (type 0xA1), otherwise take the first appliance
    match = next(
        (a for a in appliances if a.get("type", "").lower() == "0xa1"),
        appliances[0],
    )
    return {
        "appliance_id": match["id"],
        "homegroup_id": match.get("homegroupId", ""),
        "name": match.get("name", ""),
        "type": match.get("type", ""),
    }


def _get_appliance_config() -> dict:
    global _appliance_config
    if _appliance_config is not None:
        return _appliance_config

    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE) as f:
            _appliance_config = json.load(f)
        print(f"Loaded appliance config from {CACHE_FILE}: {_appliance_config}")
        return _appliance_config

    print("No cached appliance config found — discovering from cloud...")
    cloud = _get_cloud()
    _appliance_config = _discover_appliance(cloud)
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE, "w") as f:
        json.dump(_appliance_config, f, indent=2)
    print(f"Discovered and cached appliance config: {_appliance_config}")
    return _appliance_config


def _transparent_send(cmd_bytes: bytes) -> bytes:
    cloud = _get_cloud()
    cfg = _get_appliance_config()
    user_id = int(cloud._session["userId"])

    encoded = _encode_as_csv(cmd_bytes)
    order = cloud._security.aes_encrypt_string(encoded)

    instant = str(int(time()))
    body = {
        "appId": 1010,
        "format": 2,
        "clientType": 1,
        "language": "en_US",
        "src": 1010,
        "stamp": instant,
        "timestamp": True,
        "deviceId": _mba_cloud.CLOUD_API_DEVICE_ID,
        "reqId": token_hex(16),
        "uid": cloud._uid,
        "userId": user_id,
        "order": order,
        "funId": "0000",
        "isFull": False,
        "applianceCode": cfg["appliance_id"],
        "homegroupId": cfg["homegroup_id"],
        "waitResp": True,
    }
    payload = json.dumps(body)
    sign = cloud._security.sign_proxied(None, data=payload, random=instant)
    headers = {
        "x-recipe-app": "1010",
        "Authorization": cloud._proxied_auth,
        "sign": sign,
        "secretVersion": "1",
        "random": instant,
        "version": "2.22.0",
        "systemVersion": "8.1.0",
        "platform": "0",
        "Accept-Encoding": "identity",
        "Content-Type": "application/json",
        "uid": cloud._uid,
        "accessToken": cloud._header_access_token,
    }
    url = cloud._api_url + "/v1/appliance/transparent/send"
    resp = requests.post(url, data=payload, headers=headers, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if str(data.get("code")) != "0":
        raise RuntimeError(f"Cloud error {data.get('code')}: {data.get('msg')}")
    decrypted = cloud._security.aes_decrypt_string(data["data"]["reply"])
    return _decode_from_csv(decrypted)


def fetch_status() -> dict:
    global _cloud
    cfg = _get_appliance_config()
    appliance = DehumidifierAppliance(cfg["appliance_id"])
    cmd_bytes = appliance.refresh_command().finalize()
    try:
        raw = _transparent_send(cmd_bytes)
    except Exception:
        # Session may have expired — re-authenticate once and retry
        _cloud = None
        raw = _transparent_send(cmd_bytes)

    appliance.process_response(raw[10:])
    return {
        "running": appliance.running,
        "current_humidity": appliance.current_humidity,
        "current_temperature": appliance.current_temperature,
        "target_humidity": appliance.target_humidity,
        "fan_speed": appliance.fan_speed,
        "mode": appliance.mode,
        "tank_full": appliance.tank_full,
        "tank_level": appliance.tank_level,
        "defrosting": appliance.defrosting,
        "filter_indicator": appliance.filter_indicator,
        "ion_mode": appliance.ion_mode,
        "pump": appliance.pump,
        "sleep_mode": appliance.sleep_mode,
        "error_code": appliance.error_code,
    }


@app.route("/status")
def status():
    try:
        return jsonify(fetch_status())
    except Exception as e:
        return jsonify({"error": str(e)}), 503


@app.route("/health")
def health():
    return jsonify({"ok": True})


if __name__ == "__main__":
    _get_cloud()
    _get_appliance_config()
    app.run(host="0.0.0.0", port=PORT)
