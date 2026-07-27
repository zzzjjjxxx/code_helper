from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from assistant_shared.models import CommandRule, SecurityPolicy

ROOT = Path(__file__).resolve().parents[3]

DEFAULT_ALLOWED_COMMANDS: tuple[CommandRule, ...] = (
    CommandRule(
        executables=["python.exe", "python", "python3", "python3.exe"],
        args_prefix=["-m", "pytest", "-q"],
    ),
)


@dataclass(slots=True)
class LLMConfig:
    provider: str = "openai"
    base_url: str = "https://api.openai.com/v1"
    api_key_env: str = "OPENAI_API_KEY"
    model: str = "your-model-name"
    temperature: float = 0.2
    max_output_tokens: int = 2048
    timeout_seconds: int = 120


@dataclass(slots=True)
class Settings:
    app_name: str = "R&D Assistant MVP"
    root_dir: Path = ROOT
    data_dir: Path = ROOT / "data"
    workspace_root: Path = ROOT / "data" / "demo_workspace"
    database_path: Path = ROOT / "data" / "app.db"
    snapshot_root: Path = ROOT / "data" / "snapshots"
    llm_config_path: Path = ROOT / "config" / "llm_config.json"
    llm_config: LLMConfig = field(default_factory=LLMConfig)
    allowed_commands: tuple[CommandRule, ...] = DEFAULT_ALLOWED_COMMANDS
    cors_origins: tuple[str, ...] = ("http://localhost:5173",)

    @classmethod
    def load(cls) -> "Settings":
        _load_env_file(ROOT / ".env")
        data_dir = Path(os.getenv("APP_DATA_DIR", str(ROOT / "data")))
        workspace_root = Path(os.getenv("APP_WORKSPACE_ROOT", str(data_dir / "demo_workspace")))
        database_path = Path(os.getenv("APP_DATABASE_PATH", str(data_dir / "app.db")))
        snapshot_root = Path(os.getenv("APP_SNAPSHOT_ROOT", str(data_dir / "snapshots")))
        llm_config_path = Path(os.getenv("APP_LLM_CONFIG_PATH", str(ROOT / "config" / "llm_config.json")))
        allowed_commands = _parse_allowed_commands(os.getenv("APP_ALLOWED_COMMANDS"))
        cors = tuple(
            origin.strip()
            for origin in os.getenv("APP_CORS_ORIGINS", "http://localhost:5173").split(",")
            if origin.strip()
        )
        llm_config = _load_llm_config(llm_config_path)
        return cls(
            data_dir=data_dir,
            workspace_root=workspace_root,
            database_path=database_path,
            snapshot_root=snapshot_root,
            llm_config_path=llm_config_path,
            llm_config=llm_config,
            allowed_commands=allowed_commands,
            cors_origins=cors,
        )

    def security_policy(self) -> SecurityPolicy:
        return SecurityPolicy(
            workspace_root=str(self.workspace_root),
            snapshot_root=str(self.snapshot_root),
            allowed_commands=[rule.model_copy() for rule in self.allowed_commands],
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.load()


def _parse_allowed_commands(raw: str | None) -> tuple[CommandRule, ...]:
    if not raw:
        return DEFAULT_ALLOWED_COMMANDS

    parsed = json.loads(raw)
    if not isinstance(parsed, list):
        raise ValueError("APP_ALLOWED_COMMANDS must be a JSON list")

    rules: list[CommandRule] = []
    for item in parsed:
        if not isinstance(item, dict):
            raise ValueError("Each command rule must be a JSON object")
        executables = item.get("executables", [])
        args_prefix = item.get("args_prefix", [])
        if not isinstance(executables, list) or not all(isinstance(value, str) for value in executables):
            raise ValueError("Command rule executables must be a list of strings")
        if not isinstance(args_prefix, list) or not all(isinstance(value, str) for value in args_prefix):
            raise ValueError("Command rule args_prefix must be a list of strings")
        rules.append(CommandRule(executables=executables, args_prefix=args_prefix))

    return tuple(rules) if rules else DEFAULT_ALLOWED_COMMANDS


def _load_llm_config(path: Path) -> LLMConfig:
    if not path.exists():
        return LLMConfig()

    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, dict):
        raise ValueError("LLM config must be a JSON object")

    return LLMConfig(
        provider=str(raw.get("provider", "openai")),
        base_url=str(raw.get("base_url", "https://api.openai.com/v1")),
        api_key_env=str(raw.get("api_key_env", "OPENAI_API_KEY")),
        model=str(raw.get("model", "your-model-name")),
        temperature=float(raw.get("temperature", 0.2)),
        max_output_tokens=int(raw.get("max_output_tokens", 2048)),
        timeout_seconds=int(raw.get("timeout_seconds", 120)),
    )

def _load_env_file(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value
