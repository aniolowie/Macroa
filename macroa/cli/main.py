"""Click CLI — REPL and single-shot modes."""

from __future__ import annotations

import sys
import logging
import time

import click
from rich.prompt import Prompt
from rich.table import Table

import macroa.kernel as kernel
from macroa.cli.renderer import (
    console,
    print_banner,
    print_help,
    render_error,
    render_info,
    render_result,
)

logging.basicConfig(level=logging.WARNING)


def _resolve_session(name_or_id: str | None) -> str:
    """If a name is provided, resolve it to a session UUID. Otherwise create a new UUID."""
    if not name_or_id:
        return kernel.get_session_id()
    # If it looks like a UUID, use it directly; otherwise treat as a name
    import re
    uuid_pattern = re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
    )
    if uuid_pattern.match(name_or_id):
        return name_or_id
    return kernel.resolve_session(name_or_id)


@click.group(invoke_without_command=True)
@click.pass_context
@click.option("--debug", is_flag=True, default=False, help="Show debug metadata in output.")
@click.option("--session", default=None, help="Session name or ID for context continuity.")
def cli(ctx: click.Context, debug: bool, session: str | None) -> None:
    """Macroa — personal AI OS."""
    if ctx.invoked_subcommand is None:
        _repl(debug=debug, session_name=session)


@cli.command()
@click.argument("input_text", nargs=-1, required=True)
@click.option("--debug", is_flag=True, default=False, help="Show debug metadata.")
@click.option("--session", default=None, help="Session name or ID.")
def run(input_text: tuple[str, ...], debug: bool, session: str | None) -> None:
    """Run a single command and exit."""
    raw = " ".join(input_text)
    session_id = _resolve_session(session)
    _execute(raw, session_id=session_id, debug=debug)


# ------------------------------------------------------------------ sessions subcommand


@cli.group()
def sessions() -> None:
    """Manage named sessions."""


@sessions.command("list")
def sessions_list() -> None:
    """List all named sessions."""
    all_sessions = kernel.list_sessions()
    if not all_sessions:
        render_info("No named sessions found.")
        return
    table = Table(title="Macroa Sessions", show_lines=False)
    table.add_column("Name", style="bold cyan")
    table.add_column("Turns", justify="right")
    table.add_column("Last active", style="dim")
    table.add_column("ID", style="dim")
    for s in all_sessions:
        last = time.strftime("%Y-%m-%d %H:%M", time.localtime(s.updated_at))
        table.add_row(s.name, str(s.turn_count), last, s.session_id[:8] + "…")
    console.print(table)


@sessions.command("delete")
@click.argument("name")
def sessions_delete(name: str) -> None:
    """Delete a named session and its context."""
    if kernel.delete_session(name):
        render_info(f"Session '{name}' deleted.")
    else:
        render_error(f"Session '{name}' not found.")


# ------------------------------------------------------------------ REPL


def _repl(debug: bool, session_name: str | None) -> None:
    print_banner()
    session_id = _resolve_session(session_name)
    if session_name:
        render_info(f"Resuming session: [bold]{session_name}[/bold]")
    debug_mode = debug

    while True:
        try:
            raw = Prompt.ask("[bold cyan]macroa[/bold cyan]")
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Bye.[/dim]")
            sys.exit(0)

        raw = raw.strip()
        if not raw:
            continue

        # Built-in commands
        if raw.lower() in ("exit", "quit", "q"):
            kernel.shutdown()
            console.print("[dim]Bye.[/dim]")
            sys.exit(0)

        if raw.lower() == "clear":
            kernel.clear_session(session_id)
            render_info("Context cleared.")
            continue

        if raw.lower() == "debug":
            debug_mode = not debug_mode
            render_info(f"Debug mode {'ON' if debug_mode else 'OFF'}.")
            continue

        if raw.lower() == "help":
            print_help()
            continue

        _execute(raw, session_id=session_id, debug=debug_mode)


# ------------------------------------------------------------------ shared


def _execute(raw: str, session_id: str, debug: bool) -> None:
    try:
        result = kernel.run(raw, session_id=session_id)
        render_result(result, debug=debug)
        if not result.success:
            sys.exit(1)
    except EnvironmentError as exc:
        render_error(str(exc))
        sys.exit(1)
    except Exception as exc:
        render_error(f"Unexpected error: {exc}")
        if debug:
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    cli()
