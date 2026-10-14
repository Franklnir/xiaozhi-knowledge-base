#!/usr/bin/env python3
"""
Audit Log Analysis Script for EduSmart Xiaozhi VPS (163.61.58.235)
Analyzes Nginx access/error logs and system logs for the past 7 days (Oct 7 - Oct 14, 2026).
"""
import glob
import gzip
import re
import os
import json
from collections import Counter, defaultdict
from datetime import datetime

LOG_DIR = "/logs"
TARGET_DATES = [
    "07/Oct/2026",
    "08/Oct/2026",
    "09/Oct/2026",
    "10/Oct/2026",
    "11/Oct/2026",
    "12/Oct/2026",
    "13/Oct/2026",
    "14/Oct/2026",
]

def parse_logs():
    log_files = sorted(glob.glob(os.path.join(LOG_DIR, "access.log*")))
    print(f"[*] Found {len(log_files)} access log files in {LOG_DIR}")

    # Metrics containers
    daily_requests = Counter()
    daily_status = defaultdict(Counter)
    daily_bytes = Counter()
    daily_unique_ips = defaultdict(set)
    daily_endpoints = defaultdict(Counter)
    daily_user_agents = defaultdict(Counter)
    daily_ip_reqs = defaultdict(Counter)
    
    # Global aggregates for the 7-day period
    global_status = Counter()
    global_endpoints = Counter()
    global_ips = Counter()
    global_user_agents = Counter()
    global_categories = Counter()
    hourly_distribution = Counter()
    
    # Regex for standard combined log format
    # 101.255.119.67 - - [14/Oct/2026:00:54:40 +0700] "GET /api/community/chat/unread-count HTTP/1.1" 200 33 "referer" "UA" "forwarded"
    log_pattern = re.compile(
        r'^(?P<ip>\S+) \S+ \S+ \[(?P<date>\d{2}/[A-Za-z]{3}/\d{4}):(?P<time>\d{2}:\d{2}:\d{2}) [^\]]+\] '
        r'"(?P<method>\S+)\s+(?P<path>[^\s\?]+)(?:\?[^\s"]*)?\s+[A-Z0-9/\.]+" '
        r'(?P<status>\d{3}) (?P<bytes>\d+) "(?P<referer>[^"]*)" "(?P<ua>[^"]*)"'
    )

    total_lines = 0
    matched_lines = 0

    for filepath in log_files:
        opener = gzip.open if filepath.endswith(".gz") else open
        try:
            with opener(filepath, "rt", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    total_lines += 1
                    m = log_pattern.match(line.strip())
                    if not m:
                        # Fallback simple search
                        d_match = re.search(r'\[(\d{2}/[A-Za-z]{3}/\d{4}):(\d{2}):', line)
                        if d_match:
                            d_str = d_match.group(1)
                            if d_str in TARGET_DATES:
                                daily_requests[d_str] += 1
                        continue
                    
                    matched_lines += 1
                    data = m.groupdict()
                    d_str = data["date"]
                    
                    if d_str not in TARGET_DATES:
                        continue
                    
                    hour = data["time"].split(":")[0]
                    status = int(data["status"])
                    b_sent = int(data["bytes"])
                    path = data["path"]
                    ip = data["ip"]
                    ua = data["ua"]
                    
                    daily_requests[d_str] += 1
                    status_group = f"{status // 100}xx"
                    daily_status[d_str][status_group] += 1
                    daily_status[d_str][status] += 1
                    daily_bytes[d_str] += b_sent
                    daily_unique_ips[d_str].add(ip)
                    daily_endpoints[d_str][path] += 1
                    daily_ip_reqs[d_str][ip] += 1
                    
                    global_status[status] += 1
                    global_endpoints[path] += 1
                    global_ips[ip] += 1
                    hourly_distribution[f"{d_str} {hour}:00"] += 1
                    
                    # User Agent classification
                    ua_lower = ua.lower()
                    if "esp32" in ua_lower or "esp-http-client" in ua_lower or "audio" in ua_lower and "esp" in ua_lower:
                        cat = "ESP32 IoT Client"
                    elif any(bot in ua_lower for bot in ["bot", "spider", "crawl", "slurp", "googlebot", "bingbot", "yandex"]):
                        cat = "Search Engine Bot"
                    elif any(sec in ua_lower for sec in ["scan", "nikto", "sqlmap", "nmap", "zgrab", "nuclei", "python-requests", "curl"]):
                        cat = "Automated Scanner / Script"
                    elif "mobile" in ua_lower or "android" in ua_lower or "iphone" in ua_lower:
                        cat = "Mobile Browser"
                    else:
                        cat = "Desktop Web Browser"
                    
                    daily_user_agents[d_str][cat] += 1
                    global_categories[cat] += 1
                    
        except Exception as e:
            print(f"[!] Error processing {filepath}: {e}")

    # Process Error logs
    error_files = sorted(glob.glob(os.path.join(LOG_DIR, "error.log*")))
    daily_errors = Counter()
    error_types = Counter()
    for ef in error_files:
        opener = gzip.open if ef.endswith(".gz") else open
        try:
            with opener(ef, "rt", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    # 2026/10/14 07:25:01 [error] ...
                    em = re.search(r'^(\d{4}/\d{2}/\d{2}) \S+ \[(\w+)\] (.+)$', line)
                    if em:
                        dt_iso, lvl, msg = em.groups()
                        # convert 2026/10/14 to 14/Oct/2026
                        try:
                            d_obj = datetime.strptime(dt_iso, "%Y/%m/%d")
                            d_target = d_obj.strftime("%d/%b/%Y")
                            if d_target in TARGET_DATES:
                                daily_errors[d_target] += 1
                                # categorize error
                                if "client intended to send too large body" in msg:
                                    error_types["Payload Too Large (413)"] += 1
                                elif "limiting requests" in msg:
                                    error_types["Rate Limit Exceeded (503)"] += 1
                                elif "connect() failed" in msg or "Connection refused" in msg:
                                    error_types["Upstream Connect Refused"] += 1
                                elif "SSL" in msg:
                                    error_types["SSL/TLS Handshake Error"] += 1
                                else:
                                    error_types[msg[:60]] += 1
                        except:
                            pass
        except Exception as e:
            print(f"[!] Error reading error log {ef}: {e}")

    # Assemble report object
    report = {
        "summary": {
            "period": "07/Oct/2026 - 14/Oct/2026 (7 Days)",
            "total_requests": sum(daily_requests.values()),
            "total_bandwidth_bytes": sum(daily_bytes.values()),
            "total_bandwidth_mb": round(sum(daily_bytes.values()) / (1024 * 1024), 2),
            "total_bandwidth_gb": round(sum(daily_bytes.values()) / (1024 * 1024 * 1024), 2),
            "client_categories": dict(global_categories),
        },
        "daily_breakdown": [],
        "top_endpoints": global_endpoints.most_common(25),
        "status_distribution": dict(global_status),
        "error_distribution": dict(error_types),
        "daily_errors": dict(daily_errors),
        "top_ips": global_ips.most_common(15)
    }

    for d in TARGET_DATES:
        reqs = daily_requests[d]
        b = daily_bytes[d]
        u_ips = len(daily_unique_ips[d])
        s_dict = dict(daily_status[d])
        c_dict = dict(daily_user_agents[d])
        report["daily_breakdown"].append({
            "date": d,
            "requests": reqs,
            "unique_ips": u_ips,
            "bandwidth_mb": round(b / (1024 * 1024), 2),
            "status_2xx": s_dict.get("2xx", 0),
            "status_3xx": s_dict.get("3xx", 0),
            "status_4xx": s_dict.get("4xx", 0),
            "status_5xx": s_dict.get("5xx", 0),
            "errors_in_error_log": daily_errors.get(d, 0),
            "categories": c_dict,
            "top_endpoints": daily_endpoints[d].most_common(5)
        })

    print(json.dumps(report, indent=2))
    with open("/tmp/audit_report.json", "w") as out:
        json.dump(report, out, indent=2)

if __name__ == "__main__":
    parse_logs()
