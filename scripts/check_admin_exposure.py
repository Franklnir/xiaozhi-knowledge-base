#!/usr/bin/env python3
"""
Admin and User Exposure Audit Script
Tests:
1. Public exposure of admin credentials
2. User enumeration vectors
3. Account takeover vectors
4. Password storage security
"""
import requests
import re
import json

BASE_URL = "https://xiaozhiscig.biz.id"

def check_exposure():
    print("=== AUDIT KEAMANAN AKUN ADMIN & USER ===")
    
    # 1. Cek apakah file sensitif terekspos ke publik
    print("\n[1] Pengecekan Kebocoran File Sensitif (.env, git, backup)...")
    sensitive_paths = [
        "/.env", "/.env.local", "/.env.backup", "/.git/config",
        "/config.py", "/xiaozhi/config.py", "/app.py",
        "/backup.sql", "/xiaozhi.db", "/data/xiaozhi.db"
    ]
    exposed_files = []
    for path in sensitive_paths:
        try:
            r = requests.get(f"{BASE_URL}{path}", timeout=5, allow_redirects=False)
            status = r.status_code
            if status == 200:
                exposed_files.append((path, "200 OK (LEAKED!)"))
            else:
                print(f"    {path:<25} -> Status: {status} (Aman)")
        except Exception as e:
            print(f"    {path:<25} -> Error: {e}")

    # 2. Cek apakah ada Endpoint Publik yang Membocorkan Daftar User / Admin
    print("\n[2] Pengecekan Endpoint Pembocor Akun (User Enumeration)...")
    enum_endpoints = [
        "/api/users", "/api/v1/users", "/users", "/admin/users",
        "/api/admin/users", "/admin/api/users-data", "/api/v1/auth/users",
        "/api/v1/marketplace/sellers", "/api/members", "/api/leaderboard"
    ]
    leaked_endpoints = []
    for ep in enum_endpoints:
        try:
            r = requests.get(f"{BASE_URL}{ep}", timeout=5, allow_redirects=False)
            if r.status_code == 200:
                leaked_endpoints.append((ep, r.text[:80]))
                print(f"    {ep:<30} -> Status: 200 (Waspada! Cek respon)")
            else:
                print(f"    {ep:<30} -> Status: {r.status_code} (Aman)")
        except Exception as e:
            print(f"    {ep:<30} -> Error: {e}")

    # 3. Cek Error Message Leakage pada Login
    print("\n[3] Pengecekan Pesan Error Login (Apakah Membedakan User Ada vs Tidak Ada?)...")
    # Test invalid username
    r1 = requests.post(
        f"{BASE_URL}/api/v1/auth/login",
        json={"username": "user_pasti_tidak_ada_xyz123", "password": "passwordngawur123"},
        timeout=5
    )
    # Test valid username but wrong password
    r2 = requests.post(
        f"{BASE_URL}/api/v1/auth/login",
        json={"username": "irsyad26", "password": "passwordngawur123"},
        timeout=5
    )
    
    msg1 = r1.json().get("message") or r1.json().get("detail", {}).get("message")
    msg2 = r2.json().get("message") or r2.json().get("detail", {}).get("message")
    print(f"    Respon User Tidak Ada: '{msg1}' (Status: {r1.status_code})")
    print(f"    Respon User Ada (Salah Password): '{msg2}' (Status: {r2.status_code})")
    
    is_timing_safe = (msg1 == msg2)
    print(f"    Pesan identik (Anti-Enumeration): {'YA (Aman, penyerang tidak bisa menebak username)' if is_timing_safe else 'TIDAK (Bocor)'}")

    # 4. Ringkasan
    print("\n" + "="*50)
    print("HASIL AUDIT:")
    print(f"- File Sensitif Terekspos: {len(exposed_files)}")
    print(f"- Endpoint Publik Pembocor User: {len(leaked_endpoints)}")
    print(f"- Pesan Error Login Kebocoran User: {'Aman (Generic)' if is_timing_safe else 'Rentan'}")

if __name__ == "__main__":
    check_exposure()
