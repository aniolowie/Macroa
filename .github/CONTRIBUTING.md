# Contributing to Macroa

## Setup

```bash
git clone https://github.com/aniolowie/Macroa.git
cd Macroa
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # add your OPENROUTER_API_KEY
```

## Before opening a PR

```bash
ruff check macroa/ tests/        # lint
pytest tests/ -q                 # all 170+ tests must pass
```

Tests mock all LLM calls — no API key is needed to run them.

## Project conventions

- **Deterministic ops never call an LLM.** If you're adding a skill or feature that can be implemented without AI, implement it without AI.
- **Never raise in `run()` / `execute()`.** Skills and tools must always return a `SkillResult`, even on failure. Let the kernel handle escalation.
- **New drivers go in `macroa/drivers/`.** New skills go in `macroa/skills/`. New kernel primitives go in `macroa/kernel/`.
- **Write tests.** New features need tests. Aim to keep coverage above 70%.
- **Keep the dependency footprint small.** Core Macroa has 5 runtime dependencies. Web extras are optional. Don't add to core without a strong reason.

## Adding a tool (userspace program)

See the tool writing guide in the README and the reference implementation at `macroa/tools/examples/call_me/`.

## Commit style

```
feat(scope): short description
fix(scope): short description
docs: short description
test: short description
refactor(scope): short description
```

## Opening issues

Use the issue templates. For bugs, include the output of `macroa --debug run "..."`.
