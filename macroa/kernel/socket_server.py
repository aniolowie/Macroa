"""Unix domain socket server — bridges kernel.run() to connected thin clients.

Protocol: newline-delimited JSON in both directions.

Client → Server:
  {"type": "run",           "input": "...", "session_id": "abc"}
  {"type": "clear",         "session_id": "abc"}
  {"type": "sudo_response", "request_id": "xyz", "approved": true}
  {"type": "ping"}

Server → Client:
  {"type": "banner",       "version": "0.3.0", "session_id": "abc"}
  {"type": "chunk",        "content": "..."}
  {"type": "done",         "success": true, "skill": "chat_skill",
                           "tier": "haiku", "turn_id": "...", "error": null,
                           "output": null}
  {"type": "event",        "event_type": "reminder.fired", "payload": {...}}
  {"type": "sudo_confirm", "command": "...", "reason": "...", "request_id": "xyz"}
  {"type": "pong"}

Thread model:
  - asyncio event loop in a daemon thread (start_in_thread)
  - kernel.run() dispatched via loop.run_in_executor()
  - stream_callback bridges sync→async via asyncio.Queue + run_coroutine_threadsafe
  - sudo_confirm bridges async→sync via threading.Event
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import json
import logging
import threading
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

_PUSH_EVENTS = (
    "reminder.fired",
    "research.phase.start",
    "research.subagent.start",
    "research.tool.call",
    "research.subagent.done",
)


def _write_line(writer: asyncio.StreamWriter, obj: dict) -> None:
    writer.write((json.dumps(obj) + "\n").encode())


def _get_version() -> str:
    try:
        return importlib.metadata.version("macroa")
    except Exception:
        return "dev"


class SocketServer:
    """Unix domain socket server wrapping kernel.run()."""

    def __init__(self, socket_path: Path) -> None:
        self._socket_path = socket_path
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server: asyncio.Server | None = None
        self._thread: threading.Thread | None = None
        self._clients: list[asyncio.StreamWriter] = []
        self._started = threading.Event()

    def start_in_thread(self) -> None:
        """Start asyncio event loop + socket server in a daemon thread."""
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name="macroa-socket",
        )
        self._thread.start()
        # Wait up to 3 s for server to be listening
        self._started.wait(timeout=3.0)

    def stop(self) -> None:
        """Stop the server and close the socket."""
        if self._loop and self._server:
            self._loop.call_soon_threadsafe(self._server.close)

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._serve())
        except Exception as exc:
            logger.warning("Socket server loop exited: %s", exc)

    async def _serve(self) -> None:
        self._socket_path.parent.mkdir(parents=True, exist_ok=True)
        # Remove stale socket file
        if self._socket_path.exists():
            try:
                self._socket_path.unlink()
            except OSError:
                pass

        self._server = await asyncio.start_unix_server(
            self._handle_client,
            path=str(self._socket_path),
        )

        # Subscribe to pushable events on the global bus
        from macroa.kernel.events import bus
        for et in _PUSH_EVENTS:
            bus.subscribe(et, self._on_event)

        self._started.set()
        logger.info("Socket server listening on %s", self._socket_path)

        async with self._server:
            await self._server.serve_forever()

    def _on_event(self, event: object) -> None:
        """Called from any thread when a subscribed event fires.
        Pushes a JSON line to every connected client."""
        if self._loop is None or self._loop.is_closed():
            return
        data = (json.dumps({
            "type": "event",
            "event_type": event.event_type,  # type: ignore[attr-defined]
            "payload": event.payload,        # type: ignore[attr-defined]
        }) + "\n").encode()

        async def _push() -> None:
            for writer in list(self._clients):
                try:
                    writer.write(data)
                    await writer.drain()
                except Exception:
                    pass

        asyncio.run_coroutine_threadsafe(_push(), self._loop)

    # ── per-connection handler ────────────────────────────────────────────────

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        session_id = str(uuid.uuid4())
        self._clients.append(writer)

        _write_line(writer, {
            "type": "banner",
            "version": _get_version(),
            "session_id": session_id,
        })
        await writer.drain()

        # request_id → asyncio.Future for pending sudo confirmations
        sudo_futures: dict[str, asyncio.Future[bool]] = {}

        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue

                msg_type = msg.get("type")

                if msg_type == "ping":
                    _write_line(writer, {"type": "pong"})
                    await writer.drain()

                elif msg_type == "clear":
                    sid = msg.get("session_id", session_id)
                    import macroa.kernel as _kernel
                    _kernel.clear_session(sid)
                    _write_line(writer, {
                        "type": "done", "success": True,
                        "skill": None, "tier": None, "turn_id": None,
                        "error": None, "output": None,
                    })
                    await writer.drain()

                elif msg_type == "sudo_response":
                    request_id = msg.get("request_id", "")
                    approved = bool(msg.get("approved", False))
                    fut = sudo_futures.pop(request_id, None)
                    if fut and not fut.done():
                        fut.set_result(approved)

                elif msg_type == "run":
                    if msg.get("session_id"):
                        session_id = msg["session_id"]
                    await self._handle_run(
                        msg, writer,
                        session_id=session_id,
                        sudo_futures=sudo_futures,
                    )

        except (ConnectionResetError, asyncio.IncompleteReadError, BrokenPipeError):
            pass
        finally:
            if writer in self._clients:
                self._clients.remove(writer)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _handle_run(
        self,
        msg: dict,
        writer: asyncio.StreamWriter,
        session_id: str,
        sudo_futures: dict[str, asyncio.Future[bool]],
    ) -> None:
        import macroa.kernel as _kernel

        raw_input: str = msg.get("input", "")
        loop = asyncio.get_running_loop()

        # Queue for streaming chunks (sync → async bridge)
        chunk_queue: asyncio.Queue[str | None] = asyncio.Queue()

        def stream_callback(chunk: str) -> None:
            asyncio.run_coroutine_threadsafe(chunk_queue.put(chunk), loop)

        def confirm_callback(command: str, reason: str) -> bool:
            """Called from executor thread; blocks until client responds or timeout."""
            request_id = str(uuid.uuid4())
            fut: asyncio.Future[bool] = loop.create_future()
            sudo_futures[request_id] = fut

            # Send sudo_confirm to client
            asyncio.run_coroutine_threadsafe(
                _send_sudo_confirm(writer, command, reason, request_id), loop
            )

            # Bridge async future → sync threading.Event
            response_event = threading.Event()
            result_holder: list[bool] = []

            def _on_done(f: asyncio.Future[bool]) -> None:
                try:
                    result_holder.append(f.result())
                except Exception:
                    result_holder.append(False)
                response_event.set()

            loop.call_soon_threadsafe(fut.add_done_callback, _on_done)
            response_event.wait(timeout=30.0)
            return result_holder[0] if result_holder else False

        # Drain chunk queue → send to client
        async def _send_chunks() -> None:
            while True:
                chunk = await chunk_queue.get()
                if chunk is None:
                    break
                _write_line(writer, {"type": "chunk", "content": chunk})
                await writer.drain()

        send_task = asyncio.create_task(_send_chunks())
        try:
            result = await loop.run_in_executor(
                None,
                lambda: _kernel.run(
                    raw_input,
                    session_id=session_id,
                    confirm_callback=confirm_callback,
                    stream_callback=stream_callback,
                ),
            )
        except Exception as exc:
            await chunk_queue.put(None)
            await send_task
            _write_line(writer, {
                "type": "done", "success": False,
                "skill": None, "tier": None, "turn_id": None,
                "error": str(exc), "output": None,
            })
            await writer.drain()
            return

        await chunk_queue.put(None)
        await send_task

        _write_line(writer, {
            "type": "done",
            "success": result.success,
            "skill": result.metadata.get("skill"),
            "tier": result.model_tier.value,
            "turn_id": result.turn_id,
            "error": result.error,
            # include output so client can render non-streamed results
            "output": result.output if result.output else None,
        })
        await writer.drain()


async def _send_sudo_confirm(
    writer: asyncio.StreamWriter,
    command: str,
    reason: str,
    request_id: str,
) -> None:
    _write_line(writer, {
        "type": "sudo_confirm",
        "command": command,
        "reason": reason,
        "request_id": request_id,
    })
    await writer.drain()
