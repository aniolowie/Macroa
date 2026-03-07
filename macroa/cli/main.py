"""Click CLI — REPL and single-shot modes."""

from __future__ import annotations

import logging
import signal
import sys
import time
from collections.abc import Callable

import click
from rich.prompt import Prompt
from rich.table import Table

import macroa.kernel as kernel
from macroa.cli import wizard as _wizard
from macroa.cli.renderer import (
    console,
    print_banner,
    print_help,
    render_error,
    render_info,
    render_result,
)

logging.basicConfig(level=logging.WARNING)


# ── Research live feed ────────────────────────────────────────────────────────


def _on_research_event(event: Event) -> None:  # type: ignore[name-defined]  # noqa: F821
    """Print a styled progress line for each research pipeline event."""
    p = event.payload
    if event.event_type == "research.phase.start":
        phase = p["phase"]
        name = p["name"]
        if phase == 1:
            query_display = p.get("query", "")[:80]
            console.print(f"\n[bold]Research[/bold]  [dim]{query_display}[/dim]")
            console.print()
        console.print(f"  [bold cyan]Phase {phase}[/bold cyan]  {name}…")
    elif event.event_type == "research.subagent.start":
        n, total, obj = p["subagent_n"], p["total"], p["objective"]
        console.print(f"    [cyan][{n}/{total}][/cyan]  {obj[:90]}")
    elif event.event_type == "research.tool.call":
        tool, arg = p["tool"], p["arg"]
        label = "search" if tool == "web_search" else "fetch "
        arg_display = (arg[:72] + "…") if len(arg) > 72 else arg
        console.print(f"          [dim]↳[/dim] [blue]{label}[/blue]  [dim]{arg_display}[/dim]")
    elif event.event_type == "research.subagent.done":
        n, total, c = p["subagent_n"], p["total"], p["citation_count"]
        src = f"{c} source{'s' if c != 1 else ''}"
        console.print(f"          [dim]✓ [{n}/{total}] {src}[/dim]")


def _register_research_feed() -> None:
    from macroa.kernel.events import Events, bus
    for et in (
        Events.RESEARCH_PHASE_START,
        Events.RESEARCH_SUBAGENT_START,
        Events.RESEARCH_TOOL_CALL,
        Events.RESEARCH_SUBAGENT_DONE,
    ):
        bus.subscribe(et, _on_research_event)


def _on_reminder_fired(event: "Event") -> None:  # type: ignore[name-defined]  # noqa: F821
    """Print a visible banner when a scheduled reminder fires."""
    msg = event.payload.get("message", "")
    from datetime import datetime
    now_str = datetime.now().strftime("%H:%M")
    console.print(
        f"\n[bold yellow]⏰ Reminder [{now_str}][/bold yellow]  {msg}\n",
        highlight=False,
    )


def _register_reminder_notifications() -> None:
    from macroa.kernel.events import Events, bus
    bus.subscribe(Events.REMINDER_FIRED, _on_reminder_fired)


def _make_confirm_callback() -> Callable[[str, str], bool]:
    """Return a Rich-powered sudo confirm callback with a 30 s SIGALRM timeout."""
    def confirm(command: str, reason: str) -> bool:
        def _timeout(signum: int, frame: object) -> None:  # noqa: ARG001
            raise TimeoutError

        old_handler = signal.signal(signal.SIGALRM, _timeout)
        signal.alarm(30)
        try:
            console.print("\n[bold yellow]⚡ sudo[/bold yellow] Agent wants to run:")
            console.print(f"  [bold]{command}[/bold]")
            console.print(f"  [dim]{reason} — auto-denies in 30 s[/dim]")
            answer = Prompt.ask("  Allow?", choices=["y", "n"], default="n")
            return answer == "y"
        except (TimeoutError, KeyboardInterrupt, EOFError):
            console.print("\n[dim]Timed out — denied.[/dim]")
            return False
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)

    return confirm


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
        # Run setup wizard on first use (no API key configured yet)
        if _wizard.needs_setup():
            _wizard.run_wizard()
            # Invalidate settings cache so the newly written key is picked up
            try:
                from macroa.config.settings import get_settings
                get_settings.cache_clear()
            except Exception:
                logging.exception("Failed to clear settings cache after running setup wizard")
        _repl(debug=debug, session_name=session)


@cli.command()
@click.argument("input_text", nargs=-1, required=True)
@click.option("--debug", is_flag=True, default=False, help="Show debug metadata.")
@click.option("--session", default=None, help="Session name or ID.")
def run(input_text: tuple[str, ...], debug: bool, session: str | None) -> None:
    """Run a single command and exit."""
    _register_research_feed()
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


# ------------------------------------------------------------------ schedule subcommand


@cli.group()
def schedule() -> None:
    """Manage scheduled commands."""


@schedule.command("add")
@click.argument("label")
@click.argument("command")
@click.argument("spec")
@click.option("--session", default=None, help="Session name or ID to run under.")
def schedule_add(label: str, command: str, spec: str, session: str | None) -> None:
    """Schedule COMMAND under LABEL with recurrence SPEC.

    \b
    SPEC formats:
      once:<unix_timestamp>        run once at epoch second
      every:<seconds>              repeat every N seconds
      daily:<HH:MM>                every day at HH:MM local time
      cron:<min> <hr> <dom> <mon> <dow>   5-field cron
    """
    try:
        session_id = _resolve_session(session) if session else None
        task = kernel.schedule_add(label=label, command=command, schedule=spec, session_id=session_id)
        render_info(f"Task [bold]{task.label}[/bold] scheduled (ID: {task.task_id[:8]}…)")
    except ValueError as exc:
        render_error(str(exc))


@schedule.command("list")
@click.option("--all", "show_all", is_flag=True, help="Include disabled tasks.")
def schedule_list(show_all: bool) -> None:
    """List scheduled tasks."""
    tasks = kernel.schedule_list(include_disabled=show_all)
    if not tasks:
        render_info("No scheduled tasks.")
        return
    table = Table(title="Scheduled Tasks", show_lines=False)
    table.add_column("Label", style="bold cyan")
    table.add_column("Schedule")
    table.add_column("Next run", style="dim")
    table.add_column("Runs", justify="right")
    table.add_column("ID", style="dim")
    for t in tasks:
        next_r = time.strftime("%Y-%m-%d %H:%M", time.localtime(t.next_run_at))
        table.add_row(t.label, t.schedule, next_r, str(t.run_count), t.task_id[:8] + "…")
    console.print(table)


@schedule.command("delete")
@click.argument("task_id")
def schedule_delete(task_id: str) -> None:
    """Delete a scheduled task by ID prefix."""
    # support prefix matching
    tasks = kernel.schedule_list(include_disabled=True)
    matches = [t for t in tasks if t.task_id.startswith(task_id)]
    if not matches:
        render_error(f"No task found matching '{task_id}'.")
        return
    if len(matches) > 1:
        render_error(f"Ambiguous prefix — {len(matches)} tasks match. Be more specific.")
        return
    kernel.schedule_delete(matches[0].task_id)
    render_info(f"Task '{matches[0].label}' deleted.")


# ------------------------------------------------------------------ REPL


def _repl(debug: bool, session_name: str | None) -> None:
    _register_research_feed()
    _register_reminder_notifications()
    print_banner()
    session_id = _resolve_session(session_name)
    confirm_callback = _make_confirm_callback()
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

        _execute(raw, session_id=session_id, debug=debug_mode, confirm_callback=confirm_callback, stream=True)


# ------------------------------------------------------------------ shared


def _execute(
    raw: str,
    session_id: str,
    debug: bool,
    confirm_callback: Callable[[str, str], bool] | None = None,
    stream: bool = False,
) -> None:
    stream_callback: Callable[[str], None] | None = None
    was_streamed = False

    if stream:
        _streamed: list[str] = []

        def stream_callback(chunk: str) -> None:  # noqa: F811
            nonlocal was_streamed
            was_streamed = True
            console.print(chunk, end="", highlight=False)
            _streamed.append(chunk)

    try:
        result = kernel.run(
            raw,
            session_id=session_id,
            confirm_callback=confirm_callback,
            stream_callback=stream_callback,
        )
        if was_streamed:
            console.print()  # newline after streamed output
        render_result(result, debug=debug, skip_output=was_streamed)
        if not result.success:
            sys.exit(1)
    except OSError as exc:
        render_error(str(exc))
        sys.exit(1)
    except Exception as exc:
        render_error(f"Unexpected error: {exc}")
        if debug:
            import traceback
            traceback.print_exc()
        sys.exit(1)


@cli.command()
@click.argument("source")
@click.option("--force", is_flag=True, default=False, help="Overwrite existing tool.")
def install(source: str, force: bool) -> None:
    """Install a tool from a local path or git URL."""
    from macroa.config.settings import get_settings
    from macroa.tools.installer import InstallError
    from macroa.tools.installer import install as do_install
    try:
        dest = do_install(source, get_settings().tools_dir, force=force)
        render_info(f"Tool installed at [bold]{dest}[/bold]. Restart Macroa to load it.")
    except InstallError as exc:
        render_error(str(exc))
        sys.exit(1)


@cli.command()
@click.argument("name")
def uninstall(name: str) -> None:
    """Uninstall a tool by name."""
    from macroa.config.settings import get_settings
    from macroa.tools.installer import uninstall as do_uninstall
    if do_uninstall(name, get_settings().tools_dir):
        render_info(f"Tool '{name}' uninstalled.")
    else:
        render_error(f"Tool '{name}' not found.")


@cli.group()
def tools() -> None:
    """Manage installed tools."""


@tools.command("list")
def tools_list() -> None:
    """List installed user tools."""
    from macroa.config.settings import get_settings
    from macroa.tools.installer import list_installed
    installed = list_installed(get_settings().tools_dir)
    if not installed:
        render_info("No user tools installed. Use: macroa install <path|url>")
        return
    table = Table(title="Installed Tools", show_lines=False)
    table.add_column("Name", style="bold cyan")
    table.add_column("Description")
    table.add_column("Path", style="dim")
    for t in installed:
        table.add_row(t["name"], t["description"] or "—", t["path"])
    console.print(table)


@cli.group()
def daemon() -> None:
    """Manage the Macroa background daemon."""


@daemon.command("start")
@click.option("--port", default=8000, show_default=True, help="Web API port.")
@click.option("--no-web", is_flag=True, default=False, help="Disable the HTTP API.")
def daemon_start(port: int, no_web: bool) -> None:
    """Start the background daemon (scheduler + watchdog + optional web API)."""
    from macroa.kernel.daemon import is_running, start as daemon_start_fn
    if is_running():
        from macroa.kernel.daemon import pid_file
        pid = pid_file().read_text().strip()
        render_info(f"Daemon already running (PID {pid}).")
        return
    try:
        pid = daemon_start_fn(port=port, web=not no_web)
        web_note = f"  Web API: http://127.0.0.1:{port}" if not no_web else "  Web API: disabled"
        render_info(f"Daemon started (PID {pid}).{web_note}")
    except RuntimeError as exc:
        render_error(str(exc))
        sys.exit(1)


@daemon.command("stop")
def daemon_stop() -> None:
    """Stop the background daemon."""
    from macroa.kernel.daemon import stop as daemon_stop_fn
    if daemon_stop_fn():
        render_info("Daemon stopped.")
    else:
        render_info("No daemon running.")


@daemon.command("status")
def daemon_status() -> None:
    """Show daemon status and stats."""
    from macroa.kernel.daemon import is_running, read_status
    if not is_running():
        console.print("[dim]Daemon:[/dim] [red]offline[/red]")
        return
    st = read_status()
    pid = st.get("pid", "?")
    tasks = st.get("scheduler_tasks", "?")
    web_port = st.get("web_port")
    started = st.get("started_at")
    uptime_str = ""
    if started:
        secs = int(time.time() - started)
        h, rem = divmod(secs, 3600)
        m, s = divmod(rem, 60)
        uptime_str = f"  uptime {h}h {m}m {s}s" if h else f"  uptime {m}m {s}s"
    web_str = f"  web: http://127.0.0.1:{web_port}" if web_port else "  web: disabled"
    console.print(
        f"[dim]Daemon:[/dim] [green]running[/green]  "
        f"PID {pid}  tasks: {tasks}{web_str}{uptime_str}"
    )


@cli.command()
@click.option("--token", default=None, envvar="MACROA_TELEGRAM_TOKEN",
              help="Telegram bot token (or set MACROA_TELEGRAM_TOKEN).")
@click.option("--allow", multiple=True, metavar="USER_ID",
              help="Restrict to these Telegram user IDs (repeat for multiple).")
def telegram(token: str | None, allow: tuple[str, ...]) -> None:
    """Start the Telegram bot adapter (blocks until Ctrl+C)."""
    if not token:
        render_error(
            "No token provided. Pass --token or set MACROA_TELEGRAM_TOKEN.\n"
            "  Get a token from @BotFather on Telegram."
        )
        sys.exit(1)
    from macroa.channels.telegram import TelegramAdapter
    from macroa.channels.base import AdapterError
    import macroa.kernel as kernel

    allowed = set(allow) if allow else None
    adapter = TelegramAdapter(token=token, run_fn=kernel.run, allowed_users=allowed)
    try:
        bot_info = adapter.validate_token()
        render_info(
            f"Telegram bot [bold]@{bot_info['username']}[/bold] connected.\n"
            f"  Start chatting: https://t.me/{bot_info['username']}\n"
            "  Press Ctrl+C to stop."
        )
    except AdapterError as exc:
        render_error(str(exc))
        sys.exit(1)

    adapter.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        adapter.stop()
        render_info("Telegram adapter stopped.")


@cli.command()
@click.option("--token", default=None, envvar="MACROA_DISCORD_TOKEN",
              help="Discord bot token (or set MACROA_DISCORD_TOKEN).")
@click.option("--channel", multiple=True, metavar="CHANNEL_ID",
              help="Channel IDs to listen in (repeat for multiple).")
@click.option("--allow", multiple=True, metavar="USER_ID",
              help="Restrict to these Discord user IDs.")
def discord(token: str | None, channel: tuple[str, ...], allow: tuple[str, ...]) -> None:
    """Start the Discord bot adapter (blocks until Ctrl+C)."""
    if not token:
        render_error(
            "No token provided. Pass --token or set MACROA_DISCORD_TOKEN.\n"
            "  Get a token at discord.com/developers → Your App → Bot."
        )
        sys.exit(1)
    from macroa.channels.discord import DiscordAdapter
    from macroa.channels.base import AdapterError
    import macroa.kernel as kernel

    allowed = set(allow) if allow else None
    channel_ids = list(channel) if channel else None
    adapter = DiscordAdapter(
        token=token, run_fn=kernel.run,
        channel_ids=channel_ids, allowed_users=allowed,
    )
    try:
        bot_info = adapter.validate_token()
        render_info(
            f"Discord bot [bold]{bot_info['username']}[/bold] connected.\n"
            "  Press Ctrl+C to stop."
        )
    except AdapterError as exc:
        render_error(str(exc))
        sys.exit(1)

    adapter.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        adapter.stop()
        render_info("Discord adapter stopped.")


@cli.command()
def setup() -> None:
    """Run the interactive setup wizard (configure API key, name, models)."""
    _wizard.run_wizard(rerun=True)
    try:
        from macroa.config.settings import get_settings
        get_settings.cache_clear()
    except Exception as exc:
        # Cache clearing is a best-effort step; log failures but do not abort setup.
        logging.warning("Failed to clear settings cache during setup: %s", exc)


@cli.command()
@click.option("--host", default="127.0.0.1", show_default=True, help="Bind address.")
@click.option("--port", default=8000, show_default=True, help="Port to listen on.")
@click.option("--reload", is_flag=True, default=False, help="Auto-reload on code change (dev).")
def serve(host: str, port: int, reload: bool) -> None:
    """Start the Macroa HTTP API server (requires pip install macroa[web])."""
    try:
        import uvicorn
    except ImportError:
        render_error("uvicorn not installed. Run: pip install macroa[web]")
        sys.exit(1)
    render_info(f"Starting Macroa API on http://{host}:{port}")
    uvicorn.run("macroa.web.app:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    cli()
