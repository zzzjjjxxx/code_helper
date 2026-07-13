from __future__ import annotations


def normalize_username(raw: str) -> str:
    """Normalize a display name for consistent comparisons."""
    return raw.strip().lower()
