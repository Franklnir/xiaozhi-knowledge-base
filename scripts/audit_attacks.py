#!/usr/bin/env python3
"""
Comprehensive Security Attack Audit for EduSmart Xiaozhi VPS
Analyzes Fail2ban, SSH auth logs, and Nginx web server access logs from start to finish.
"""
import glob
import gzip
import os
import re
from collections import Counter, defaultdict
from datetime import datetime

VLOG_DIR = "/vlog"

def analyze_fail2ban():
    print("[*] Menganalisis Log Fail2ban...")
    f2b_files = sorted(glob.glob(os.path.join(VLOG_DIR, "fail2ban.log*")))
    
    total_found = 0
    total_bans = 0
    jail_counts = Counter()
    banned_ips = Counter()
    earliest_date = None
    latest_date = None
    
    # Regex: 2026-10-25 00:31:36,996 fail2ban.filter [116904]: INFO [sshd] Found 64.227.122.123
    # Regex: 2026-10-25 00:31:36,996 fail2ban.actions [116904]: NOTICE [sshd] Ban 64.227.122.123
    found_pattern = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*?\[(\w+)\] Found ([\d\.]+)")
    ban_pattern = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*?\[(\w+)\] Ban ([\d\.]+)")
    
    for fpath in f2b_files:
        opener = gzip.open if fpath.endswith(".gz") else open
        try:
            with opener(fpath, "rt", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    fm = found_pattern.search(line)
                    if fm:
                        dt_str, jail, ip = fm.groups()
                        total_found += 1
                        jail_counts[jail] += 1
                        if not earliest_date or dt_str < earliest_date:
                            earliest_date = dt_str
                        if not latest_date or dt_str > latest_date:
                            latest_date = dt_str
                        continue
                    
                    bm = ban_pattern.search(line)
                    if bm:
                        dt_str, jail, ip = bm.groups()
                        total_bans += 1
                        banned_ips[ip] += 1
                        if not earliest_date or dt_str < earliest_date:
                            earliest_date = dt_str
                        if not latest_date or dt_str > latest_date:
                            latest_date = dt_str
        except Exception as e:
            print(f"[-] Error reading {fpath}: {e}")
            
    return {
        "files_analyzed": len(f2b_files),
        "total_found": total_found,
        "total_bans": total_bans,
        "jail_counts": dict(jail_counts),
        "unique_banned_ips": len(banned_ips),
        "top_banned_ips": banned_ips.most_common(10),
        "earliest_date": earliest_date,
        "latest_date": latest_date,
    }


def analyze_ssh_auth():
    print("[*] Menganalisis Log SSH / Auth...")
    auth_files = sorted(glob.glob(os.path.join(VLOG_DIR, "auth.log*")))
    
    failed_passwords = 0
    invalid_users = 0
    attacker_ips = Counter()
    targeted_usernames = Counter()
    earliest_date = None
    latest_date = None
    
    # Oct 25 00:31:36 vps sshd[...]: Failed password for invalid user admin from 64.227.122.123 port 54321 ssh2
    # Oct 25 00:31:36 vps sshd[...]: Failed password for root from 64.227.122.123 port 54321 ssh2
    # Oct 25 00:31:36 vps sshd[...]: Invalid user test from 64.227.122.123
    failed_pwd_re = re.compile(r"Failed password for (?:invalid user )?(\S+) from ([\d\.]+)")
    invalid_usr_re = re.compile(r"Invalid user (\S+) from ([\d\.]+)")
    
    for fpath in auth_files:
        opener = gzip.open if fpath.endswith(".gz") else open
        try:
            with opener(fpath, "rt", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    m1 = failed_pwd_re.search(line)
                    if m1:
                        user, ip = m1.groups()
                        failed_passwords += 1
                        attacker_ips[ip] += 1
                        targeted_usernames[user] += 1
                    
                    m2 = invalid_usr_re.search(line)
                    if m2:
                        user, ip = m2.groups()
                        invalid_users += 1
                        attacker_ips[ip] += 1
                        targeted_usernames[user] += 1
        except Exception as e:
            print(f"[-] Error reading {fpath}: {e}")
            
    return {
        "files_analyzed": len(auth_files),
        "failed_password_attempts": failed_passwords,
        "invalid_user_probes": invalid_users,
        "unique_attacker_ips": len(attacker_ips),
        "top_attackers": attacker_ips.most_common(10),
        "top_targeted_usernames": targeted_usernames.most_common(10),
    }


def analyze_web_attacks():
    print("[*] Menganalisis Log Web Server Nginx (Attack Scanners & Exploits)...")
    nginx_files = sorted(glob.glob(os.path.join(VLOG_DIR, "nginx", "access.log*")))
    
    # Attack signatures
    ATTACK_SIGNATURES = {
        "config_env_leak": re.compile(r"(\.env|\.git|\.aws|config\.json|\.yml|\.yaml|\.ini|\.sql|\.bak|\.zip|\.tar)", re.I),
        "php_exploit_probe": re.compile(r"(phpmyadmin|pma|wp-login|wp-admin|xmlrpc|setup\.php|info\.php|test\.php|eval-stdin)", re.I),
        "rce_webshell": re.compile(r"(cgi-bin|shell|cmd|phpunit|actuator|solr|telescope|webconsole|console)", re.I),
        "path_traversal": re.compile(r"(\.\./|\.\.\\|\%2e\%2e|\%252e)", re.I),
        "sql_injection": re.compile(r"(union.*select|select.*from|order\s+by|\bexec\b|\bwaitfor\b|information_schema)", re.I),
        "bot_scanner_probes": re.compile(r"(sqlmap|nikto|masscan|zgrab|acunetix|nessus|nmap|morfeus)", re.I),
    }
    
    attack_category_counts = Counter()
    attack_ips = Counter()
    attack_targets = Counter()
    total_web_requests = 0
    total_attacks = 0
    earliest_date = None
    latest_date = None
    
    log_pattern = re.compile(
        r'^(?P<ip>\S+) \S+ \S+ \[(?P<date>\d{2}/[A-Za-z]{3}/\d{4}):(?P<time>\d{2}:\d{2}:\d{2}) [^\]]+\] '
        r'"(?P<method>\S+)\s+(?P<path>[^\s\?]+)(?:\?[^\s"]*)?\s+[A-Z0-9/\.]+" '
        r'(?P<status>\d{3}) (?P<bytes>\d+) "(?P<referer>[^"]*)" "(?P<ua>[^"]*)"'
    )
    
    for fpath in nginx_files:
        opener = gzip.open if fpath.endswith(".gz") else open
        try:
            with opener(fpath, "rt", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    total_web_requests += 1
                    m = log_pattern.match(line.strip())
                    if not m:
                        continue
                    
                    ip = m.group("ip")
                    path = m.group("path")
                    ua = m.group("ua")
                    status = m.group("status")
                    d_str = m.group("date")
                    
                    if not earliest_date:
                        earliest_date = d_str
                    latest_date = d_str
                    
                    # Check attack signatures
                    is_attack = False
                    for cat, sig in ATTACK_SIGNATURES.items():
                        if sig.search(path) or (cat == "bot_scanner_probes" and sig.search(ua)):
                            attack_category_counts[cat] += 1
                            is_attack = True
                    
                    if is_attack:
                        total_attacks += 1
                        attack_ips[ip] += 1
                        attack_targets[path.lower()] += 1
        except Exception as e:
            print(f"[-] Error reading {fpath}: {e}")
            
    return {
        "files_analyzed": len(nginx_files),
        "total_web_requests": total_web_requests,
        "total_web_attacks": total_attacks,
        "category_breakdown": dict(attack_category_counts),
        "top_attack_ips": attack_ips.most_common(10),
        "top_attack_paths": attack_targets.most_common(15),
        "earliest_date": earliest_date,
        "latest_date": latest_date,
    }


def main():
    print("=" * 65)
    print("AUDIT KEAMANAN LENGKAP: TOTAL SERANGAN KE SERVER VPS (DARI AWAL - AKHIR)")
    print("=" * 65)
    
    f2b = analyze_fail2ban()
    ssh = analyze_ssh_auth()
    web = analyze_web_attacks()
    
    total_all_attacks = f2b["total_found"] + ssh["failed_password_attempts"] + web["total_web_attacks"]
    
    import json
    result = {
        "fail2ban": f2b,
        "ssh": ssh,
        "web": web,
        "total_combined_attack_events": total_all_attacks
    }
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    main()
