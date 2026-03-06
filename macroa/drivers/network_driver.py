"""Network driver — HTTP I/O for tools and skills.

Uses only Python stdlib (urllib) — no extra dependencies.
All methods return NetworkResponse; never raise.

Tools should use this driver rather than importing requests/httpx directly
so that the kernel can apply rate limiting, logging, and retries uniformly
in Phase 2.
"""

from __future__ import annotations

import json as _json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any


@dataclass
class NetworkResponse:
    status_code: int
    body: str
    headers: dict[str, str]
    success: bool
    error: str | None = None

    def json(self) -> Any:
        """Parse body as JSON. Raises ValueError on invalid JSON."""
        return _json.loads(self.body)

    def json_safe(self, default: Any = None) -> Any:
        """Parse body as JSON, returning default on failure."""
        try:
            return _json.loads(self.body)
        except (ValueError, TypeError):
            return default


class NetworkDriver:
    def __init__(self, timeout: int = 30) -> None:
        self._timeout = timeout

    def get(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> NetworkResponse:
        req = urllib.request.Request(url, headers=headers or {}, method="GET")
        return self._execute(req, timeout or self._timeout)

    def post(
        self,
        url: str,
        json: dict | None = None,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> NetworkResponse:
        h = dict(headers or {})
        body: bytes | None = data
        if json is not None:
            body = _json.dumps(json).encode()
            h.setdefault("Content-Type", "application/json")
        req = urllib.request.Request(url, data=body, headers=h, method="POST")
        return self._execute(req, timeout or self._timeout)

    def post_form(
        self,
        url: str,
        fields: dict[str, str],
        headers: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> NetworkResponse:
        h = dict(headers or {})
        h.setdefault("Content-Type", "application/x-www-form-urlencoded")
        body = urllib.parse.urlencode(fields).encode()
        req = urllib.request.Request(url, data=body, headers=h, method="POST")
        return self._execute(req, timeout or self._timeout)

    def _execute(self, req: urllib.request.Request, timeout: int) -> NetworkResponse:
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                return NetworkResponse(
                    status_code=resp.status,
                    body=body,
                    headers=dict(resp.headers),
                    success=200 <= resp.status < 300,
                )
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                body = ""
            return NetworkResponse(
                status_code=exc.code,
                body=body,
                headers=dict(exc.headers) if exc.headers else {},
                success=False,
                error=f"HTTP {exc.code}: {exc.reason}",
            )
        except urllib.error.URLError as exc:
            return NetworkResponse(
                status_code=0, body="", headers={}, success=False,
                error=f"URL error: {exc.reason}",
            )
        except TimeoutError:
            return NetworkResponse(
                status_code=0, body="", headers={}, success=False,
                error=f"Request timed out after {timeout}s",
            )
        except Exception as exc:
            return NetworkResponse(
                status_code=0, body="", headers={}, success=False,
                error=f"Network error: {exc}",
            )
