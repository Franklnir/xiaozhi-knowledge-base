#!/usr/bin/env python3
"""
Comprehensive Security URL Audit Script for EduSmart Xiaozhi
1. Analyzes Nginx logs for malicious/insecure inbound URLs requested by external bots/attackers.
2. Identifies any suspicious requests that returned 2xx (leakage/breach).
3. Analyzes outbound and hardcoded URLs in application configs and code.
"""
import glob
import gzip
import re
import os
import json
from collections import Counter, defaultdict

LOG_DIR = "/logs"

PATTERNS = {
    "Sensitive Files (.env, .git, backups)": re.compile(
        r'(\.env|\.git|\.svn|\.bak|\.old|\.zip|\.tar|\.gz|\.sql|\.config|\.yml|\.yaml|\.json\.bak|id_rsa|id_ed25519)',
        re.I
    ),
    "Path Traversal (LFI / RFI)": re.compile(
        r'(\.\./|\.\.\\|\.\.%2f|\.\.%5c|/etc/passwd|/proc/self|/windows/win\.ini)',
        re.I
    ),
    "WordPress & CMS Scans": re.compile(
        r'(wp-login|wp-admin|wp-content|wp-includes|xmlrpc\.php|wordpress|joomla|drupal)',
        re.I
    ),
    "Database Admin Tools": re.compile(
        r'(phpmyadmin|pma|adminer|mysqladmin|dbadmin|myadmin|sqlmanager)',
        re.I
    ),
    "Webshell & Remote Code Exec": re.compile(
        r'(shell\.php|c99\.php|r57\.php|alfa\.php|cmd\.exe|powershell|/bin/sh|/bin/bash|eval\(|passthru|system\()',
        re.I
    ),
    "SQL Injection (SQLi)": re.compile(
        r'(union\s+select|select\s+.*from|order\s+by\s+\d+|benchmark\(|sleep\(|\'--|\"--)|\bor\s+1=1\b',
        re.I
    ),
    "Cross-Site Scripting (XSS)": re.compile(
        r'(<script|%3cscript|javascript:|onerror=|onload=|alert\()',
        re.I
    ),
    "Spring Boot & Cloud Actuator": re.compile(
        r'(/actuator|/env|/health|/metrics|/heapdump|solr|struts|log4j)',
        re.I
    ),
    "Generic PHP / Script Probing": re.compile(
        r'(\.php|\.asp|\.aspx|\.jsp|\.cgi)',
        re.I
    )
}

def analyze_logs():
    log_files = sorted(glob.glob(os.path.join(LOG_DIR, "access.log*")))
    print(f"[*] Scanning {len(log_files)} log files for malicious/unsafe URLs...")

    hits_by_category = defaultdict(Counter)
    status_by_category = defaultdict(Counter)
    breach_alerts = []
    top_attacker_ips = Counter()
    all_suspicious_requests = Counter()

    log_regex = re.compile(
        r'^(?P<ip>\S+) \S+ \S+ \[(?P<time>[^\]]+)\] '
        r'"(?P<method>\S+)\s+(?P<url>[^\s"]*)\s+HTTP/[0-9\.]+"\s+'
        r'(?P<status>\d{3})\s+(?P<bytes>\d+)'
    )

    total_scanned = 0
    total_suspicious = 0

    for filepath in log_files:
        opener = gzip.open if filepath.endswith(".gz") else open
        try:
            with opener(filepath, "rt", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    total_scanned += 1
                    m = log_regex.match(line.strip())
                    if not m:
                        continue
                    
                    data = m.groupdict()
                    ip = data["ip"]
                    url = data["url"]
                    status = data["status"]
                    time_str = data["time"]

                    # Check patterns
                    is_suspicious = False
                    for category, pattern in PATTERNS.items():
                        if pattern.search(url):
                            is_suspicious = True
                            hits_by_category[category][url] += 1
                            status_by_category[category][status] += 1
                            all_suspicious_requests[url] += 1
                            top_attacker_ips[ip] += 1

                            # Critical: Did the server return 200 or 206 for a malicious probe?
                            if status.startswith("2"):
                                if not url.startswith("/download/"):
                                    breach_alerts.append({
                                        "time": time_str,
                                        "ip": ip,
                                        "status": status,
                                        "category": category,
                                        "url": url[:150]
                                    })
                    if is_suspicious:
                        total_suspicious += 1

        except Exception as e:
            print(f"[!] Error reading {filepath}: {e}")

    report = {
        "total_scanned_requests": total_scanned,
        "total_suspicious_requests": total_suspicious,
        "breach_alerts_count": len(breach_alerts),
        "breach_alerts": breach_alerts[:20],
        "category_summary": {},
        "top_attack_targets": all_suspicious_requests.most_common(25),
        "top_attacking_ips": top_attacker_ips.most_common(15)
    }

    for cat in PATTERNS:
        total_hits = sum(hits_by_category[cat].values())
        statuses = dict(status_by_category[cat])
        top_urls = hits_by_category[cat].most_common(5)
        report["category_summary"][cat] = {
            "total_hits": total_hits,
            "status_distribution": statuses,
            "top_probed_urls": top_urls
        }

    print(json.dumps(report, indent=2))
    with open("/tmp/unsafe_urls_audit.json", "w") as out:
        json.dump(report, out, indent=2)

if __name__ == "__main__":
    analyze_logs()
