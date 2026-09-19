"""
Automated Downloader, Initializer, and Runner for Portable PostgreSQL 16 on Windows.
Runs fully locally without Docker or WSL dependencies.
"""
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
ZIP_FILE = BASE_DIR / "pgsql_dl.zip"
PGSQL_DIR = BASE_DIR / "pgsql"
DATA_DIR = BASE_DIR / "pgdata_local"
LOG_FILE = BASE_DIR / "pg_server.log"
DOWNLOAD_URL = "https://get.enterprisedb.com/postgresql/postgresql-16.6-1-windows-x64-binaries.zip"


def log(msg):
    print(f"[PostgreSQL Setup] {msg}", flush=True)


def download_with_progress(url: str, dest_path: Path):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    log(f"Starting download from: {url}")
    start = time.time()
    with urllib.request.urlopen(req) as resp, open(dest_path, "wb") as out_file:
        total_size = int(resp.headers.get("content-length", 0))
        downloaded = 0
        chunk_size = 1024 * 1024  # 1MB
        last_logged = 0

        while True:
            chunk = resp.read(chunk_size)
            if not chunk:
                break
            out_file.write(chunk)
            downloaded += len(chunk)
            if downloaded - last_logged >= 25 * 1024 * 1024 or downloaded == total_size:
                pct = (downloaded / total_size * 100) if total_size else 0
                mb = downloaded / (1024 * 1024)
                total_mb = total_size / (1024 * 1024)
                log(f"Downloaded {mb:.1f} MB / {total_mb:.1f} MB ({pct:.1f}%)")
                last_logged = downloaded

    log(f"Download complete in {time.time() - start:.1f}s.")


def main():
    bin_dir = PGSQL_DIR / "bin"
    initdb_exe = bin_dir / "initdb.exe"
    pg_ctl_exe = bin_dir / "pg_ctl.exe"
    psql_exe = bin_dir / "psql.exe"

    # Step 1: Download & Extract
    if not initdb_exe.exists():
        if not ZIP_FILE.exists():
            download_with_progress(DOWNLOAD_URL, ZIP_FILE)

        log("Extracting PostgreSQL binaries to pgsql/...")
        with zipfile.ZipFile(str(ZIP_FILE), 'r') as zf:
            zf.extractall(str(BASE_DIR))
        log("Extraction complete.")

        if ZIP_FILE.exists():
            ZIP_FILE.unlink()
            log("Removed zip file to reclaim disk space.")

    # Step 2: Initialize Cluster
    if not DATA_DIR.exists() or not (DATA_DIR / "PG_VERSION").exists():
        log(f"Initializing cluster in {DATA_DIR}...")
        subprocess.run([
            str(initdb_exe),
            "-D", str(DATA_DIR),
            "-U", "postgres",
            "-A", "trust",
            "-E", "UTF8",
            "--locale=C",
        ], check=True)
        log("Cluster initialized.")

    # Step 3: Start Server
    status_res = subprocess.run([str(pg_ctl_exe), "-D", str(DATA_DIR), "status"], capture_output=True, text=True)
    if "server is running" not in status_res.stdout:
        log("Starting PostgreSQL server on 127.0.0.1:5432...")
        subprocess.run([
            str(pg_ctl_exe),
            "-D", str(DATA_DIR),
            "-l", str(LOG_FILE),
            "-o", "-p 5432 -h 127.0.0.1",
            "start",
        ], check=True)
        time.sleep(2)
        log("PostgreSQL server running successfully!")
    else:
        log("PostgreSQL server is already running.")

    # Step 4: Create Role & Database
    log("Configuring xiaozhi_app role and xiaozhi database...")
    create_role_sql = """
    DO $$
    BEGIN
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'xiaozhi_app') THEN
            CREATE ROLE xiaozhi_app WITH LOGIN PASSWORD 'xiaozhi_secret' SUPERUSER;
        END IF;
    END
    $$;
    """
    subprocess.run([str(psql_exe), "-h", "127.0.0.1", "-U", "postgres", "-p", "5432", "-c", create_role_sql], check=True)

    db_check = subprocess.run(
        [str(psql_exe), "-h", "127.0.0.1", "-U", "postgres", "-p", "5432", "-tAc", "SELECT 1 FROM pg_database WHERE datname='xiaozhi'"],
        capture_output=True, text=True
    )
    if "1" not in db_check.stdout:
        subprocess.run([str(psql_exe), "-h", "127.0.0.1", "-U", "postgres", "-p", "5432", "-c", "CREATE DATABASE xiaozhi OWNER xiaozhi_app;"], check=True)
        log("Database 'xiaozhi' created.")
    else:
        log("Database 'xiaozhi' already exists.")

    # Step 5: Test Connection via psycopg
    log("Testing connection via psycopg...")
    import psycopg
    with psycopg.connect("postgresql://xiaozhi_app:xiaozhi_secret@127.0.0.1:5432/xiaozhi") as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT version();")
            ver = cur.fetchone()[0]
            log(f"Connection test PASSED! Server: {ver}")

    log("Local PostgreSQL is 100% READY and listening on 127.0.0.1:5432!")


if __name__ == "__main__":
    main()
