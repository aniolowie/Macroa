"""Rich-based terminal renderer."""

from __future__ import annotations

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
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
        "tier.haiku": "dim green",
        "tier.sonnet": "dim yellow",
        "tier.opus": "dim magenta",
    }
)

console = Console(theme=_THEME)

_TIER_STYLE = {
    ModelTier.HAIKU: "tier.haiku",
    ModelTier.SONNET: "tier.sonnet",
    ModelTier.OPUS: "tier.opus",
}

_TIER_LABEL = {
    ModelTier.HAIKU: "haiku",
    ModelTier.SONNET: "sonnet",
    ModelTier.OPUS: "opus",
}


def render_result(result: SkillResult, *, debug: bool = False) -> None:
    if not result.success and result.error:
        console.print(f"[error]Error:[/error] {result.error}")
        return

    output = result.output.strip()
    if not output:
        console.print("[meta](no output)[/meta]")
        return

    # Try to render as Markdown if it contains markdown-like syntax
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
            "[bold]Examples:[/bold]\n"
            "  remember my server IP is 192.168.1.100\n"
            "  what is my server IP?\n"
            "  !ls -la /tmp\n"
            "  think carefully about the tradeoffs of microservices",
            title="Macroa Help",
            border_style="cyan",
        )
    )


def print_banner() -> None:
    console.print(
        "[bold cyan]Macroa[/bold cyan] [dim]— personal AI OS[/dim]\n"
        "[meta]Type [bold]help[/bold] for commands, [bold]exit[/bold] to quit.[/meta]"
    )


def _looks_like_markdown(text: str) -> bool:
    markers = ("# ", "## ", "**", "- ", "* ", "```", "> ", "1. ")
    lines = text.splitlines()
    return any(line.startswith(m) for line in lines[:10] for m in markers)
