# Changelog

All notable changes to Macroa are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/).

---

## [0.2.1] — 2026-03-06

### Added
- **Setup wizard** — `macroa setup` runs an interactive first-run wizard that configures the API key, display name, and model preferences; wizard auto-triggers on first `macroa` invocation when no key is found (`macroa/cli/wizard.py`)
- **Persistent user config** — wizard writes to `~/.macroa/.env` so configuration survives pip reinstalls and works across projects; settings now load `~/.macroa/.env` before any project `.env`
- **`MACROA_USER_NAME`** env var — stored by wizard, shown in banner and available to skills
- **Startup banner** — replaced the bare one-liner with a Rich panel showing version, greeting, model stack (NANO/HAIKU/SONNET/OPUS with current model IDs), and lifetime activity summary
- **`macroa setup` command** — re-run wizard at any time to change API key, name, or model choices

### Changed
- `macroa/cli/renderer.py` — `print_banner()` now renders a two-column panel (identity + model stack) with a quick-reference footer
- `macroa/config/settings.py` — added `user_name` field; loads `~/.macroa/.env` with `override=False` before project `.env`

---

## [0.2.0] — 2026-03-06

### Added
- **Named sessions** — `--session <name>` resolves human-readable names to stable UUIDs; sessions persist to SQLite across process restarts (`macroa/kernel/sessions.py`)
- **Session CLI** — `macroa sessions list` and `macroa sessions delete <name>`
- **Scheduler** — SQLite-backed background task scheduler supporting `once:`, `every:`, `daily:`, and 5-field cron expressions (`macroa/kernel/scheduler.py`)
- **Schedule CLI** — `macroa schedule add/list/delete`
- **FastAPI web layer** — REST API at `http://localhost:8000` with `POST /run`, `GET /run/stream` (SSE), `/sessions`, `/schedule`, `/audit/stats`, `/audit/recent`, `/health`
- **Web dashboard** — dark-mode SPA at `/dashboard` showing usage stats, tier distribution, sessions, scheduled tasks, and audit log
- **`macroa serve`** CLI command — wraps uvicorn; optional `[web]` extras
- **Streaming LLM** — `LLMDriver.stream()` yields text chunks via OpenAI SDK `stream=True`
- **Tool package manager** — `macroa install <path|url>`, `macroa uninstall <name>`, `macroa tools list`; supports local directories and git URLs with optional `#subdir`
- **Thread safety** — `threading.Lock` on kernel session dict
- **Context persistence** — context entries auto-saved after every `kernel.run()` and restored on session resume

### Changed
- `--session` flag now accepts human-readable names in addition to UUIDs
- `pyproject.toml` — added classifiers, URLs, proper author field, `[web]` extras, coverage config

### Fixed
- SQLite `PRAGMA foreign_keys = ON` — session delete now correctly cascades to context entries

---

## [0.1.0] — 2026-02-01

### Added
- **Kernel** — Router (NANO), Planner (NANO), Dispatcher with escalation loop, Combiner (HAIKU)
- **4-tier model system** — NANO / HAIKU / SONNET / OPUS mapped to hardware analogy (microcontroller → GPU)
- **Drivers** — LLM (OpenRouter via openai SDK), Shell, Filesystem, Memory (SQLite v2 with facts + episodes), Network (stdlib only)
- **Skills** — `shell_skill` (hard-route via `!`), `file_skill`, `memory_skill`, `chat_skill`
- **Tools system** — `BaseTool`, `ToolManifest`, `ToolRegistry`, `ToolRunner` (timeout isolation), `HeartbeatManager` (persistent tool daemon)
- **Memory v2** — `facts` table with confidence, source, and expiry; `episodes` table for session summaries; schema versioning
- **EventBus** — thread-safe pub/sub singleton; `KERNEL_RUN_START`, `KERNEL_RUN_COMPLETE`, `PLAN_CREATED` events
- **AuditLog** — every `kernel.run()` automatically recorded to `~/.macroa/audit.db`
- **CLI** — REPL (`macroa`) and single-shot (`macroa run "..."`) with Rich rendering, debug mode, built-in commands
- **Reference tool** — Twilio phone call example at `macroa/tools/examples/call_me/`
- 108 unit tests, zero external dependencies required
