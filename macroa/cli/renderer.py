"""Rich-based terminal renderer."""

from __future__ import annotations

import getpass
import importlib.metadata

from rich.columns import Columns
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

from macroa.stdlib.schema import ModelTier, SkillResult

_THEME = Theme(
    {
        "prompt": "bold cyan",
        "success": "green",
        "error": "bold red",
        "warning": "yellow",
        "meta": "dim white",
        "skill": "dim cyan",
        "tier.nano":   "dim blue",
        "tier.haiku":  "dim green",
        "tier.sonnet": "dim yellow",
        "tier.opus":   "dim magenta",
    }
)

console = Console(theme=_THEME)

_TIER_STYLE = {
    ModelTier.NANO:   "tier.nano",
    ModelTier.HAIKU:  "tier.haiku",
    ModelTier.SONNET: "tier.sonnet",
    ModelTier.OPUS:   "tier.opus",
}

_TIER_LABEL = {
    ModelTier.NANO:   "nano",
    ModelTier.HAIKU:  "haiku",
    ModelTier.SONNET: "sonnet",
    ModelTier.OPUS:   "opus",
}


# ── result rendering ──────────────────────────────────────────────────────────

def render_result(result: SkillResult, *, debug: bool = False) -> None:
    if not result.success and result.error:
        console.print(f"[error]Error:[/error] {result.error}")
        return

    output = result.output.strip()
    if not output:
        console.print("[meta](no output)[/meta]")
        return

    if _looks_like_markdown(output):
        console.print(Markdown(output))
    else:
        console.print(output)

    if debug:
        tier_style = _TIER_STYLE.get(result.model_tier, "meta")
        tier_label = _TIER_LABEL.get(result.model_tier, result.model_tier.value)
        skill = result.metadata.get("skill", "—")
        console.print(
            f"[meta]  skill={skill}  tier={tier_label}  "
            f"turn={result.turn_id[:8]}[/meta]",
            style=tier_style,
        )


def render_error(message: str) -> None:
    console.print(f"[error]{message}[/error]")


def render_info(message: str) -> None:
    console.print(f"[meta]{message}[/meta]")


def render_prompt() -> str:
    return "[prompt]macroa>[/prompt] "


# ── help ──────────────────────────────────────────────────────────────────────

def print_help() -> None:
    console.print(
        Panel(
            "[bold]Built-in commands:[/bold]\n"
            "  [prompt]clear[/prompt]      — clear context window\n"
            "  [prompt]debug[/prompt]      — toggle debug metadata\n"
            "  [prompt]help[/prompt]       — show this message\n"
            "  [prompt]exit[/prompt] / [prompt]quit[/prompt] / [prompt]q[/prompt] — exit\n\n"
            "[bold]Shell shortcuts:[/bold]\n"
            "  [prompt]!<cmd>[/prompt] or [prompt]$<cmd>[/prompt] — run shell command directly\n\n"
            "[bold]CLI commands (outside the shell):[/bold]\n"
            "  [prompt]macroa run \"...\"[/prompt]        single command, no REPL\n"
            "  [prompt]macroa sessions list[/prompt]      named sessions\n"
            "  [prompt]macroa schedule add[/prompt]       recurring tasks\n"
            "  [prompt]macroa tools list[/prompt]         installed tools\n"
            "  [prompt]macroa serve[/prompt]              web dashboard\n"
            "  [prompt]macroa setup[/prompt]              reconfigure\n\n"
            "[bold]Examples:[/bold]\n"
            "  remember my server IP is 192.168.1.100\n"
            "  what is my server IP?\n"
            "  !ls -la /tmp\n"
            "  think carefully about the tradeoffs of microservices",
            title="Macroa Help",
            border_style="cyan",
        )
    )


# ── banner ────────────────────────────────────────────────────────────────────

def print_banner() -> None:
    """Print the startup banner with system status."""
    version = _get_version()
    name = _get_user_name()
    audit_line = _get_audit_summary()
    model_table = _build_model_table()

    # Left column: identity block
    left = Text()
    left.append("\n")
    left.append(" ◈ ", style="bold cyan")
    left.append("Macroa  ", style="bold white")
    left.append(f"v{version}", style="dim")
    left.append("\n")
    left.append(" ◈ ", style="bold cyan")
    left.append("Personal AI OS", style="dim white")
    left.append("\n\n")
    left.append(f" Hello, {name}\n", style="bold")
    left.append(f" {audit_line}\n", style="dim")

    # Right column: model stack
    right = model_table

    columns = Columns([left, right], equal=False, expand=True)

    footer = Text(justify="center")
    footer.append("help", style="bold cyan")
    footer.append("  ·  ", style="dim")
    footer.append("exit", style="bold cyan")
    footer.append("  ·  ", style="dim")
    footer.append("clear", style="bold cyan")
    footer.append("  ·  ", style="dim")
    footer.append("debug", style="bold cyan")
    footer.append("  ·  ", style="dim")
    footer.append("!shell", style="bold cyan")
    footer.append("  ·  ", style="dim")
    footer.append("macroa setup", style="bold cyan")

    body = Text()
    body.append_text(left)

    console.print(
        Panel(
            columns,
            title=f"[bold cyan]Macroa[/bold cyan] [dim]v{version}[/dim]",
            subtitle=footer,
            border_style="cyan",
            padding=(0, 2),
        )
    )
    console.print()


# ── banner helpers ────────────────────────────────────────────────────────────

def _get_version() -> str:
    try:
        return importlib.metadata.version("macroa")
    except importlib.metadata.PackageNotFoundError:
        return "dev"


def _get_user_name() -> str:
    import os
    name = os.environ.get("MACROA_USER_NAME", "").strip()
    if name:
        return name
    try:
        from macroa.config.settings import get_settings
        s = get_settings()
        if s.user_name:
            return s.user_name
    except Exception:
        pass
    return getpass.getuser().capitalize()


def _get_audit_summary() -> str:
    try:
        from macroa.config.settings import get_settings
        from macroa.kernel.audit import AuditLog
        log = AuditLog(db_path=get_settings().audit_db_path)
        stats = log.stats()
        total = stats.get("total_runs", 0)
        if total == 0:
            return "No prior activity — let's get started"
        sessions_count = len(set(
            e.session_id for e in log.recent(500)
        ))
        failures = stats.get("failures", 0)
        parts = [f"{total} turn{'s' if total != 1 else ''}"]
        if sessions_count > 1:
            parts.append(f"{sessions_count} sessions")
        if failures:
            parts.append(f"{failures} failure{'s' if failures != 1 else ''}")
        return "  ·  ".join(parts)
    except Exception:
        return "Ready"


def _build_model_table() -> Table:
    import os
    table = Table(box=None, show_header=False, padding=(0, 1), expand=False)
    table.add_column("tier", style="bold cyan", no_wrap=True)
    table.add_column("model", style="dim")

    tiers = [
        ("NANO",   "MACROA_MODEL_NANO",   "google/gemini-2.5-flash-lite"),
        ("HAIKU",  "MACROA_MODEL_HAIKU",  "anthropic/claude-haiku-4-5"),
        ("SONNET", "MACROA_MODEL_SONNET", "anthropic/claude-sonnet-4-6"),
        ("OPUS",   "MACROA_MODEL_OPUS",   "anthropic/claude-opus-4-6"),
    ]
    for tier, env_key, default in tiers:
        model = os.environ.get(env_key, default)
        # Shorten to provider/name only (drop version noise)
        short = model.split("/")[-1] if "/" in model else model
        table.add_row(f"◈ {tier}", short)

    return table


# ── internal ──────────────────────────────────────────────────────────────────

def _looks_like_markdown(text: str) -> bool:
    markers = ("# ", "## ", "**", "- ", "* ", "```", "> ", "1. ")
    lines = text.splitlines()
    return any(line.startswith(m) for line in lines[:10] for m in markers)
