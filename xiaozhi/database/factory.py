"""
Store factory for selecting the appropriate database backend.
Supports PostgreSQL (production VPS), HuggingFace (for HF Spaces), and SQLite (for local testing/fallback).
"""
import os
import logging
import xiaozhi.config

logger = logging.getLogger("xiaozhi.factory")


def create_store():
    """
    Create the appropriate store based on environment configuration.

    Environment variables:
        DB_BACKEND: "postgres", "sqlite", or "huggingface" (default: auto-detect)
        DATABASE_URL / POSTGRES_*: PostgreSQL connection parameters
        SQLITE_DB_PATH: Path to SQLite database file (default: data/xiaozhi.db)

    Auto-detection logic:
        - If DATABASE_URL or POSTGRES_DB is set → use PostgresStore
        - If running on HuggingFace Spaces (SPACE_ID env var exists) → use HFJsonStore (fallback SQLite)
        - If HF_TOKEN is set → use HFJsonStore (fallback SQLite)
        - Otherwise → use SQLiteStore
    """
    backend = os.getenv("DB_BACKEND", "").lower()
    is_hf_space = bool(os.getenv("SPACE_ID"))
    has_hf_token = bool(os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACEHUB_API_TOKEN"))

    # Explicit backend selection
    if backend == "postgres":
        logger.info("Using PostgreSQL backend (explicit)")
        from xiaozhi.database.postgres_store import PostgresStore
        return PostgresStore()
    elif backend == "sqlite":
        logger.info("Using SQLite backend (explicit)")
        from xiaozhi.database.sqlite_store import SQLiteStore
        return SQLiteStore()
    elif backend == "huggingface":
        logger.info("Using HuggingFace backend (explicit)")
        from xiaozhi.database.store import HFJsonStore
        return HFJsonStore()

    # Auto-detect: if DATABASE_URL or POSTGRES_DB set, prefer Postgres
    if os.getenv("DATABASE_URL") or os.getenv("POSTGRES_DB"):
        try:
            logger.info("Using PostgreSQL backend (auto-detected from environment)")
            from xiaozhi.database.postgres_store import PostgresStore
            return PostgresStore()
        except Exception as exc:
            logger.warning("PostgreSQL backend auto-detect failed (%s), falling back...", exc)

    # Auto-detect
    if is_hf_space or has_hf_token:
        logger.info("Using HuggingFace backend (auto-detected)")
        try:
            from xiaozhi.database.store import HFJsonStore
            return HFJsonStore()
        except Exception as exc:
            logger.warning("HuggingFace backend gagal (%s), fallback ke SQLite.", exc)
            from xiaozhi.database.sqlite_store import SQLiteStore
            return SQLiteStore()
    else:
        logger.info("Using SQLite backend (auto-detected)")
        from xiaozhi.database.sqlite_store import SQLiteStore
        return SQLiteStore()
