# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| `0.x` (current) | ✅ |

## Reporting a vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Please report security vulnerabilities by opening a [GitHub Security Advisory](https://github.com/aniolowie/Macroa/security/advisories/new). This keeps the report private until a fix is ready.

Include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix if you have one

You can expect an initial response within **72 hours** and a fix or remediation plan within **14 days** for confirmed issues.

## Security model

Macroa is a local-first tool. Key boundaries:

- **API keys** are read from `.env` and never logged or transmitted beyond the configured API endpoint (OpenRouter).
- **Tool execution** — user-installed tools run with the same OS privileges as the Macroa process itself. Only install tools you trust, the same way you would `pip install`.
- **Shell skill** — commands prefixed with `!` run as subprocesses under your user account.
- **Filesystem access** — the `fs` driver rejects paths outside `$HOME`.
- **Network** — outbound HTTP only (to OpenRouter and tool-specific APIs). No inbound ports are opened unless you run `macroa serve`.
- **`macroa serve`** — the HTTP API binds to `127.0.0.1` by default. Binding to `0.0.0.0` exposes it to the network; add authentication before doing so in untrusted environments.
