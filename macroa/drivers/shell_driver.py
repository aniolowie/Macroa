"""Shell driver — subprocess execution, never raises."""

from __future__ import annotations

import subprocess

from macroa.stdlib.text import strip_ansi, truncate

_MAX_OUTPUT = 50_000


class ShellDriver:
    def run(self, command: str, timeout: int = 30) -> tuple[int, str, str]:
        """Execute a shell command. Returns (exit_code, stdout, stderr)."""
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            stdout = truncate(strip_ansi(result.stdout), _MAX_OUTPUT)
            stderr = truncate(strip_ansi(result.stderr), _MAX_OUTPUT)
            return result.returncode, stdout, stderr
        except subprocess.TimeoutExpired:
            return 124, "", f"Command timed out after {timeout}s"
        except Exception as exc:
            return 1, "", f"Shell driver error: {exc}"
