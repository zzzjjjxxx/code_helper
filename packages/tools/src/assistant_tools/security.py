from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Iterable

from assistant_shared.models import CommandRule, TestOutcome


SNAPSHOT_MANIFEST_NAME = ".assistant_snapshot.json"


def is_within_root(path: str | Path, root: str | Path) -> bool:
    path_obj = Path(path).resolve()
    root_obj = Path(root).resolve()
    try:
        return path_obj.is_relative_to(root_obj)
    except AttributeError:  # pragma: no cover - Python 3.11 always has is_relative_to
        return str(path_obj).startswith(str(root_obj))


def command_is_allowed(command: Iterable[str], allowed_commands: Iterable[CommandRule]) -> bool:
    parts = [str(part) for part in command]
    if not parts:
        return False

    executable = Path(parts[0]).name.lower()
    args = parts[1:]
    for rule in allowed_commands:
        if executable not in {candidate.lower() for candidate in rule.executables}:
            continue
        if args[: len(rule.args_prefix)] == list(rule.args_prefix):
            return True
    return False


def capture_snapshot(workspace_root: str | Path, snapshot_dir: str | Path) -> Path:
    source = Path(workspace_root)
    snapshot = Path(snapshot_dir)
    if snapshot.exists():
        shutil.rmtree(snapshot)
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        source,
        snapshot,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc"),
    )
    write_snapshot_manifest(snapshot, source)
    return snapshot


def restore_snapshot(snapshot_dir: str | Path, workspace_root: str | Path) -> None:
    source = Path(snapshot_dir)
    target = Path(workspace_root)
    validate_snapshot(source, target)
    if target.exists():
        for child in list(target.iterdir()):
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
    else:
        target.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target, dirs_exist_ok=True, ignore=shutil.ignore_patterns(SNAPSHOT_MANIFEST_NAME))


def run_command(
    # DOC_ANCHOR: tools.run_command
    command: Iterable[str],
    *,
    workspace_root: str | Path,
    allowed_commands: Iterable[CommandRule],
    timeout_seconds: int = 120,
) -> TestOutcome:
    parts = [str(part) for part in command]
    command_display = " ".join(parts)
    started = datetime.utcnow()

    if not parts:
        raise ValueError("Command cannot be empty")

    workspace = Path(workspace_root).resolve()
    if not workspace.exists():
        raise ValueError("Workspace root does not exist")

    cwd = workspace
    if not command_is_allowed(parts, allowed_commands):
        raise PermissionError(f"Command is not allowed: {command_display}")

    env = _safe_environment()
    if len(parts) >= 3 and Path(parts[0]).name.lower().startswith('python') and parts[1:3] == ['-m', 'pytest']:
        env['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
    try:
        completed = subprocess.run(
            parts,
            cwd=str(cwd),
            check=False,
            capture_output=True,
            text=True,
            env=env,
            shell=False,
            timeout=timeout_seconds,
        )
        return TestOutcome(
            command=command_display,
            return_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            passed=completed.returncode == 0,
            duration_ms=_duration_ms(started),
        )
    except subprocess.TimeoutExpired:
        return TestOutcome(
            command=command_display,
            return_code=124,
            stdout="",
            stderr=f"Command timed out after {timeout_seconds} seconds",
            passed=False,
            duration_ms=_duration_ms(started),
        )


def write_snapshot_manifest(snapshot_dir: str | Path, workspace_root: str | Path) -> Path:
    snapshot = Path(snapshot_dir)
    manifest_path = snapshot / SNAPSHOT_MANIFEST_NAME
    manifest = {
        "workspace_root": str(Path(workspace_root).resolve()),
        "created_at": datetime.utcnow().isoformat(),
        "files": _snapshot_files(snapshot),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest_path


def validate_snapshot(snapshot_dir: str | Path, expected_workspace_root: str | Path) -> None:
    snapshot = Path(snapshot_dir)
    manifest_path = snapshot / SNAPSHOT_MANIFEST_NAME
    if not manifest_path.exists():
        raise ValueError("Snapshot manifest is missing")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_root = str(Path(expected_workspace_root).resolve())
    if manifest.get("workspace_root") != expected_root:
        raise ValueError("Snapshot does not belong to the requested workspace")

    files = manifest.get("files")
    if not isinstance(files, list):
        raise ValueError("Snapshot manifest is invalid")

    for entry in files:
        # Verify the snapshot still matches what was originally captured.
        if not isinstance(entry, dict):
            raise ValueError("Snapshot manifest is invalid")
        relative_path = entry.get("path")
        expected_hash = entry.get("sha256")
        if not isinstance(relative_path, str) or not isinstance(expected_hash, str):
            raise ValueError("Snapshot manifest is invalid")
        file_path = snapshot / relative_path
        if not file_path.exists() or _sha256(file_path) != expected_hash:
            raise ValueError(f"Snapshot file changed unexpectedly: {relative_path}")


def _snapshot_files(snapshot_dir: Path) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for path in sorted(snapshot_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.name == SNAPSHOT_MANIFEST_NAME:
            continue
        relative_path = path.relative_to(snapshot_dir).as_posix()
        entries.append({"path": relative_path, "sha256": _sha256(path)})
    return entries


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_environment() -> dict[str, str]:
    # DOC_ANCHOR: tools.safe_environment
    allowed_keys = {
        "PATH",
        "SystemRoot",
        "WINDIR",
        "HOME",
        "USERPROFILE",
        "TEMP",
        "TMP",
        "PYTHONPATH",
        "PYTHONHOME",
        "VIRTUAL_ENV",
    }
    env = {key: value for key, value in os.environ.items() if key in allowed_keys or key.startswith("APP_")}
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _duration_ms(started: datetime) -> int:
    return int((datetime.utcnow() - started).total_seconds() * 1000)



