"""Tests for the VFS layer — layout, backends, mount resolution, and vfs_skill."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from macroa.vfs.base import VFSError
from macroa.vfs.local import LocalBackend
from macroa.vfs.memory import MemoryBackend
from macroa.vfs.vfs import VFS

# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp(tmp_path):
    return tmp_path


@pytest.fixture
def local(tmp_path):
    return LocalBackend(tmp_path, label="test-local")


@pytest.fixture
def mem_driver():
    store: dict[tuple[str, str], str] = {}

    driver = MagicMock()
    driver.get.side_effect = lambda ns, key: store.get((ns, key))
    driver.set.side_effect = lambda ns, key, val: store.update({(ns, key): val})
    driver.delete.side_effect = lambda ns, key: bool(store.pop((ns, key), None))
    driver.list_all.side_effect = lambda namespace=None: [
        {"namespace": ns, "key": k, "value": v}
        for (ns, k), v in store.items()
        if namespace is None or ns == namespace
    ]
    return driver


@pytest.fixture
def memory(mem_driver):
    return MemoryBackend(mem_driver)


@pytest.fixture
def vfs(tmp_path, mem_driver):
    v = VFS()
    v.mount("/mem",       MemoryBackend(mem_driver))
    v.mount("/workspace", LocalBackend(tmp_path / "workspace", "workspace"))
    v.mount("/identity",  LocalBackend(tmp_path / "identity",  "identity"))
    v.mount("/fs",        LocalBackend(tmp_path / "fs",        "fs"))
    (tmp_path / "workspace").mkdir()
    (tmp_path / "identity").mkdir()
    (tmp_path / "fs").mkdir()
    return v


# ── LocalBackend ──────────────────────────────────────────────────────────────

class TestLocalBackend:
    def test_write_and_read(self, local, tmp_path):
        local.write("/hello.txt", "world")
        assert (tmp_path / "hello.txt").read_text() == "world"
        assert local.read("/hello.txt") == "world"

    def test_write_creates_parents(self, local, tmp_path):
        local.write("/deep/nested/file.txt", "data")
        assert (tmp_path / "deep" / "nested" / "file.txt").exists()

    def test_read_missing_raises(self, local):
        with pytest.raises(FileNotFoundError):
            local.read("/no-such-file.txt")

    def test_list_dir(self, local, tmp_path):
        local.write("/a.txt", "a")
        local.write("/b.txt", "b")
        entries = local.list("/")
        assert "a.txt" in entries and "b.txt" in entries

    def test_list_missing_returns_empty(self, local):
        assert local.list("/no-such-dir") == []

    def test_exists(self, local):
        assert not local.exists("/ghost.txt")
        local.write("/ghost.txt", "boo")
        assert local.exists("/ghost.txt")

    def test_delete(self, local, tmp_path):
        local.write("/del.txt", "x")
        assert local.delete("/del.txt") is True
        assert not (tmp_path / "del.txt").exists()
        assert local.delete("/del.txt") is False

    def test_stat_file(self, local):
        local.write("/f.txt", "hello")
        node = local.stat("/f.txt")
        assert node is not None
        assert node.is_dir is False
        assert node.size == 5
        assert node.backend == "test-local"

    def test_stat_dir(self, local, tmp_path):
        (tmp_path / "subdir").mkdir()
        node = local.stat("/subdir")
        assert node is not None
        assert node.is_dir is True

    def test_stat_missing_returns_none(self, local):
        assert local.stat("/phantom") is None

    def test_name(self, local):
        assert local.name == "test-local"


# ── MemoryBackend ─────────────────────────────────────────────────────────────

class TestMemoryBackend:
    def test_write_and_read(self, memory, mem_driver):
        memory.write("/user/name", "Alice")
        assert memory.read("/user/name") == "Alice"

    def test_read_missing_raises(self, memory):
        with pytest.raises(FileNotFoundError):
            memory.read("/user/ghost")

    def test_read_bad_path_raises(self, memory):
        with pytest.raises(ValueError):
            memory.read("/user")  # no key

    def test_list_namespaces(self, memory):
        memory.write("/user/name", "Alice")
        memory.write("/project/goal", "Build OS")
        ns = memory.list("/")
        assert "user" in ns and "project" in ns

    def test_list_keys(self, memory):
        memory.write("/user/name", "Alice")
        memory.write("/user/age", "30")
        keys = memory.list("/user")
        assert "name" in keys and "age" in keys

    def test_exists(self, memory):
        assert not memory.exists("/user/x")
        memory.write("/user/x", "1")
        assert memory.exists("/user/x")

    def test_delete(self, memory):
        memory.write("/user/tmp", "gone")
        assert memory.delete("/user/tmp") is True
        assert not memory.exists("/user/tmp")
        assert memory.delete("/user/tmp") is False

    def test_stat_value(self, memory):
        memory.write("/user/key", "hello")
        node = memory.stat("/user/key")
        assert node is not None
        assert node.is_dir is False
        assert node.size == 5

    def test_stat_namespace_is_dir(self, memory):
        memory.write("/ns/k", "v")
        node = memory.stat("/ns")
        assert node is not None
        assert node.is_dir is True

    def test_stat_missing_returns_none(self, memory):
        assert memory.stat("/ns/ghost") is None


# ── VFS mount resolution ──────────────────────────────────────────────────────

class TestVFSMounts:
    def test_longest_prefix_wins(self, vfs):
        vfs.write("/workspace/scratch.txt", "data")
        assert vfs.read("/workspace/scratch.txt") == "data"

    def test_mem_path_routes_to_memory(self, vfs):
        vfs.write("/mem/user/name", "Bob")
        assert vfs.read("/mem/user/name") == "Bob"

    def test_identity_path_routes_to_local(self, vfs, tmp_path):
        vfs.write("/identity/IDENTITY.md", "# Agent")
        assert (tmp_path / "identity" / "IDENTITY.md").exists()

    def test_unmounted_path_raises_vfserror(self, vfs):
        with pytest.raises(VFSError):
            vfs.read("/unknown/path")

    def test_exists_unmounted_returns_false(self, vfs):
        assert vfs.exists("/totally/unknown") is False

    def test_stat_unmounted_returns_none(self, vfs):
        assert vfs.stat("/not/a/mount") is None

    def test_list(self, vfs):
        vfs.write("/workspace/a.txt", "a")
        vfs.write("/workspace/b.txt", "b")
        entries = vfs.list("/workspace")
        assert "a.txt" in entries and "b.txt" in entries

    def test_delete(self, vfs):
        vfs.write("/workspace/del.txt", "x")
        assert vfs.delete("/workspace/del.txt") is True
        assert not vfs.exists("/workspace/del.txt")

    def test_mounts_listing(self, vfs):
        mount_map = dict(vfs.mounts())
        assert "/mem" in mount_map
        assert "/workspace" in mount_map
        assert "/identity" in mount_map

    def test_path_without_leading_slash(self, vfs):
        vfs.write("/workspace/noslash.txt", "ok")
        # VFS should normalise path
        assert vfs.exists("/workspace/noslash.txt")


# ── Layout bootstrap ──────────────────────────────────────────────────────────

class TestLayoutBootstrap:
    def test_bootstrap_creates_dirs(self, tmp_path, monkeypatch):
        from macroa.vfs import layout as lay
        monkeypatch.setattr(lay, "MACROA_DIR", tmp_path)
        lay.bootstrap_layout()
        for subdir in lay.LAYOUT:
            assert (tmp_path / subdir).is_dir(), f"Missing: {subdir}"

    def test_bootstrap_idempotent(self, tmp_path, monkeypatch):
        from macroa.vfs import layout as lay
        monkeypatch.setattr(lay, "MACROA_DIR", tmp_path)
        lay.bootstrap_layout()
        lay.bootstrap_layout()  # should not raise

    def test_migrate_identity_files(self, tmp_path, monkeypatch):
        from macroa.vfs import layout as lay
        monkeypatch.setattr(lay, "MACROA_DIR", tmp_path)
        # Write legacy flat files
        (tmp_path / "IDENTITY.md").write_text("# Me")
        (tmp_path / "USER.md").write_text("# User")
        lay.bootstrap_layout()
        assert (tmp_path / "identity" / "IDENTITY.md").read_text() == "# Me"
        assert (tmp_path / "identity" / "USER.md").read_text() == "# User"

    def test_migrate_does_not_overwrite_existing(self, tmp_path, monkeypatch):
        from macroa.vfs import layout as lay
        monkeypatch.setattr(lay, "MACROA_DIR", tmp_path)
        (tmp_path / "identity").mkdir(parents=True)
        (tmp_path / "identity" / "IDENTITY.md").write_text("# New")
        (tmp_path / "IDENTITY.md").write_text("# Old")
        lay.bootstrap_layout()
        # Existing new file must NOT be overwritten
        assert (tmp_path / "identity" / "IDENTITY.md").read_text() == "# New"

    def test_migrate_db_files(self, tmp_path, monkeypatch):
        from macroa.vfs import layout as lay
        monkeypatch.setattr(lay, "MACROA_DIR", tmp_path)
        (tmp_path / "memory.db").write_bytes(b"sqlite")
        lay.bootstrap_layout()
        assert (tmp_path / "memory" / "memory.db").read_bytes() == b"sqlite"

    def test_layout_status(self, tmp_path, monkeypatch):
        from macroa.vfs import layout as lay
        monkeypatch.setattr(lay, "MACROA_DIR", tmp_path)
        lay.bootstrap_layout()
        status = lay.layout_status()
        assert all(status.values()), f"Missing dirs: {[k for k, v in status.items() if not v]}"


# ── vfs_skill ─────────────────────────────────────────────────────────────────

class TestVFSSkill:
    def _intent(self, action: str, path: str, content: str = ""):
        from macroa.stdlib.schema import Intent, ModelTier
        return Intent(
            raw=f"{action} {path}",
            skill_name="vfs_skill",
            parameters={"action": action, "path": path, "content": content},
            model_tier=ModelTier.NANO,
            routing_confidence=1.0,
        )

    def _drivers(self, vfs):
        d = MagicMock()
        d.vfs = vfs
        return d

    def test_write_and_read(self, vfs):
        from macroa.skills.vfs_skill import run
        from macroa.stdlib.schema import Context
        ctx = Context(entries=[], session_id="t")
        drivers = self._drivers(vfs)

        result = run(self._intent("write", "/workspace/f.txt", "hello"), ctx, drivers)
        assert result.success

        result = run(self._intent("read", "/workspace/f.txt"), ctx, drivers)
        assert result.success
        assert result.output == "hello"

    def test_list(self, vfs):
        from macroa.skills.vfs_skill import run
        from macroa.stdlib.schema import Context
        ctx = Context(entries=[], session_id="t")
        drivers = self._drivers(vfs)
        vfs.write("/workspace/x.txt", "x")
        result = run(self._intent("list", "/workspace"), ctx, drivers)
        assert result.success
        assert "x.txt" in result.output

    def test_exists_true(self, vfs):
        from macroa.skills.vfs_skill import run
        from macroa.stdlib.schema import Context
        ctx = Context(entries=[], session_id="t")
        drivers = self._drivers(vfs)
        vfs.write("/workspace/e.txt", "x")
        result = run(self._intent("exists", "/workspace/e.txt"), ctx, drivers)
        assert result.success
        assert "Exists" in result.output

    def test_delete(self, vfs):
        from macroa.skills.vfs_skill import run
        from macroa.stdlib.schema import Context
        ctx = Context(entries=[], session_id="t")
        drivers = self._drivers(vfs)
        vfs.write("/workspace/gone.txt", "x")
        result = run(self._intent("delete", "/workspace/gone.txt"), ctx, drivers)
        assert result.success
        assert "Deleted" in result.output

    def test_stat(self, vfs):
        from macroa.skills.vfs_skill import run
        from macroa.stdlib.schema import Context
        ctx = Context(entries=[], session_id="t")
        drivers = self._drivers(vfs)
        vfs.write("/workspace/s.txt", "hello")
        result = run(self._intent("stat", "/workspace/s.txt"), ctx, drivers)
        assert result.success
        assert "file" in result.output

    def test_no_vfs_returns_error(self):
        from macroa.skills.vfs_skill import run
        from macroa.stdlib.schema import Context, Intent, ModelTier
        ctx = Context(entries=[], session_id="t")
        d = MagicMock()
        d.vfs = None
        intent = Intent(
            raw="read /x",
            skill_name="vfs_skill",
            parameters={"action": "read", "path": "/x"},
            model_tier=ModelTier.NANO,
            routing_confidence=1.0,
        )
        result = run(intent, ctx, d)
        assert not result.success
        assert "not available" in result.error

    def test_missing_path_returns_error(self, vfs):
        from macroa.skills.vfs_skill import run
        from macroa.stdlib.schema import Context, Intent, ModelTier
        ctx = Context(entries=[], session_id="t")
        d = self._drivers(vfs)
        intent = Intent(
            raw="read",
            skill_name="vfs_skill",
            parameters={"action": "read", "path": ""},
            model_tier=ModelTier.NANO,
            routing_confidence=1.0,
        )
        result = run(intent, ctx, d)
        assert not result.success
