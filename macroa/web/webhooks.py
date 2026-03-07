"""Webhook store — persistent registry of inbound webhook triggers.

A webhook is a named HTTP endpoint that maps an inbound POST payload to a
kernel.run() call.  The caller authenticates via a secret key embedded in
the URL (?key=...).  The command template is rendered against the incoming
JSON body before being passed to the kernel.

Template syntax (Jinja-style, no dependencies — simple string formatting):
  {{body}}          — full JSON body serialised to a compact string
  {{field}}         — value of top-level JSON key "field"
  {{field.nested}}  — dot-path into the JSON body

Storage: SQLite table in ~/.macroa/webhooks.db
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class WebhookConfig:
    name: str                          # URL slug — POST /webhook/{name}
    command_template: str              # kernel.run() input, may contain {{placeholders}}
    session_id: str                    # kernel session to run under
    secret_key: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    enabled: bool = True
    description: str = ""
    created_at: float = field(default_factory=time.time)
    last_triggered_at: float | None = None
    trigger_count: int = 0
    last_error: str | None = None


class WebhookStore:
    """SQLite-backed registry of webhook configurations."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    # ── schema ────────────────────────────────────────────────────────────────

    def _init(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS webhooks (
                    name              TEXT PRIMARY KEY,
                    command_template  TEXT NOT NULL,
                    session_id        TEXT NOT NULL,
                    secret_key        TEXT NOT NULL,
                    enabled           INTEGER NOT NULL DEFAULT 1,
                    description       TEXT NOT NULL DEFAULT '',
                    created_at        REAL NOT NULL,
                    last_triggered_at REAL,
                    trigger_count     INTEGER NOT NULL DEFAULT 0,
                    last_error        TEXT
                )
            """)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    # ── public API ────────────────────────────────────────────────────────────

    def create(self, wh: WebhookConfig) -> WebhookConfig:
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO webhooks
                    (name, command_template, session_id, secret_key, enabled,
                     description, created_at, last_triggered_at, trigger_count, last_error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                wh.name, wh.command_template, wh.session_id, wh.secret_key,
                int(wh.enabled), wh.description, wh.created_at,
                wh.last_triggered_at, wh.trigger_count, wh.last_error,
            ))
        return wh

    def get(self, name: str) -> WebhookConfig | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM webhooks WHERE name = ?", (name,)
            ).fetchone()
        return _row_to_wh(row) if row else None

    def list_all(self) -> list[WebhookConfig]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM webhooks ORDER BY created_at DESC"
            ).fetchall()
        return [_row_to_wh(r) for r in rows]

    def delete(self, name: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM webhooks WHERE name = ?", (name,))
        return cur.rowcount > 0

    def record_trigger(self, name: str, error: str | None = None) -> None:
        with self._connect() as conn:
            conn.execute("""
                UPDATE webhooks
                SET trigger_count = trigger_count + 1,
                    last_triggered_at = ?,
                    last_error = ?
                WHERE name = ?
            """, (time.time(), error, name))


# ── template rendering ────────────────────────────────────────────────────────


def render_template(template: str, body: dict | str | None) -> str:
    """Render {{placeholder}} tokens against a JSON body dict.

    Supports:
      {{body}}          — full body serialised to compact JSON string
      {{field}}         — top-level key lookup
      {{field.nested}}  — dot-path lookup (up to 5 levels deep)
    """
    if not body:
        body = {}
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except Exception:
            body = {"body": body}

    if not isinstance(body, dict):
        body = {}

    import re

    def replace(m: re.Match) -> str:
        token = m.group(1).strip()
        if token == "body":
            return json.dumps(body, ensure_ascii=False, separators=(",", ":"))
        # dot-path lookup
        parts = token.split(".")
        current = body
        for p in parts:
            if isinstance(current, dict) and p in current:
                current = current[p]
            else:
                return m.group(0)   # leave unreplaced if path not found
        return str(current)

    return re.sub(r"\{\{([^}]+)\}\}", replace, template)


# ── helpers ───────────────────────────────────────────────────────────────────


def _row_to_wh(row: sqlite3.Row) -> WebhookConfig:
    return WebhookConfig(
        name=row["name"],
        command_template=row["command_template"],
        session_id=row["session_id"],
        secret_key=row["secret_key"],
        enabled=bool(row["enabled"]),
        description=row["description"] or "",
        created_at=row["created_at"],
        last_triggered_at=row["last_triggered_at"],
        trigger_count=row["trigger_count"],
        last_error=row["last_error"],
    )
