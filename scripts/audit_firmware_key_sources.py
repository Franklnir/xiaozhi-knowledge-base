#!/usr/bin/env python3
"""Report which configured legacy key can decrypt each firmware, never the keys."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from prepare_production_rotation import legacy_firmware_secret, parse_env


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--current-env", type=Path, required=True)
    parser.add_argument("--legacy-firmware-source", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()

    _, values = parse_env(args.current_env)
    candidates = {
        "APP_SECRET_KEY": values.get("APP_SECRET_KEY", ""),
        "DATA_ENCRYPTION_KEY": values.get("DATA_ENCRYPTION_KEY", ""),
        "FIRMWARE_PRESET_SECRET": values.get("FIRMWARE_PRESET_SECRET", ""),
        "LEGACY_FIRMWARE_FALLBACK": legacy_firmware_secret(values, args.legacy_firmware_source),
    }
    candidates = {name: value for name, value in candidates.items() if value}

    files = sorted(args.root.rglob("*.bin.enc"))
    if not files:
        raise RuntimeError("No encrypted firmware files found")
    unmatched = False
    for path in files:
        payload = path.read_bytes()
        matches: list[str] = []
        for name, secret in candidates.items():
            try:
                AESGCM(hashlib.sha256(secret.encode("utf-8")).digest()).decrypt(
                    payload[:12], payload[12:], None
                )
            except Exception:
                continue
            matches.append(name)
        if not matches:
            unmatched = True
        print(f"{path}: {','.join(matches) if matches else 'NO_CANDIDATE_MATCH'}")
    return 1 if unmatched else 0


if __name__ == "__main__":
    raise SystemExit(main())
