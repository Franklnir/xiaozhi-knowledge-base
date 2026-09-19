#!/usr/bin/env python3
"""
Automated PostgreSQL Backup & Retention Script for Xiaozhi Indonesia.
Creates compressed timestamped dumps and purges backups older than retention days.
"""
import argparse
import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backup")


def run_backup(
    output_dir: str = "backups",
    retention_days: int = 7,
    pg_host: str = "localhost",
    pg_port: str = "5432",
    pg_db: str = "xiaozhi",
    pg_user: str = "xiaozhi_app",
    pg_password: str = "xiaozhi_secret",
) -> Optional[Path]:
    backup_path = Path(output_dir)
    backup_path.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dump_filename = backup_path / f"xiaozhi_pg_{timestamp}.dump"

    env = os.environ.copy()
    if pg_password:
        env["PGPASSWORD"] = pg_password

    # Check if pg_dump is available (PATH or local pgsql/bin)
    pg_dump_bin = shutil.which("pg_dump")
    local_bin = Path(__file__).resolve().parent.parent / "pgsql" / "bin" / "pg_dump.exe"
    if not pg_dump_bin and local_bin.exists():
        pg_dump_bin = str(local_bin)

    if not pg_dump_bin:
        logger.warning("'pg_dump' not found in system PATH. Attempting Docker container dump fallback...")
        # Try docker exec if running in docker
        cmd = [
            "docker", "exec", "xiaozhi-postgres",
            "pg_dump", "-U", pg_user, "-d", pg_db, "-Fc", "-Z", "6"
        ]
        try:
            with open(dump_filename, "wb") as f:
                res = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE, check=True)
            logger.info("Backup successfully generated via Docker container: %s", dump_filename)
        except Exception as e:
            logger.error("Failed to execute backup via Docker: %s", e)
            return None
    else:
        cmd = [
            pg_dump_bin,
            "-h", pg_host,
            "-p", str(pg_port),
            "-U", pg_user,
            "-d", pg_db,
            "-Fc",  # Custom format (compressed)
            "-Z", "6",  # Compression level
            "-f", str(dump_filename),
        ]
        try:
            logger.info("Executing pg_dump to %s...", dump_filename)
            subprocess.run(cmd, env=env, check=True)
            logger.info("Backup created successfully: %s (Size: %d bytes)", dump_filename, dump_filename.stat().st_size)
        except Exception as exc:
            logger.error("pg_dump failed: %s", exc)
            return None

    # Retention cleanup
    cleanup_old_backups(backup_path, retention_days)
    return dump_filename


def cleanup_old_backups(backup_dir: Path, retention_days: int) -> None:
    cutoff = datetime.now() - timedelta(days=retention_days)
    for p in backup_dir.glob("xiaozhi_pg_*.dump"):
        if p.is_file():
            mtime = datetime.fromtimestamp(p.stat().st_mtime)
            if mtime < cutoff:
                logger.info("Deleting outdated backup: %s", p.name)
                try:
                    p.unlink()
                except Exception as e:
                    logger.warning("Could not delete %s: %s", p.name, e)


def main():
    parser = argparse.ArgumentParser(description="Xiaozhi PostgreSQL Automated Backup")
    parser.add_argument("--output-dir", default=os.getenv("BACKUP_DIR", "backups"))
    parser.add_argument("--retention-days", type=int, default=int(os.getenv("BACKUP_RETENTION_DAYS", "7")))
    parser.add_argument("--host", default=os.getenv("POSTGRES_HOST", "localhost"))
    parser.add_argument("--port", default=os.getenv("POSTGRES_PORT", "5432"))
    parser.add_argument("--db", default=os.getenv("POSTGRES_DB", "xiaozhi"))
    parser.add_argument("--user", default=os.getenv("POSTGRES_USER", "xiaozhi_app"))
    parser.add_argument("--password", default=os.getenv("POSTGRES_PASSWORD", "xiaozhi_secret"))
    args = parser.parse_args()

    result = run_backup(
        output_dir=args.output_dir,
        retention_days=args.retention_days,
        pg_host=args.host,
        pg_port=args.port,
        pg_db=args.db,
        pg_user=args.user,
        pg_password=args.password,
    )
    if not result:
        sys.exit(1)


if __name__ == "__main__":
    main()
