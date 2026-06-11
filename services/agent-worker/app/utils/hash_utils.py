from __future__ import annotations

import hashlib


def make_source_hash(*parts: object) -> str:
    """Generate a 64-char hex SHA256 source_hash per DB contract.

    All parts are trimmed, lower-cased, and None → empty string before joining with '|'.
    """
    normalized = [
        "" if part is None else str(part).strip().lower()
        for part in parts
    ]
    raw = "|".join(normalized)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
