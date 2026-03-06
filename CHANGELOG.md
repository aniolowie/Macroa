# Changelog

All notable changes to Macroa are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/).

---

## [0.2.7] — 2026-03-06

### Fixed
- **`agent_skill` registry warning** — stub `run()` added so skill registry loads the module without "run() is missing" warning; kernel still dispatches agent turns directly via `AgentLoop`
- **Tool capabilities in system prompt** — `_CAPABILITIES_SECTION` always appended to post-onboarding system prompt so Hammond describes actual Macroa tools instead of generic LLM capabilities; workspace path `~/.macroa/` included
- **Shell skill UX** — missing command now returns a helpful prompt instead of a bare error

---

## [0.2.5] — 2026-03-06

### Added
- **Agent skill** (`macroa/kernel/agent.py`, `macroa/skills/agent_skill.py`) — agentic tool-calling loop powered by OpenAI function calling (no new deps). Tools: `write_file`, `read_file`, `run_command`, `remember`, `recall`. Runs until the LLM stops invoking tools or hits a 10-round safety cap.
- **sudo permission tier** (`macroa/kernel/sudo.py`) — every `run_command` call is classified SAFE / ELEVATED / BLOCKED before execution. ELEVATED commands pause the agent and prompt the user in the REPL with a 30 s SIGALRM timeout; auto-deny on timeout. Approved pattern types persist for the session (e.g. approving `rm` once covers all `rm` calls that session). Blocked patterns (disk wipe, fork bomb, remote code execution) are rejected unconditionally.
- **First-boot agent mode** — kernel detects missing `~/.macroa/IDENTITY.md` and overrides every turn to `agent_skill`, giving Hammond tools to write identity files directly during onboarding.
- **`ROUTE_DECISION`, `SUDO_REQUEST`, `SUDO_RESULT`, `AGENT_TOOL_CALL`** event constants added to `Events`.

### Improved (router)
- **Keyword shortcut layer** — before calling the LLM, check all skill triggers; if exactly one non-chat skill matches unambiguously, route there at confidence 0.95 with zero API cost.
- **HAIKU retry on low confidence** — if NANO returns confidence < 0.5 on a non-chat result, automatically retry with HAIKU and take the higher-confidence answer.
- **Few-shot examples** in the routing system prompt — 5 inline input→JSON examples improve JSON compliance and accuracy.
- **Parameter completeness check** — logs a warning when the LLM omits required parameters (e.g. `path` for `file_skill`), making gaps visible without crashing.
- **Context continuity hint** — prompt explicitly instructs the router to prefer skill continuity when the user is mid-task.
- **`ROUTE_DECISION` event** emitted after every routing decision for dashboard/audit visibility.

---

## [0.2.4] — 2026-03-06

### Fixed
- **Router JSON parse failure** — LLM driver now passes `response_format={"type": "json_object"}` to the API when `expect_json=True`, forcing structured output instead of relying on text instructions alone. Eliminates the `"Expecting value: line 1 column 1 (char 0)"` warning that caused every turn to fall back to `chat_skill`.
- **Markdown fence stripping** — added `_extract_json()` in `router.py` to strip ` ```json ` / ` ``` ` fences before parsing, covering models that ignore the "no fences" instruction.

### Added
- **Identity layer** (`macroa/kernel/identity.py`) — `build_system_prompt()` loads `~/.macroa/IDENTITY.md`, `USER.md`, and `SOUL.md` as the live system prompt. On first boot (no `IDENTITY.md`), writes and loads `~/.macroa/BOOTSTRAP.md` which guides an onboarding conversation to establish the AI's name, personality, and emoji.
- **Memory-aware chat** — `chat_skill` now searches the memory driver for facts relevant to each query and appends them to the system prompt, so "what do you know about me" returns stored facts even when routed as a general chat turn.

---

## [0.2.3] — 2026-03-06

### Fixed
- Include `macroa/web/static/*` in `pyproject.toml` package-data so `dashboard.html` is bundled in the wheel

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
