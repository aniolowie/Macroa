"""Tests for the tool package manager."""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from macroa.tools.installer import (
    InstallError,
    install,
    list_installed,
    uninstall,
    _read_manifest_description,
)


# ------------------------------------------------------------------ helpers

def _make_tool(base: Path, name: str, description: str = "A test tool") -> Path:
    """Create a minimal valid tool directory."""
    d = base / name
    d.mkdir(parents=True)
    (d / "tool.py").write_text(f'''
from macroa.tools.base import BaseTool, ToolManifest
MANIFEST = ToolManifest(name="{name}", description="{description}", triggers=[])
class MyTool(BaseTool):
    def execute(self, intent, context, drivers): ...
''')
    return d


# ------------------------------------------------------------------ local install

def test_install_local(tmp_path):
    src = _make_tool(tmp_path / "src", "my_tool")
    dest_base = tmp_path / "tools"
    result = install(str(src), dest_base)
    assert (result / "tool.py").exists()
    assert result.name == "my_tool"


def test_install_local_no_tool_py(tmp_path):
    src = tmp_path / "bad_tool"
    src.mkdir()
    with pytest.raises(InstallError, match="No tool.py"):
        install(str(src), tmp_path / "tools")


def test_install_local_missing_src(tmp_path):
    with pytest.raises(InstallError, match="does not exist"):
        install(str(tmp_path / "nonexistent"), tmp_path / "tools")


def test_install_local_force_overwrite(tmp_path):
    src = _make_tool(tmp_path / "src", "my_tool", "v1")
    tools_dir = tmp_path / "tools"
    install(str(src), tools_dir)
    # Modify source to v2
    (src / "tool.py").write_text('# v2\nfrom macroa.tools.base import BaseTool, ToolManifest\nMANIFEST = ToolManifest(name="my_tool", description="v2", triggers=[])\nclass T(BaseTool):\n    def execute(self, i, c, d): ...\n')
    result = install(str(src), tools_dir, force=True)
    assert "v2" in (result / "tool.py").read_text()


def test_install_no_force_raises_on_duplicate(tmp_path):
    src = _make_tool(tmp_path / "src", "my_tool")
    tools_dir = tmp_path / "tools"
    install(str(src), tools_dir)
    with pytest.raises(InstallError, match="already installed"):
        install(str(src), tools_dir, force=False)


# ------------------------------------------------------------------ uninstall

def test_uninstall(tmp_path):
    src = _make_tool(tmp_path / "src", "my_tool")
    tools_dir = tmp_path / "tools"
    install(str(src), tools_dir)
    assert uninstall("my_tool", tools_dir) is True
    assert not (tools_dir / "my_tool").exists()


def test_uninstall_nonexistent(tmp_path):
    assert uninstall("ghost_tool", tmp_path / "tools") is False


# ------------------------------------------------------------------ list_installed

def test_list_installed(tmp_path):
    tools_dir = tmp_path / "tools"
    _make_tool(tools_dir, "tool_a", "Does A")
    _make_tool(tools_dir, "tool_b", "Does B")
    found = list_installed(tools_dir)
    names = [t["name"] for t in found]
    assert "tool_a" in names
    assert "tool_b" in names


def test_list_installed_empty(tmp_path):
    assert list_installed(tmp_path / "empty") == []


def test_list_installed_skips_non_dirs(tmp_path):
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "stray_file.txt").write_text("not a tool")
    _make_tool(tools_dir, "real_tool")
    result = list_installed(tools_dir)
    assert len(result) == 1
    assert result[0]["name"] == "real_tool"


# ------------------------------------------------------------------ description extraction

def test_read_manifest_description(tmp_path):
    f = tmp_path / "tool.py"
    f.write_text('MANIFEST = ToolManifest(name="t", description="Hello world", triggers=[])\n')
    assert _read_manifest_description(f) == "Hello world"


def test_read_manifest_description_missing(tmp_path):
    f = tmp_path / "tool.py"
    f.write_text("# no manifest here\n")
    assert _read_manifest_description(f) == ""


# ------------------------------------------------------------------ URL (mocked git)

def test_install_url_no_git(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda cmd: None)
    with pytest.raises(InstallError, match="git is required"):
        install("https://example.com/repo.git", tmp_path / "tools")


def test_install_url_git_clone_fails(tmp_path, monkeypatch):
    import subprocess
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/git")
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: (_ for _ in ()).throw(
            subprocess.CalledProcessError(1, "git", stderr=b"fatal: not found")
        ),
    )
    with pytest.raises(InstallError, match="git clone failed"):
        install("https://example.com/repo.git", tmp_path / "tools")
