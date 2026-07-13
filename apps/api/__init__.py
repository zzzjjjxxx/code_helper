"""FastAPI backend for the R&D assistant MVP."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
PACKAGE_SRC_DIRS = [
    ROOT / "packages" / "agent-core" / "src",
    ROOT / "packages" / "tools" / "src",
    ROOT / "packages" / "memory" / "src",
    ROOT / "packages" / "telemetry" / "src",
    ROOT / "packages" / "shared" / "src",
]

for package_src in PACKAGE_SRC_DIRS:
    path = str(package_src)
    if package_src.exists() and path not in sys.path:
        sys.path.insert(0, path)

