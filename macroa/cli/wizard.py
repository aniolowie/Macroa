"""First-run setup wizard — guides new users through configuration.

Writes to ~/.macroa/.env so the config survives across projects and
pip installs without touching any project-level .env file.

Design goals:
  - A complete non-technical user should finish in under 2 minutes
  - Every question has a sensible default so pressing Enter always works
  - No jargon — model tiers are explained in plain English
"""

from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

_console = Console()
_MACROA_DIR = Path.home() / ".macroa"
_ENV_PATH = _MACROA_DIR / ".env"


# ── public API ────────────────────────────────────────────────────────────────

def needs_setup() -> bool:
    """Return True if Macroa has not been configured yet."""
    _load_macroa_env()
    return not bool(os.environ.get("OPENROUTER_API_KEY", "").strip())


def run_wizard(*, rerun: bool = False) -> None:
    """Run the interactive setup wizard. Safe to call even if already configured."""
    _load_macroa_env()
    _console.clear()
    _step_welcome(rerun=rerun)
    api_key = _step_api_key()
    name = _step_name()
    models = _step_models()
    _write_env(api_key=api_key, name=name, models=models)
    _step_done(name=name)


# ── steps ─────────────────────────────────────────────────────────────────────

def _step_welcome(*, rerun: bool) -> None:
    heading = "Reconfiguring Macroa" if rerun else "Welcome to Macroa"
    subtitle = "Let's update your setup." if rerun else (
        "Your personal AI OS — built on the same principles as an operating system.\n\n"
        "This wizard takes about [bold]2 minutes[/bold] and only needs to run once.\n"
        "Every question has a default — just press [bold]Enter[/bold] to accept it."
    )
    _console.print(
        Panel(
            f"[bold cyan]{heading}[/bold cyan]\n\n{subtitle}",
            border_style="cyan",
            padding=(1, 4),
        )
    )
    _console.print()
    try:
        Prompt.ask("[dim]Press Enter to begin[/dim]", default="")
    except (KeyboardInterrupt, EOFError):
        _console.print("\n[dim]Setup cancelled.[/dim]")
        sys.exit(0)
    _console.print()


def _step_api_key() -> str:
    _console.print(
        Panel(
            "[bold]Step 1 of 3 — API Key[/bold]\n\n"
            "Macroa routes AI requests through [cyan]OpenRouter[/cyan], which gives you\n"
            "access to Claude, Gemini, GPT, and dozens of other models with one key.\n\n"
            "  [dim]→ Get a free key at: https://openrouter.ai/keys[/dim]\n\n"
            "Your key is stored locally in [dim]~/.macroa/.env[/dim] and never sent anywhere\n"
            "except directly to OpenRouter.",
            border_style="cyan",
            padding=(1, 4),
        )
    )

    existing = os.environ.get("OPENROUTER_API_KEY", "")
    if existing:
        masked = existing[:8] + "…" + existing[-4:] if len(existing) > 12 else "***"
        keep = Confirm.ask(
            f"  A key is already configured ([dim]{masked}[/dim]). Keep it?",
            default=True,
        )
        if keep:
            _console.print()
            return existing

    while True:
        try:
            key = Prompt.ask("  [bold]OpenRouter API key[/bold]").strip()
        except (KeyboardInterrupt, EOFError):
            _console.print("\n[dim]Setup cancelled.[/dim]")
            sys.exit(0)
        if key.startswith("sk-or-") and len(key) > 20:
            _console.print("  [green]✓ Key looks good.[/green]\n")
            return key
        if key:
            _console.print(
                "  [yellow]That doesn't look like an OpenRouter key "
                "(should start with sk-or-). Try again.[/yellow]"
            )
        else:
            _console.print("  [yellow]API key is required.[/yellow]")


def _step_name() -> str:
    _console.print(
        Panel(
            "[bold]Step 2 of 3 — Your Name[/bold]\n\n"
            "Macroa greets you by name when you open it.\n"
            "This is stored locally and never sent to any AI model.",
            border_style="cyan",
            padding=(1, 4),
        )
    )
    default_name = getpass.getuser().capitalize()
    try:
        name = Prompt.ask("  [bold]Your name[/bold]", default=default_name).strip()
    except (KeyboardInterrupt, EOFError):
        name = default_name
    name = name or default_name
    _console.print(f"  [green]✓ Hello, {name}![/green]\n")
    return name


def _step_models() -> dict[str, str]:
    _defaults = {
        "MACROA_MODEL_NANO":   "google/gemini-2.5-flash-lite",
        "MACROA_MODEL_HAIKU":  "anthropic/claude-haiku-4-5",
        "MACROA_MODEL_SONNET": "anthropic/claude-sonnet-4-6",
        "MACROA_MODEL_OPUS":   "anthropic/claude-opus-4-6",
    }

    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    table.add_column("Tier", style="bold cyan")
    table.add_column("Role")
    table.add_column("Default model", style="dim")
    table.add_row("NANO",   "Background routing — always on, cheapest",   _defaults["MACROA_MODEL_NANO"])
    table.add_row("HAIKU",  "Lightweight tasks — fast and affordable",    _defaults["MACROA_MODEL_HAIKU"])
    table.add_row("SONNET", "Quality work — the everyday workhorse",      _defaults["MACROA_MODEL_SONNET"])
    table.add_row("OPUS",   "Heavy reasoning — most powerful, use sparingly", _defaults["MACROA_MODEL_OPUS"])

    _console.print(
        Panel(
            Text.assemble(
                ("Step 3 of 3 — Models\n\n", "bold"),
                "Macroa uses four model tiers, each with a different cost/power tradeoff.\n"
                "The defaults are a sensible starting point for most users.",
            ),
            border_style="cyan",
            padding=(1, 4),
        )
    )
    _console.print(table)
    _console.print()

    try:
        keep = Confirm.ask(
            "  Keep the defaults? (recommended for new users)",
            default=True,
        )
    except (KeyboardInterrupt, EOFError):
        keep = True

    if keep:
        _console.print("  [green]✓ Using default models.[/green]\n")
        return {}   # empty = don't write, settings.py will use its own defaults

    # Advanced: let user override each tier
    models: dict[str, str] = {}
    for key, default in _defaults.items():
        tier = key.replace("MACROA_MODEL_", "")
        try:
            val = Prompt.ask(f"  {tier}", default=default).strip()
        except (KeyboardInterrupt, EOFError):
            val = default
        if val and val != default:
            models[key] = val
        elif val == default:
            pass   # don't write, let settings.py default handle it
    _console.print()
    return models


# ── file I/O ──────────────────────────────────────────────────────────────────

def _write_env(*, api_key: str, name: str, models: dict[str, str]) -> None:
    _MACROA_DIR.mkdir(parents=True, exist_ok=True)

    lines: list[str] = [
        "# Macroa configuration — generated by setup wizard\n",
        "# Edit this file to change settings, or run: macroa setup\n",
        "\n",
        f"OPENROUTER_API_KEY={api_key}\n",
        f"MACROA_USER_NAME={name}\n",
    ]
    for key, val in models.items():
        lines.append(f"{key}={val}\n")

    _ENV_PATH.write_text("".join(lines))
    # Inject into current process so the caller can use them immediately
    os.environ["OPENROUTER_API_KEY"] = api_key
    os.environ["MACROA_USER_NAME"] = name
    for key, val in models.items():
        os.environ[key] = val


def _step_done(*, name: str) -> None:
    _console.print(
        Panel(
            f"[bold green]Macroa is ready, {name}![/bold green]\n\n"
            f"Config saved to [dim]{_ENV_PATH}[/dim]\n\n"
            "[bold]Quick start:[/bold]\n"
            "  [cyan]macroa[/cyan]                     open the interactive shell\n"
            "  [cyan]macroa run \"what can you do?\"[/cyan]  single-shot command\n"
            "  [cyan]macroa serve[/cyan]               open the web dashboard\n"
            "  [cyan]macroa setup[/cyan]               run this wizard again\n\n"
            "[dim]Tip: prefix any shell command with [bold]![/bold] to run it directly.\n"
            "     e.g.  [bold]!ls -la[/bold]   or   [bold]!git status[/bold][/dim]",
            border_style="green",
            padding=(1, 4),
        )
    )
    _console.print()


# ── helpers ───────────────────────────────────────────────────────────────────

def _load_macroa_env() -> None:
    """Load ~/.macroa/.env without overwriting already-set env vars."""
    if _ENV_PATH.exists():
        try:
            from dotenv import load_dotenv
        except ImportError:
            # python-dotenv is optional; if it's not installed, just skip loading.
            return

        try:
            load_dotenv(_ENV_PATH, override=False)
        except Exception as exc:
            # Don't crash if the env file is unreadable or invalid, but surface it.
            _console.print(
                f"[yellow]Warning:[/yellow] Failed to load env file at "
                f"[dim]{_ENV_PATH}[/dim]: {exc}"
            )
