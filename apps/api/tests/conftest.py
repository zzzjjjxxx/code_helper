from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
for relative in [
    ROOT,
    ROOT / "packages" / "agent-core" / "src",
    ROOT / "packages" / "tools" / "src",
    ROOT / "packages" / "memory" / "src",
    ROOT / "packages" / "telemetry" / "src",
    ROOT / "packages" / "shared" / "src",
]:
    path = str(relative)
    if path not in sys.path:
        sys.path.insert(0, path)
