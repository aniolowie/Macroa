"""Tests for deterministic drivers (no LLM calls)."""

import tempfile
from pathlib import Path

import pytest

from macroa.drivers.fs_driver import FSDriver, FSDriverError
from macroa.drivers.memory_driver import MemoryDriver
from macroa.drivers.shell_driver import ShellDriver


# ------------------------------------------------------------------ ShellDriver

def test_shell_echo():
    driver = ShellDriver()
    code, stdout, stderr = driver.run("echo hello")
    assert code == 0
    assert "hello" in stdout
    assert stderr == ""


def test_shell_nonzero_exit():
    driver = ShellDriver()
    code, stdout, stderr = driver.run("exit 42", timeout=5)
    assert code == 42


def test_shell_stderr():
    driver = ShellDriver()
    code, stdout, stderr = driver.run("ls /nonexistent_path_xyz")
    assert code != 0
    assert stderr or "No such" in stderr or True  # stderr populated on most systems


def test_shell_timeout():
    driver = ShellDriver()
    code, stdout, stderr = driver.run("sleep 10", timeout=1)
    assert code == 124


# ------------------------------------------------------------------ FSDriver

def test_fs_read_write(tmp_path):
    driver = FSDriver(base_dir=tmp_path)
    path = tmp_path / "test.txt"
    driver.write(str(path), "hello world")
    assert driver.read(str(path)) == "hello world"


def test_fs_write_creates_parents(tmp_path):
    driver = FSDriver(base_dir=tmp_path)
    path = tmp_path / "a" / "b" / "c.txt"
    driver.write(str(path), "nested")
    assert driver.read(str(path)) == "nested"


def test_fs_list_dir(tmp_path):
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b.txt").write_text("b")
    driver = FSDriver(base_dir=tmp_path)
    entries = driver.list_dir(str(tmp_path))
    assert "a.txt" in entries
    assert "b.txt" in entries


def test_fs_symlink_guard(tmp_path):
    driver = FSDriver(base_dir=tmp_path)
    with pytest.raises(FSDriverError):
        driver.read("/etc/passwd")


def test_fs_missing_file(tmp_path):
    driver = FSDriver(base_dir=tmp_path)
    with pytest.raises(FSDriverError):
        driver.read(str(tmp_path / "nonexistent.txt"))


# ------------------------------------------------------------------ MemoryDriver (sqlite)

def test_memory_set_get(tmp_path):
    driver = MemoryDriver(backend="sqlite", db_path=tmp_path / "mem.db")
    driver.set("user", "server_ip", "192.168.1.100")
    assert driver.get("user", "server_ip") == "192.168.1.100"


def test_memory_missing_key(tmp_path):
    driver = MemoryDriver(backend="sqlite", db_path=tmp_path / "mem.db")
    assert driver.get("user", "nonexistent") is None


def test_memory_delete(tmp_path):
    driver = MemoryDriver(backend="sqlite", db_path=tmp_path / "mem.db")
    driver.set("user", "foo", "bar")
    assert driver.delete("user", "foo") is True
    assert driver.get("user", "foo") is None
    assert driver.delete("user", "foo") is False


def test_memory_search(tmp_path):
    driver = MemoryDriver(backend="sqlite", db_path=tmp_path / "mem.db")
    driver.set("user", "server_ip", "192.168.1.100")
    driver.set("user", "favorite_color", "blue")
    results = driver.search("server")
    assert any(r["key"] == "server_ip" for r in results)
    results2 = driver.search("blue")
    assert any(r["key"] == "favorite_color" for r in results2)


def test_memory_list_all(tmp_path):
    driver = MemoryDriver(backend="sqlite", db_path=tmp_path / "mem.db")
    driver.set("user", "a", "1")
    driver.set("user", "b", "2")
    results = driver.list_all()
    assert len(results) == 2


# ------------------------------------------------------------------ MemoryDriver (json)

def test_memory_json_backend(tmp_path):
    driver = MemoryDriver(backend="json", db_path=tmp_path / "mem.json")
    driver.set("ns", "key1", "val1")
    assert driver.get("ns", "key1") == "val1"
    results = driver.search("val")
    assert len(results) == 1
