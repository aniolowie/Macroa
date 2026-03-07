"""Singleton settings — reads from .env + environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

# Priority (highest to lowest):
#   1. Shell environment variables — already in os.environ, load_dotenv never touches them
#   2. Project .env (current working directory only) — loaded first, wins over wizard defaults
#   3. ~/.macroa/.env — written by setup wizard, only fills gaps not set above
# find_dotenv(usecwd=True) restricts the upward search to CWD, preventing the package's own
# .env from being loaded when macroa is run from an unrelated directory.
_project_dotenv = find_dotenv(usecwd=True)
if _project_dotenv:
    load_dotenv(_project_dotenv, override=False)                      # project .env (CWD only)
load_dotenv(Path.home() / ".macroa" / ".env", override=False)        # wizard defaults


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
    watchdog_db_path: Path   # watchdog observer registry
    session_budget_usd: float    # max USD spend per session (0 = unlimited)
    session_budget_tokens: int   # max tokens per session (0 = unlimited)
    network_timeout: int         # default HTTP timeout in seconds
    user_name: str           # display name set during setup wizard
    socket_path: Path        # Unix domain socket for thin-client attach

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
        raise OSError(
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

    def _clean_model(env_key: str, default: str) -> str:
        """Strip accidental 'openrouter/' prefix that some users paste from docs."""
        val = os.environ.get(env_key, default)
        if val.startswith("openrouter/"):
            val = val[len("openrouter/"):]
        return val

    return Settings(
        openrouter_api_key=api_key,
        model_nano=_clean_model("MACROA_MODEL_NANO", "google/gemini-2.5-flash-lite"),
        model_haiku=_clean_model("MACROA_MODEL_HAIKU", "google/gemini-2.5-flash-lite"),
        model_sonnet=_clean_model("MACROA_MODEL_SONNET", "anthropic/claude-sonnet-4-6"),
        model_opus=_clean_model("MACROA_MODEL_OPUS", "anthropic/claude-opus-4-6"),
        context_window=int(os.environ.get("MACROA_CONTEXT_WINDOW", "20")),
        memory_backend=os.environ.get("MACROA_MEMORY_BACKEND", "sqlite"),
        memory_db_path=Path(
            os.environ.get("MACROA_MEMORY_DB_PATH", str(macroa_dir / "memory" / "memory.db"))
        ).expanduser(),
        http_referer=os.environ.get("MACROA_HTTP_REFERER", "https://github.com/macroa/macroa"),
        app_title=os.environ.get("MACROA_APP_TITLE", "Macroa"),
        skills_dir=skills_dir,
        tools_dir=tools_dir,
        builtin_tools_dir=builtin_tools_dir,
        heartbeat_interval=int(os.environ.get("MACROA_HEARTBEAT_INTERVAL", "60")),
        audit_db_path=Path(
            os.environ.get("MACROA_AUDIT_DB_PATH", str(macroa_dir / "logs" / "audit.db"))
        ).expanduser(),
        sessions_db_path=Path(
            os.environ.get("MACROA_SESSIONS_DB_PATH", str(macroa_dir / "sessions" / "sessions.db"))
        ).expanduser(),
        scheduler_db_path=Path(
            os.environ.get("MACROA_SCHEDULER_DB_PATH", str(macroa_dir / "logs" / "scheduler.db"))
        ).expanduser(),
        scheduler_poll=int(os.environ.get("MACROA_SCHEDULER_POLL", "10")),
        watchdog_db_path=Path(
            os.environ.get("MACROA_WATCHDOG_DB_PATH", str(macroa_dir / "logs" / "watchdog.db"))
        ).expanduser(),
        session_budget_usd=float(os.environ.get("MACROA_SESSION_BUDGET_USD", "0")),
        session_budget_tokens=int(os.environ.get("MACROA_SESSION_BUDGET_TOKENS", "0")),
        network_timeout=int(os.environ.get("MACROA_NETWORK_TIMEOUT", "30")),
        user_name=os.environ.get("MACROA_USER_NAME", ""),
        socket_path=Path(
            os.environ.get("MACROA_SOCKET_PATH", str(macroa_dir / "macroa.sock"))
        ).expanduser(),
    )
