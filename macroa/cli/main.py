"""Click CLI — REPL and single-shot modes."""

from __future__ import annotations

import sys
import logging

import click
from rich.prompt import Prompt

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


@click.group(invoke_without_command=True)
@click.pass_context
@click.option("--debug", is_flag=True, default=False, help="Show debug metadata in output.")
@click.option("--session", default=None, help="Session ID for context continuity.")
def cli(ctx: click.Context, debug: bool, session: str | None) -> None:
    """Macroa — personal AI OS."""
    if ctx.invoked_subcommand is None:
        _repl(debug=debug, session_id=session)


@cli.command()
@click.argument("input_text", nargs=-1, required=True)
@click.option("--debug", is_flag=True, default=False, help="Show debug metadata.")
@click.option("--session", default=None, help="Session ID.")
def run(input_text: tuple[str, ...], debug: bool, session: str | None) -> None:
    """Run a single command and exit."""
    raw = " ".join(input_text)
    session_id = session or kernel.get_session_id()
    _execute(raw, session_id=session_id, debug=debug)


# ------------------------------------------------------------------ REPL


def _repl(debug: bool, session_id: str | None) -> None:
    print_banner()
    session_id = session_id or kernel.get_session_id()
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
