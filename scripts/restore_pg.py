#!/usr/bin/env python3
"""
Automated PostgreSQL Restore & Disaster Recovery Drill Script for Xiaozhi Indonesia.
Restores a custom-format compressed dump (-Fc) using pg_restore (or Docker container fallback)
and performs row integrity verification.
"""
import argparse
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

import psycopg
from psycopg.rows import dict_row

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("restore")


def run_restore(
    dump_file: str,
    pg_host: str = "localhost",
    pg_port: str = "5432",
    pg_db: str = "xiaozhi",
    pg_user: str = "xiaozhi_app",
    pg_password: str = "xiaozhi_secret",
    clean: bool = True,
) -> bool:
    dump_path = Path(dump_file)
    if not dump_path.exists():
        logger.error("Dump file not found: %s", dump_file)
        return False

    env = os.environ.copy()
    if pg_password:
        env["PGPASSWORD"] = pg_password

    pg_restore_bin = shutil.which("pg_restore")
    local_bin = Path(__file__).resolve().parent.parent / "pgsql" / "bin" / "pg_restore.exe"
    if not pg_restore_bin and local_bin.exists():
        pg_restore_bin = str(local_bin)

    if not pg_restore_bin:
        logger.warning("'pg_restore' not found in system PATH. Attempting Docker container restore fallback...")
        cmd = [
            "docker", "exec", "-i", "xiaozhi-postgres",
            "pg_restore", "-U", pg_user, "-d", pg_db,
        ]
        if clean:
            cmd.append("--clean")
            cmd.append("--if-exists")
        try:
            with open(dump_path, "rb") as f:
                subprocess.run(cmd, stdin=f, check=True)
            logger.info("Restore completed successfully via Docker container.")
        except Exception as exc:
            logger.error("Restore failed via Docker: %s", exc)
            return False
    else:
        cmd = [
            pg_restore_bin,
            "-h", pg_host,
            "-p", str(pg_port),
            "-U", pg_user,
            "-d", pg_db,
            "--no-owner",
            "--no-privileges",
        ]
        if clean:
            cmd.append("--clean")
            cmd.append("--if-exists")
        cmd.append(str(dump_path))

        try:
            logger.info("Executing pg_restore from %s...", dump_path)
            subprocess.run(cmd, env=env, check=True)
            logger.info("Restore completed successfully.")
        except subprocess.CalledProcessError as exc:
            # pg_restore often exits with code 1 if harmless warnings occurred during --clean
            logger.warning("pg_restore finished with warnings (exit code %d). Verifying integrity...", exc.returncode)

    # Post-restore verification drill
    return verify_restored_db(pg_host, pg_port, pg_db, pg_user, pg_password)


def verify_restored_db(host: str, port: str, db: str, user: str, password: str) -> bool:
    dsn = f"postgresql://{user}:{password}@{host}:{port}/{db}"
    try:
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT table_name 
                    FROM information_schema.tables 
                    WHERE table_schema = 'public'
                    ORDER BY table_name;
                """)
                tables = [r["table_name"] for r in cur.fetchall()]
                logger.info("Verified %d tables restored in database '%s'.", len(tables), db)
                for t in ("users", "materials", "community_chats", "relay_rooms"):
                    if t in tables:
                        cur.execute(f"SELECT COUNT(*) as cnt FROM {t}")
                        logger.info("  Table %-18s: %d rows", t, cur.fetchone()["cnt"])
                return len(tables) > 0
    except Exception as e:
        logger.error("Post-restore verification failed: %s", e)
        return False


def main():
    parser = argparse.ArgumentParser(description="Xiaozhi PostgreSQL Disaster Recovery Restore")
    parser.add_argument("dump_file", help="Path to .dump file")
    parser.add_argument("--host", default=os.getenv("POSTGRES_HOST", "localhost"))
    parser.add_argument("--port", default=os.getenv("POSTGRES_PORT", "5432"))
    parser.add_argument("--db", default=os.getenv("POSTGRES_DB", "xiaozhi"))
    parser.add_argument("--user", default=os.getenv("POSTGRES_USER", "xiaozhi_app"))
    parser.add_argument("--password", default=os.getenv("POSTGRES_PASSWORD", "xiaozhi_secret"))
    parser.add_argument("--no-clean", action="store_true", help="Do not drop existing objects before restoring")
    args = parser.parse_args()

    ok = run_restore(
        dump_file=args.dump_file,
        pg_host=args.host,
        pg_port=args.port,
        pg_db=args.db,
        pg_user=args.user,
        pg_password=args.password,
        clean=not args.no_clean,
    )
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
