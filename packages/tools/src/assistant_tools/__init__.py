from __future__ import annotations

import difflib
import sys
from pathlib import Path

from assistant_shared.models import PatchArtifact, RetrievalHit, TestOutcome

from .security import (
    SNAPSHOT_MANIFEST_NAME,
    capture_snapshot,
    command_is_allowed,
    is_within_root,
    restore_snapshot,
    run_command,
    validate_snapshot,
    write_snapshot_manifest,
)


def read_text_file(path: str | Path) -> str:
    return Path(path).read_text(encoding="utf-8")


def write_text_file(path: str | Path, content: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def list_python_files(root: str | Path) -> list[Path]:
    workspace = Path(root)
    return sorted(
        candidate
        for candidate in workspace.rglob("*.py")
        if candidate.is_file()
        and "__pycache__" not in candidate.parts
        and ".pytest_cache" not in candidate.parts
    )


TEXT_FILE_EXTENSIONS = {
    ".py",
    ".md",
    ".txt",
    ".json",
    ".toml",
    ".yaml",
    ".yml",
    ".ini",
    ".cfg",
}


def search_workspace_files(
    root: str | Path,
    query: str,
    *,
    focus_paths: list[str] | None = None,
    limit: int = 5,
) -> list[RetrievalHit]:
    workspace = Path(root)
    terms = _tokenize(query)
    if not terms:
        return []

    focus_paths = focus_paths or []
    focus_set = {Path(path).as_posix() for path in focus_paths}
    hits: list[RetrievalHit] = []

    for candidate in _iter_text_files(workspace):
        relative = candidate.relative_to(workspace).as_posix()
        text = read_text_file(candidate)
        score = _score_text(relative, text, terms, focus_set)
        if score <= 0:
            continue
        hits.append(
            RetrievalHit(
                path=relative,
                score=round(score, 3),
                reason=_build_reason(relative, terms, focus_set),
                preview=_build_snippet(text, terms),
            )
        )

    hits.sort(key=lambda item: (-item.score, item.path))
    return hits[:limit]


def replace_once(text: str, old: str, new: str) -> str:
    if old not in text:
        raise ValueError(f"Expected to find '{old}' in text")
    return text.replace(old, new, 1)


def unified_diff(path: str | Path, before: str, after: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{Path(path).as_posix()}",
            tofile=f"b/{Path(path).as_posix()}",
        )
    )


def run_pytest(
    workspace_root: str | Path,
    *,
    allowed_commands=None,
    timeout_seconds: int = 120,
) -> TestOutcome:
    from assistant_shared.models import CommandRule

    rules = allowed_commands
    if rules is None:
        rules = (
            CommandRule(
                executables=["python.exe", "python", "python3", "python3.exe"],
                args_prefix=["-m", "pytest", "-q"],
            ),
        )
    outcome = run_command(
        [sys.executable, "-m", "pytest", "-q"],
        workspace_root=workspace_root,
        allowed_commands=rules,
        timeout_seconds=timeout_seconds,
    )
    if outcome.return_code == 5:
        test_files = list(Path(workspace_root).rglob("test_*.py")) + list(Path(workspace_root).rglob("*_test.py"))
        if not any(candidate.is_file() for candidate in test_files):
            outcome = outcome.model_copy(update={
                "passed": True,
                "stderr": "No tests collected; verification skipped.",
            })
    return outcome


def preview_text(text: str, lines: int = 24) -> str:
    content = text.splitlines()
    return "\n".join(content[:lines])


def artifact_from_patch(path: str | Path, before: str, after: str) -> PatchArtifact:
    return PatchArtifact(path=str(path), before=before, after=after, diff=unified_diff(path, before, after))


__all__ = [
    "SNAPSHOT_MANIFEST_NAME",
    "artifact_from_patch",
    "capture_snapshot",
    "command_is_allowed",
    "is_within_root",
    "list_python_files",
    "preview_text",
    "read_text_file",
    "replace_once",
    "restore_snapshot",
    "run_command",
    "run_pytest",
    "search_workspace_files",
    "unified_diff",
    "validate_snapshot",
    "write_snapshot_manifest",
    "write_text_file",
]


def _iter_text_files(root: Path) -> list[Path]:
    return sorted(
        candidate
        for candidate in root.rglob("*")
        if candidate.is_file()
        and candidate.suffix.lower() in TEXT_FILE_EXTENSIONS
        and "__pycache__" not in candidate.parts
        and ".pytest_cache" not in candidate.parts
        and "snapshots" not in candidate.parts
    )


def _tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for raw in text.replace("/", " ").replace("-", " ").split():
        cleaned = "".join(ch.lower() for ch in raw if ch.isalnum() or ch == "_")
        if len(cleaned) >= 2:
            tokens.append(cleaned)
    return tokens


def _score_text(relative_path: str, text: str, terms: list[str], focus_set: set[str]) -> float:
    haystacks = [relative_path.lower(), text.lower()]
    score = 0.0
    if relative_path in focus_set:
        score += 3.0
    for term in terms:
        for haystack in haystacks:
            if term in haystack:
                score += 1.0
    return score


def _build_reason(relative_path: str, terms: list[str], focus_set: set[str]) -> str:
    reasons: list[str] = []
    if relative_path in focus_set:
        reasons.append("focus path")
    matched_terms = [term for term in terms if term in relative_path.lower()]
    if matched_terms:
        reasons.append("path terms: " + ", ".join(sorted(set(matched_terms))[:3]))
    if not reasons:
        reasons.append("content match")
    return "; ".join(reasons)


def _build_snippet(text: str, terms: list[str], radius: int = 1) -> str:
    lines = text.splitlines()
    if not lines:
        return ""

    lowered_terms = [term.lower() for term in terms]
    for index, line in enumerate(lines):
        lowered = line.lower()
        if any(term in lowered for term in lowered_terms):
            start = max(0, index - radius)
            end = min(len(lines), index + radius + 1)
            return "\n".join(lines[start:end])

    return preview_text(text, lines=8)