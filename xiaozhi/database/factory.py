"""
Store factory for selecting the appropriate database backend.
Supports HuggingFace (for HF Spaces) and SQLite (for VPS).
"""
import os
import logging

logger = logging.getLogger("xiaozhi.factory")


def create_store():
    """
    Create the appropriate store based on environment configuration.

    Environment variables:
        DB_BACKEND: "sqlite" or "huggingface" (default: auto-detect)
        SQLITE_DB_PATH: Path to SQLite database file (default: data/xiaozhi.db)

    Auto-detection logic:
        - If running on HuggingFace Spaces (SPACE_ID env var exists) → use HFJsonStore (fallback SQLite)
        - If HF_TOKEN is set → use HFJsonStore (fallback SQLite)
        - Otherwise → use SQLiteStore
    """
    backend = os.getenv("DB_BACKEND", "").lower()
    is_hf_space = bool(os.getenv("SPACE_ID"))
    has_hf_token = bool(os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACEHUB_API_TOKEN"))

    # Explicit backend selection
    if backend == "sqlite":
        logger.info("Using SQLite backend (explicit)")
        from xiaozhi.database.sqlite_store import SQLiteStore
        return SQLiteStore()
    elif backend == "huggingface":
        logger.info("Using HuggingFace backend (explicit)")
        from xiaozhi.database.store import HFJsonStore
        return HFJsonStore()

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
