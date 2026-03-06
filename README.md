# Macroa

A personal AI OS built on traditional operating system principles. The kernel routes intent, drivers adapt to external systems, skills execute deterministic operations, and tools run as isolated userspace programs.

**Core design rule:** deterministic operations never call an LLM. AI is reserved for ambiguity, reasoning, and generation — keeping costs low and behavior predictable.

---

## Architecture

```
User input
    │
    ▼
┌─────────────────────────────────────────────────────┐
│  CLI  (macroa / macroa run "...")                   │
└───────────────────────────┬─────────────────────────┘
                            │ kernel.run()
                            ▼
┌─────────────────────────────────────────────────────┐
│  KERNEL                                             │
│  Router(NANO) → hard-route (!cmd) or LLM classify  │
│  Planner(NANO) → decompose complex tasks            │
│  Dispatcher → skill.run() with escalation loop     │
│  Combiner(HAIKU) → assemble multi-step results      │
│  AuditLog → every call recorded automatically       │
│  EventBus → pub/sub for tools and heartbeat         │
└──────────────┬──────────────────────────────────────┘
               │
     ┌─────────┴──────────┐
     ▼                    ▼
┌─────────┐     ┌──────────────────────────────────┐
│ SKILLS  │     │ TOOLS (userspace programs)       │
│ shell   │     │ ~/.macroa/tools/<name>/tool.py   │
│ file    │     │ BaseTool: setup/execute/heartbeat │
│ memory  │     │ HeartbeatManager (persistent)    │
│ chat    │     └──────────────────────────────────┘
└────┬────┘
     ▼
┌─────────────────────────────────────────────────────┐
│  DRIVERS (hardware abstraction)                     │
│  llm     → OpenRouter (NANO/HAIKU/SONNET/OPUS)     │
│  shell   → subprocess, stdout/stderr capture        │
│  fs      → filesystem, $HOME-scoped                 │
│  memory  → SQLite (facts + episodes) or JSON        │
│  network → HTTP client (stdlib, no extra deps)      │
└─────────────────────────────────────────────────────┘
```

### Model tiers — hardware analogy

| Tier | Hardware | Default model | Role |
|------|----------|---------------|------|
| `NANO` | Microcontroller | `google/gemini-2.5-flash-lite` | All routing, trivial ops |
| `HAIKU` | Efficiency cores (E-cores) | `anthropic/claude-haiku-4-5` | Lightweight tasks |
| `SONNET` | Performance cores (P-cores) | `anthropic/claude-sonnet-4-6` | Quality work |
| `OPUS` | GPU | `anthropic/claude-opus-4-6` | Heavy reasoning |

Escalation is automatic: if a skill returns `needs_reasoning=True`, the kernel promotes to the next tier and retries — up to two escalations per call.

### Memory layers

| Layer | Storage | Contents |
|-------|---------|----------|
| Working | RAM (`ContextManager` deque) | Current conversation turns |
| Semantic | SQLite `facts` table | User facts, preferences — expirable, confidence-scored |
| Episodic | SQLite `episodes` table | Session summaries, searchable by topic |

---

## Installation

```bash
git clone <repo-url>
cd macroa
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

cp .env.example .env
# Edit .env and add: OPENROUTER_API_KEY=sk-or-v1-...
```

---

## Usage

### Single-shot

```bash
# Shell hard-route — zero LLM calls
macroa run "!ls -la /tmp"
macroa run "!uname -a"

# Memory (deterministic skill)
macroa run "remember my server IP is 192.168.1.100"
macroa run "what is my server IP"

# Chat (routes to chat_skill via NANO router)
macroa run "what is the capital of France"

# Force a higher tier with keywords
macroa run "think carefully about the tradeoffs of microservices"
macroa run "use the best model to analyze this architecture"

# Debug metadata
macroa --debug run "explain async/await in Python"
```

### REPL

```bash
macroa
```

Built-in commands: `clear` · `debug` · `help` · `exit` / `quit` / `q`

```
macroa> remember my favorite editor is neovim
macroa> what's my favorite editor?
macroa> !df -h
macroa> debug
macroa> think carefully about the CAP theorem
macroa> exit
```

---

## Writing a tool

Drop a directory into `~/.macroa/tools/` — it's picked up automatically on the next run:

```
~/.macroa/tools/
  my_tool/
    tool.py      ← required
    .env         ← optional: MY_API_KEY=... (loaded automatically)
    helpers.py   ← optional: any other files
```

**`tool.py` template:**

```python
from macroa.tools.base import BaseTool, ToolManifest
from macroa.stdlib.schema import Context, DriverBundle, Intent, SkillResult

MANIFEST = ToolManifest(
    name="my_tool",
    description="Does X when the user asks for Y.",
    triggers=["do X", "run Y"],
    persistent=False,   # True = heartbeat() fires every 60s
    timeout=30,
)

class MyTool(BaseTool):
    def setup(self, drivers):
        pass  # called once on load

    def execute(self, intent: Intent, context: Context, drivers: DriverBundle) -> SkillResult:
        # never raise — always return SkillResult
        return SkillResult(output="done", success=True, turn_id=intent.turn_id)

    def heartbeat(self, drivers):
        pass  # only called if persistent=True

    def teardown(self, drivers):
        pass  # called on shutdown
```

See `macroa/tools/examples/call_me/` for a complete reference implementation (Twilio phone call).

---

## Inspect runtime state

```bash
# Audit log — what ran, which tier, how long
python3 -c "
import macroa.kernel as k, json
print(json.dumps(k.get_audit_stats(), indent=2))
"

# Memory dump — human-readable markdown
python3 -c "
from macroa.config.settings import get_settings
from macroa.drivers.memory_driver import MemoryDriver
print(MemoryDriver(db_path=get_settings().memory_db_path).export_markdown())
"
```

---

## Tests

```bash
pytest tests/ -v
pytest tests/ --cov=macroa --cov-report=term-missing
```

108 tests, zero external dependencies required (unit tests mock all LLM calls).

---

## Configuration

All settings are environment variables. Copy `.env.example` for the full reference.

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENROUTER_API_KEY` | — | **Required** |
| `MACROA_MODEL_NANO` | `google/gemini-2.5-flash-lite` | Routing tier |
| `MACROA_MODEL_HAIKU` | `anthropic/claude-haiku-4-5` | E-cores tier |
| `MACROA_MODEL_SONNET` | `anthropic/claude-sonnet-4-6` | P-cores tier |
| `MACROA_MODEL_OPUS` | `anthropic/claude-opus-4-6` | GPU tier |
| `MACROA_CONTEXT_WINDOW` | `20` | Rolling turns kept in RAM |
| `MACROA_MEMORY_BACKEND` | `sqlite` | `sqlite` or `json` |
| `MACROA_TOOLS_DIR` | `~/.macroa/tools` | User-installed tools |
| `MACROA_HEARTBEAT_INTERVAL` | `60` | Seconds between heartbeat ticks |

---

## Project layout

```
macroa/
├── stdlib/
│   ├── schema.py          # All shared dataclasses
│   └── text.py            # Deterministic string utilities
├── config/
│   ├── settings.py        # Env/settings singleton
│   └── skill_registry.py  # Auto-discovery of skills/tools
├── drivers/
│   ├── llm_driver.py      # OpenRouter via openai SDK
│   ├── shell_driver.py
│   ├── fs_driver.py
│   ├── memory_driver.py   # SQLite (facts + episodes) or JSON
│   └── network_driver.py  # HTTP client (stdlib)
├── skills/                # Kernel built-ins (deterministic)
│   ├── shell_skill.py
│   ├── file_skill.py
│   ├── memory_skill.py
│   └── chat_skill.py
├── kernel/
│   ├── context.py         # Rolling context window
│   ├── escalation.py      # Tier selection and promotion
│   ├── router.py          # Intent classification
│   ├── planner.py         # Task decomposition
│   ├── dispatcher.py      # Escalation loop
│   ├── events.py          # Pub/sub event bus
│   ├── audit.py           # Immutable run log
│   └── __init__.py        # Public: kernel.run()
├── tools/
│   ├── base.py            # BaseTool + ToolManifest
│   ├── registry.py        # Tool discovery and loading
│   ├── runner.py          # Timeout + error isolation
│   ├── heartbeat.py       # Persistent tool daemon
│   └── examples/
│       └── call_me/       # Reference implementation
└── cli/
    ├── renderer.py        # Rich terminal output
    └── main.py            # Click CLI + REPL
```

---

## Phase 2 (planned)

- FastAPI web layer importing `kernel.run()` — no kernel changes needed
- Streaming LLM responses
- Named/persistent sessions
- Scheduler (`remind me at 3pm`)
- Vector memory (semantic search via embeddings)
- Tool package manager (`macroa install <url>`)
- Web dashboard with cost tracking (audit log is already the data source)
