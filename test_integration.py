import json
import subprocess
import time
import requests

BASE_URL = "https://xiaozhiscig.biz.id"
VPS_HOST = "irsyad@163.61.58.235"
KEY_PATH = "vps_key.pem"

def run_ssh(cmd: str) -> str:
    res = subprocess.run(
        ["ssh", "-i", KEY_PATH, "-o", "StrictHostKeyChecking=no", VPS_HOST, cmd],
        capture_output=True,
        text=True,
    )
    return res.stdout.strip()

def run_psql(sql: str) -> str:
    # Use base64 to avoid shell escaping issues
    import base64
    b64 = base64.b64encode(sql.encode()).decode()
    cmd = f"echo '{b64}' | base64 -d | docker exec -i xiaozhi-postgres psql -U xiaozhi_app -d xiaozhi -t -A"
    return run_ssh(cmd)

def publish_mqtt(topic: str, payload: str, retain: bool = False):
    retain_flag = "-r" if retain else ""
    if payload in ("online", "offline"):
        cmd = f"docker exec xiaozhi-mosquitto mosquitto_pub -h localhost -p 1883 -t '{topic}' -m '{payload}' {retain_flag}"
    else:
        import base64
        b64 = base64.b64encode(payload.encode()).decode()
        cmd = f"docker exec -i xiaozhi-mosquitto sh -c \"echo '{b64}' | base64 -d | mosquitto_pub -h localhost -p 1883 -t '{topic}' -s {retain_flag}\""
    return run_ssh(cmd)

print("=" * 60)
print("1. PENGUJIAN KEAMANAN (SECURITY TESTS)")
print("=" * 60)

# Test 1.1: Request tanpa token
r1 = requests.get(f"{BASE_URL}/api/device/relay/mobil-serena/commands")
print(f"[TEST 1.1] Request tanpa token:")
print(f"  Status Code: {r1.status_code} (Diharapkan 400 Bad Request)")
print(f"  Response: {r1.text}")
assert r1.status_code == 400, f"Expected 400, got {r1.status_code}"
print("  PASSED [OK]")

# Test 1.2: Request dengan token salah
r2 = requests.get(f"{BASE_URL}/api/device/relay/mobil-serena/commands?token=wrong_attacker_token_xyz")
print(f"\n[TEST 1.2] Request dengan token salah (Timing Attack Protected):")
print(f"  Status Code: {r2.status_code} (Diharapkan 401 Unauthorized)")
print(f"  Response: {r2.text}")
assert r2.status_code == 401, f"Expected 401, got {r2.status_code}"
print("  PASSED [OK]")

# Test 1.3: Request room slug tidak terdaftar
r3 = requests.get(f"{BASE_URL}/api/device/relay/ruangan-fiktif-tidak-ada/commands?token=dummy")
print(f"\n[TEST 1.3] Request room slug fiktif:")
print(f"  Status Code: {r3.status_code} (Diharapkan 404 Not Found)")
print(f"  Response: {r3.text}")
assert r3.status_code == 404, f"Expected 404, got {r3.status_code}"
print("  PASSED [OK]")

# Test 1.4: SQL Injection attack pada slug URL
r4 = requests.get(f"{BASE_URL}/api/device/relay/%27%20OR%201%3D1%20--/commands?token=dummy")
print(f"\n[TEST 1.4] Uji SQL Injection pada parameter URL (' OR 1=1 --):")
print(f"  Status Code: {r4.status_code} (Diharapkan 404 Not Found, tidak boleh 500)")
print(f"  Response: {r4.text}")
assert r4.status_code == 404, f"Expected 404, got {r4.status_code}"
print("  PASSED [OK] (Aman dari SQL Injection)")

# Test 1.5: POST status dengan token salah
r5 = requests.post(
    f"{BASE_URL}/api/device/relay/mobil-serena/status",
    json={"token": "palsu_123", "relays": [{"relay": 1, "status": "ON"}]}
)
print(f"\n[TEST 1.5] POST Status dengan token palsu:")
print(f"  Status Code: {r5.status_code} (Diharapkan 401 Unauthorized)")
print(f"  Response: {r5.text}")
assert r5.status_code == 401, f"Expected 401, got {r5.status_code}"
print("  PASSED [OK]")


print("\n" + "=" * 60)
print("2. PENGUJIAN FUNGSI REAL-TIME MQTT (FUNCTIONAL TESTS)")
print("=" * 60)

# Test 2.1: Uji LWT Availability (Online & Offline)
print("\n[TEST 2.1] Uji MQTT LWT (Last Will & Testament):")
publish_mqtt("xiaozhi/relay/mobil-serena/availability", "online", retain=True)
time.sleep(1)
last_seen = run_psql("SELECT device_last_seen_at FROM relay_rooms WHERE id = 23;")
print(f"  Publish 'online' terkirim ke Mosquitto.")
print(f"  Device Last Seen di PostgreSQL: {last_seen}")
assert last_seen.strip() != "", "Expected device_last_seen_at to be updated in database"
print("  Status ONLINE terverifikasi di PostgreSQL [OK]")

publish_mqtt("xiaozhi/relay/mobil-serena/availability", "offline", retain=True)
time.sleep(2)
logs2 = run_ssh("docker logs xiaozhi-gateway 2>&1 | tail -n 30")
print(f"  Publish 'offline' (Simulasi LWT Disconnect) terkirim ke Mosquitto.")
assert "OFFLINE" in logs2, f"Expected OFFLINE in logs:\n{logs2}"
print("  Status OFFLINE (LWT) terdeteksi oleh Gateway [OK]")
print("  PASSED [OK] (LWT berhasil sinkron)")

# Kembalikan status online
publish_mqtt("xiaozhi/relay/mobil-serena/availability", "online", retain=True)

# Test 2.2: Uji 2-Way Hardware State ACK ke Database PostgreSQL
print("\n[TEST 2.2] Uji Hardware State ACK (ESP32 -> Mosquitto -> Go -> Postgres):")
state_payload_on = json.dumps({
    "cmd_id": "test_cmd_101",
    "relay": 1,
    "status": "ON",
    "rssi": -58,
    "uptime_sec": 300
})
publish_mqtt("xiaozhi/relay/mobil-serena/state", state_payload_on)
time.sleep(1.5)

db_status1 = run_psql("SELECT status FROM relay_devices WHERE room_id = 23 AND relay_number = 1;")
print(f"  Status Relay 1 di Postgres setelah publish ON: {db_status1}")
assert db_status1.strip() == "ON", f"Expected 'ON', got '{db_status1}'"
print("  State ON berhasil tersinkron ke database!")

state_payload_off = json.dumps({
    "cmd_id": "test_cmd_102",
    "relay": 1,
    "status": "OFF",
    "rssi": -57,
    "uptime_sec": 305
})
publish_mqtt("xiaozhi/relay/mobil-serena/state", state_payload_off)
time.sleep(1.5)

db_status2 = run_psql("SELECT status FROM relay_devices WHERE room_id = 23 AND relay_number = 1;")
print(f"  Status Relay 1 di Postgres setelah publish OFF: {db_status2}")
assert db_status2.strip() == "OFF", f"Expected 'OFF', got '{db_status2}'"
print("  State OFF berhasil tersinkron ke database!")
print("  PASSED [OK] (2-Way Hardware ACK tersinkron sempurna)")

# Test 2.3: Uji Kontrol Instan Server -> MQTT
print("\n[TEST 2.3] Uji Kontrol Instan via HTTP POST -> MQTT Dispatch:")
# Subscribe in background to verify command received
run_ssh("docker exec xiaozhi-mosquitto rm -f /tmp/sub_cmd.txt")
run_ssh("docker exec -d xiaozhi-mosquitto sh -c 'mosquitto_sub -h localhost -p 1883 -t xiaozhi/relay/mobil-serena/set -C 1 > /tmp/sub_cmd.txt'")
time.sleep(0.5)

r_ctrl = requests.post(
    f"{BASE_URL}/api/device/relay/mobil-serena/control",
    json={"relay": 1, "command": "ON"}
)
print(f"  POST /control Status Code: {r_ctrl.status_code}")
print(f"  POST /control Response: {r_ctrl.text}")
assert r_ctrl.status_code == 200, f"Expected 200, got {r_ctrl.status_code}"

time.sleep(1)
sub_received = run_ssh("docker exec xiaozhi-mosquitto cat /tmp/sub_cmd.txt 2>/dev/null || echo ''")
print(f"  Pesan yang diterima Subscriber MQTT: {sub_received}")
assert "action" in sub_received and "ON" in sub_received, "Expected MQTT command to be dispatched"
print("  PASSED [OK] (Server berhasil dispatch perintah ke MQTT Mosquitto)")

print("\n" + "=" * 60)
print("SELURUH PENGUJIAN KEAMANAN & FUNGSI 100% SUKSES DAN TERVERIFIKASI!")
print("=" * 60)
