"""
FastAPI web layer for Macroa.

Imports kernel.run() directly — no kernel changes needed.
Streaming via Server-Sent Events (SSE).

Install: pip install macroa[web]
Run:     macroa serve  OR  uvicorn macroa.web.app:app
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator

try:
    from fastapi import FastAPI, HTTPException, Query
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse, StreamingResponse
    from pydantic import BaseModel
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "Web dependencies not installed. Run: pip install macroa[web]"
    ) from exc

from pathlib import Path as _Path

import macroa.kernel as kernel  # noqa: E402 — must come after FastAPI guard above
from macroa.web.webhooks import WebhookConfig, WebhookStore, render_template

_STATIC = _Path(__file__).parent / "static"


def _get_webhook_store() -> WebhookStore:
    from macroa.vfs.layout import MACROA_DIR
    return WebhookStore(db_path=MACROA_DIR / "logs" / "webhooks.db")

app = FastAPI(
    title="Macroa API",
    description="Personal AI OS — HTTP interface to kernel.run()",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ------------------------------------------------------------------ request/response models

class RunRequest(BaseModel):
    input: str
    session: str | None = None    # name or UUID; omit for ephemeral session
    stream: bool = False


class RunResponse(BaseModel):
    output: str
    success: bool
    skill: str | None = None
    tier: str | None = None
    session_id: str
    elapsed_ms: int
    error: str | None = None


class SessionInfo(BaseModel):
    session_id: str
    name: str
    turn_count: int
    updated_at: float


class ScheduleAddRequest(BaseModel):
    label: str
    command: str
    schedule: str               # once:<ts> | every:<s> | daily:<HH:MM> | cron:...
    session: str | None = None


class AuditEntryInfo(BaseModel):
    turn_id: str
    session_id: str
    raw_input: str
    skill_name: str
    model_tier: str
    success: bool
    elapsed_ms: int
    plan_steps: int
    error: str | None = None
    created_at: float
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0


class WebhookCreateRequest(BaseModel):
    name: str                       # URL slug — must be URL-safe
    command_template: str           # kernel input, may contain {{placeholders}}
    session: str | None = None      # name or UUID; omit for new session
    description: str = ""


class WebhookInfo(BaseModel):
    name: str
    command_template: str
    session_id: str
    secret_key: str
    enabled: bool
    description: str
    created_at: float
    last_triggered_at: float | None = None
    trigger_count: int
    last_error: str | None = None


class TaskInfo(BaseModel):
    task_id: str
    label: str
    command: str
    schedule: str
    next_run_at: float
    run_count: int
    enabled: bool
    last_error: str | None = None


# ------------------------------------------------------------------ /run

@app.post("/run", response_model=RunResponse)
def run_sync(req: RunRequest) -> RunResponse:
    """
    Execute a command synchronously and return the full result.
    Use stream=true on the request body to switch to SSE streaming instead.
    """
    if req.stream:
        raise HTTPException(
            status_code=400,
            detail="Use GET /run/stream for streaming, or set stream=false.",
        )
    session_id = _resolve_session(req.session)
    t0 = time.monotonic()
    result = kernel.run(req.input, session_id=session_id)
    elapsed = int((time.monotonic() - t0) * 1000)
    return RunResponse(
        output=result.output,
        success=result.success,
        skill=result.metadata.get("skill"),
        tier=result.model_tier.value,
        session_id=session_id,
        elapsed_ms=elapsed,
        error=result.error,
    )


@app.get("/run/stream")
def run_stream(
    input: str = Query(..., description="The command to run"),
    session: str | None = Query(None, description="Session name or ID"),
) -> StreamingResponse:
    """
    Execute a command and stream the response as Server-Sent Events.

    Each SSE event is `data: <chunk>\\n\\n`.
    A final `data: [DONE]\\n\\n` marks the end of the stream.
    """
    session_id = _resolve_session(session)

    async def _sse_generator() -> AsyncIterator[str]:
        # Kernel.run is synchronous; for true streaming we'd need the streaming
        # LLM path wired through chat_skill. For now we run sync and emit in one
        # chunk — the architecture is ready for real streaming when chat_skill
        # is updated to yield chunks.
        result = kernel.run(input, session_id=session_id)
        text = result.output or (result.error or "")
        # Emit in 512-char chunks to demonstrate SSE framing
        chunk_size = 512
        for i in range(0, max(1, len(text)), chunk_size):
            yield f"data: {text[i:i+chunk_size]}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(_sse_generator(), media_type="text/event-stream")


# ------------------------------------------------------------------ /sessions

@app.get("/sessions", response_model=list[SessionInfo])
def list_sessions() -> list[SessionInfo]:
    return [
        SessionInfo(
            session_id=s.session_id,
            name=s.name,
            turn_count=s.turn_count,
            updated_at=s.updated_at,
        )
        for s in kernel.list_sessions()
    ]


@app.delete("/sessions/{name}")
def delete_session(name: str) -> dict:
    if not kernel.delete_session(name):
        raise HTTPException(status_code=404, detail=f"Session '{name}' not found.")
    return {"deleted": name}


# ------------------------------------------------------------------ /schedule

@app.post("/schedule", response_model=TaskInfo)
def schedule_add(req: ScheduleAddRequest) -> TaskInfo:
    try:
        session_id = _resolve_session(req.session)
        task = kernel.schedule_add(
            label=req.label,
            command=req.command,
            schedule=req.schedule,
            session_id=session_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return _task_info(task)


@app.get("/schedule", response_model=list[TaskInfo])
def schedule_list(include_disabled: bool = False) -> list[TaskInfo]:
    return [_task_info(t) for t in kernel.schedule_list(include_disabled=include_disabled)]


@app.delete("/schedule/{task_id}")
def schedule_delete(task_id: str) -> dict:
    if not kernel.schedule_delete(task_id):
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found.")
    return {"deleted": task_id}


# ------------------------------------------------------------------ /audit

@app.get("/audit/stats")
def audit_stats() -> dict:
    return kernel.get_audit_stats()


@app.get("/audit/recent", response_model=list[AuditEntryInfo])
def audit_recent(n: int = Query(50, ge=1, le=500)) -> list[AuditEntryInfo]:
    """Return the N most recent audit log entries."""
    from macroa.config.settings import get_settings
    from macroa.kernel.audit import AuditLog
    log = AuditLog(db_path=get_settings().audit_db_path)
    return [
        AuditEntryInfo(
            turn_id=e.turn_id,
            session_id=e.session_id,
            raw_input=e.raw_input,
            skill_name=e.skill_name,
            model_tier=e.model_tier,
            success=e.success,
            elapsed_ms=e.elapsed_ms,
            plan_steps=e.plan_steps,
            error=e.error,
            created_at=e.created_at,
            prompt_tokens=e.prompt_tokens,
            completion_tokens=e.completion_tokens,
            cost_usd=e.cost_usd,
        )
        for e in log.recent(n)
    ]


# ------------------------------------------------------------------ /webhooks


@app.post("/webhooks", response_model=WebhookInfo)
def webhook_create(req: WebhookCreateRequest) -> WebhookInfo:
    """Register a new webhook trigger."""
    import re
    if not re.match(r"^[a-zA-Z0-9_-]+$", req.name):
        raise HTTPException(status_code=422, detail="Webhook name must be URL-safe (a-z, 0-9, _, -).")
    store = _get_webhook_store()
    if store.get(req.name):
        raise HTTPException(status_code=409, detail=f"Webhook '{req.name}' already exists.")
    session_id = _resolve_session(req.session)
    wh = store.create(WebhookConfig(
        name=req.name,
        command_template=req.command_template,
        session_id=session_id,
        description=req.description,
    ))
    return _wh_info(wh)


@app.get("/webhooks", response_model=list[WebhookInfo])
def webhook_list() -> list[WebhookInfo]:
    """List all registered webhooks."""
    return [_wh_info(w) for w in _get_webhook_store().list_all()]


@app.delete("/webhooks/{name}")
def webhook_delete(name: str) -> dict:
    """Delete a webhook by name."""
    if not _get_webhook_store().delete(name):
        raise HTTPException(status_code=404, detail=f"Webhook '{name}' not found.")
    return {"deleted": name}


@app.post("/webhook/{name}", response_model=RunResponse)
def webhook_trigger(
    name: str,
    key: str = Query(..., description="Webhook secret key"),
    body: dict | None = None,
) -> RunResponse:
    """
    Trigger a webhook by name.  The caller must supply the correct ?key=.
    The request body (JSON) is rendered into the command template before
    being passed to kernel.run().

    Example:
        POST /webhook/my-hook?key=abc123
        {"event": "push", "repo": "myrepo"}

    With template "summarise github event: {{event}} in {{repo}}"
    → kernel.run("summarise github event: push in myrepo")
    """
    store = _get_webhook_store()
    wh = store.get(name)
    if wh is None:
        raise HTTPException(status_code=404, detail=f"Webhook '{name}' not found.")
    if not wh.enabled:
        raise HTTPException(status_code=403, detail="Webhook is disabled.")
    if key != wh.secret_key:
        raise HTTPException(status_code=401, detail="Invalid webhook key.")

    command = render_template(wh.command_template, body)
    t0 = time.time()
    error: str | None = None
    try:
        result = kernel.run(command, session_id=wh.session_id)
        store.record_trigger(name, error=result.error)
        elapsed = int((time.time() - t0) * 1000)
        return RunResponse(
            output=result.output,
            success=result.success,
            skill=result.metadata.get("skill"),
            tier=result.model_tier.value,
            session_id=wh.session_id,
            elapsed_ms=elapsed,
            error=result.error,
        )
    except Exception as exc:
        error = str(exc)
        store.record_trigger(name, error=error)
        raise HTTPException(status_code=500, detail=error)


# ------------------------------------------------------------------ /dashboard

@app.get("/")
@app.get("/dashboard")
def dashboard() -> FileResponse:
    """Serve the web dashboard."""
    return FileResponse(_STATIC / "dashboard.html")


# ------------------------------------------------------------------ /health

@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": app.version}


# ------------------------------------------------------------------ helpers

def _resolve_session(name_or_id: str | None) -> str:
    if not name_or_id:
        return kernel.get_session_id()
    import re
    uuid_pattern = re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
    )
    if uuid_pattern.match(name_or_id):
        return name_or_id
    return kernel.resolve_session(name_or_id)


def _wh_info(w: WebhookConfig) -> WebhookInfo:
    return WebhookInfo(
        name=w.name,
        command_template=w.command_template,
        session_id=w.session_id,
        secret_key=w.secret_key,
        enabled=w.enabled,
        description=w.description,
        created_at=w.created_at,
        last_triggered_at=w.last_triggered_at,
        trigger_count=w.trigger_count,
        last_error=w.last_error,
    )


def _task_info(t) -> TaskInfo:
    return TaskInfo(
        task_id=t.task_id,
        label=t.label,
        command=t.command,
        schedule=t.schedule,
        next_run_at=t.next_run_at,
        run_count=t.run_count,
        enabled=t.enabled,
        last_error=t.last_error,
    )
