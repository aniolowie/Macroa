"""Singleton settings — reads from .env + environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    openrouter_api_key: str
    model_nano: str    # microcontroller — routing + trivial ops
    model_haiku: str   # efficiency cores — lightweight tasks
    model_sonnet: str  # performance cores — quality work
    model_opus: str    # GPU — heavy reasoning
    context_window: int
    memory_backend: str  # "sqlite" or "json"
    memory_db_path: Path
    http_referer: str
    app_title: str
    skills_dir: Path
    tools_dir: Path          # user-installed tools: ~/.macroa/tools/
    builtin_tools_dir: Path  # example tools shipped with Macroa
    heartbeat_interval: int  # seconds between persistent-tool heartbeat ticks
    audit_db_path: Path      # audit log DB (separate from memory so wipes don't erase history)
    sessions_db_path: Path   # named sessions + persisted context
    scheduler_db_path: Path  # scheduled tasks
    scheduler_poll: int      # seconds between scheduler ticks
    network_timeout: int     # default HTTP timeout in seconds

    @property
    def model_map(self) -> dict[str, str]:
        from macroa.stdlib.schema import ModelTier
        return {
            ModelTier.NANO: self.model_nano,
            ModelTier.HAIKU: self.model_haiku,
            ModelTier.SONNET: self.model_sonnet,
            ModelTier.OPUS: self.model_opus,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        raise EnvironmentError(
            "OPENROUTER_API_KEY is not set. "
            "Copy .env.example to .env and add your key."
        )

    home = Path.home()
    macroa_dir = home / ".macroa"
    macroa_dir.mkdir(exist_ok=True)

    skills_dir = Path(__file__).parent.parent / "skills"
    builtin_tools_dir = Path(__file__).parent.parent / "tools" / "examples"

    tools_dir_str = os.environ.get("MACROA_TOOLS_DIR", str(macroa_dir / "tools"))
    tools_dir = Path(tools_dir_str).expanduser()
    tools_dir.mkdir(parents=True, exist_ok=True)

    return Settings(
        openrouter_api_key=api_key,
        model_nano=os.environ.get("MACROA_MODEL_NANO", "google/gemini-2.5-flash-lite"),
        model_haiku=os.environ.get("MACROA_MODEL_HAIKU", "anthropic/claude-haiku-4-5"),
        model_sonnet=os.environ.get("MACROA_MODEL_SONNET", "anthropic/claude-sonnet-4-6"),
        model_opus=os.environ.get("MACROA_MODEL_OPUS", "anthropic/claude-opus-4-6"),
        context_window=int(os.environ.get("MACROA_CONTEXT_WINDOW", "20")),
        memory_backend=os.environ.get("MACROA_MEMORY_BACKEND", "sqlite"),
        memory_db_path=Path(
            os.environ.get("MACROA_MEMORY_DB_PATH", str(macroa_dir / "memory.db"))
        ).expanduser(),
        http_referer=os.environ.get("MACROA_HTTP_REFERER", "https://github.com/macroa/macroa"),
        app_title=os.environ.get("MACROA_APP_TITLE", "Macroa"),
        skills_dir=skills_dir,
        tools_dir=tools_dir,
        builtin_tools_dir=builtin_tools_dir,
        heartbeat_interval=int(os.environ.get("MACROA_HEARTBEAT_INTERVAL", "60")),
        audit_db_path=Path(
            os.environ.get("MACROA_AUDIT_DB_PATH", str(macroa_dir / "audit.db"))
        ).expanduser(),
        sessions_db_path=Path(
            os.environ.get("MACROA_SESSIONS_DB_PATH", str(macroa_dir / "sessions.db"))
        ).expanduser(),
        scheduler_db_path=Path(
            os.environ.get("MACROA_SCHEDULER_DB_PATH", str(macroa_dir / "scheduler.db"))
        ).expanduser(),
        scheduler_poll=int(os.environ.get("MACROA_SCHEDULER_POLL", "10")),
        network_timeout=int(os.environ.get("MACROA_NETWORK_TIMEOUT", "30")),
    )
