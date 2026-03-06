"""
Tool package manager — macroa install <source>

Supported sources:
  - Local directory path  : macroa install /path/to/my_tool
  - Git repository URL    : macroa install https://github.com/user/repo
  - Git URL + subdirectory: macroa install https://github.com/user/repo#tools/my_tool

Installation layout:
  ~/.macroa/tools/<tool_name>/
    tool.py          (required)
    .env             (optional, auto-loaded by ToolRegistry)
    *.py / other files

Security model:
  - Tools run with the same user privileges as Macroa itself
  - No sandboxing beyond Python process isolation and ToolRunner timeout
  - Users must vet tool code before installing (same as pip install)
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class InstallError(Exception):
    pass


def install(source: str, tools_dir: Path, force: bool = False) -> Path:
    """
    Install a tool from a local path or git URL.

    Returns the installed tool directory.
    Raises InstallError on failure.
    """
    tools_dir.mkdir(parents=True, exist_ok=True)

    if _looks_like_url(source):
        return _install_from_git(source, tools_dir, force)
    else:
        return _install_from_local(Path(source).expanduser().resolve(), tools_dir, force)


def uninstall(name: str, tools_dir: Path) -> bool:
    """Remove an installed tool. Returns True if found and deleted."""
    target = tools_dir / name
    if not target.exists():
        return False
    shutil.rmtree(target)
    return True


def list_installed(tools_dir: Path) -> list[dict]:
    """Return info about all installed tools."""
    if not tools_dir.exists():
        return []
    results = []
    for d in sorted(tools_dir.iterdir()):
        if d.is_dir() and (d / "tool.py").exists():
            info = {"name": d.name, "path": str(d)}
            env_file = d / ".env"
            info["has_env"] = env_file.exists()
            # Try to extract MANIFEST description without importing
            info["description"] = _read_manifest_description(d / "tool.py")
            results.append(info)
    return results


# ------------------------------------------------------------------ internal

def _looks_like_url(source: str) -> bool:
    return source.startswith(("http://", "https://", "git@", "git://"))


def _install_from_local(src: Path, tools_dir: Path, force: bool) -> Path:
    if not src.exists():
        raise InstallError(f"Source path does not exist: {src}")
    if not (src / "tool.py").exists():
        raise InstallError(f"No tool.py found in {src}. Not a valid Macroa tool.")

    name = src.name
    dest = tools_dir / name

    if dest.exists():
        if not force:
            raise InstallError(
                f"Tool '{name}' already installed at {dest}. Use --force to overwrite."
            )
        shutil.rmtree(dest)

    shutil.copytree(src, dest)
    logger.info("Installed tool '%s' from %s", name, src)
    return dest


def _install_from_git(url: str, tools_dir: Path, force: bool) -> Path:
    """Clone a git repo (or a subdirectory within it) into tools_dir."""
    # Parse optional #subdir fragment
    subdir: Optional[str] = None
    if "#" in url:
        url, subdir = url.split("#", 1)

    if not shutil.which("git"):
        raise InstallError("git is required for URL installs but was not found in PATH.")

    with tempfile.TemporaryDirectory(prefix="macroa-install-") as tmp:
        tmp_path = Path(tmp)
        logger.info("Cloning %s …", url)
        try:
            subprocess.run(
                ["git", "clone", "--depth=1", url, str(tmp_path / "repo")],
                check=True,
                capture_output=True,
                timeout=60,
            )
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.decode(errors="replace")
            raise InstallError(f"git clone failed:\n{stderr}") from exc
        except subprocess.TimeoutExpired:
            raise InstallError("git clone timed out (60s).")

        src = tmp_path / "repo"
        if subdir:
            src = src / subdir

        return _install_from_local(src, tools_dir, force)


def _read_manifest_description(tool_py: Path) -> str:
    """Extract the description string from MANIFEST without importing the file."""
    import re
    try:
        text = tool_py.read_text(errors="replace")
        # Match description="..." or description='...' anywhere in the file
        m = re.search(r'description\s*=\s*["\']([^"\']+)["\']', text)
        if m:
            return m.group(1)
    except Exception:
        pass
    return ""
