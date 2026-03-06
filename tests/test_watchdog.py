"""Tests for the Watchdog interrupt system."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from macroa.kernel.watchdog import (
    FileChangeObserver,
    MemoryChangeObserver,
    ObserverMeta,
    WatchdogManager,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _meta(observer_type: str, config: dict, action: str = "{event} {path}", **kwargs) -> ObserverMeta:
    return ObserverMeta(
        observer_id="test-id",
        observer_type=observer_type,
        config=config,
        action=action,
        session_id="test-session",
        **kwargs,
    )


def _mem_driver(store: dict | None = None):
    store = store if store is not None else {}
    driver = MagicMock()
    driver.get.side_effect = lambda ns, key: store.get((ns, key))
    driver.set.side_effect = lambda ns, key, val: store.update({(ns, key): val})
    driver.delete.side_effect = lambda ns, key: bool(store.pop((ns, key), None))
    return driver


# ── FileChangeObserver ────────────────────────────────────────────────────────

class TestFileChangeObserver:
    def test_no_trigger_on_setup_when_file_exists(self, tmp_path):
        f = tmp_path / "f.txt"
        f.write_text("hello")
        obs = FileChangeObserver(_meta("FileChangeObserver", {"path": str(f)}))
        obs.setup()
        assert obs.check() is None  # no change yet

    def test_triggers_on_modification(self, tmp_path):
        f = tmp_path / "f.txt"
        f.write_text("v1")
        obs = FileChangeObserver(_meta("FileChangeObserver", {"path": str(f)},
                                       action="file {path} was {event}"))
        obs.setup()
        # Simulate mtime change
        f.write_text("v2")
        # Force mtime difference (some filesystems have 1s resolution)
        obs._last_mtime -= 1
        result = obs.check()
        assert result is not None
        assert "modified" in result

    def test_triggers_on_creation(self, tmp_path):
        f = tmp_path / "new.txt"
        obs = FileChangeObserver(_meta("FileChangeObserver", {"path": str(f)},
                                       action="file {path} was {event}"))
        obs.setup()  # file doesn't exist yet
        assert obs.check() is None  # still doesn't exist
        f.write_text("appeared")
        result = obs.check()
        assert result is not None
        assert "created" in result

    def test_triggers_on_deletion(self, tmp_path):
        f = tmp_path / "gone.txt"
        f.write_text("here")
        obs = FileChangeObserver(_meta("FileChangeObserver", {"path": str(f)},
                                       action="file {path} was {event}"))
        obs.setup()
        f.unlink()
        result = obs.check()
        assert result is not None
        assert "deleted" in result

    def test_no_trigger_without_setup(self, tmp_path):
        f = tmp_path / "f.txt"
        f.write_text("x")
        obs = FileChangeObserver(_meta("FileChangeObserver", {"path": str(f)}))
        assert obs.check() is None  # not initialized yet

    def test_no_double_trigger_on_same_content(self, tmp_path):
        f = tmp_path / "f.txt"
        f.write_text("static")
        obs = FileChangeObserver(_meta("FileChangeObserver", {"path": str(f)}))
        obs.setup()
        assert obs.check() is None
        assert obs.check() is None  # second check — no change

    def test_action_template_interpolation(self, tmp_path):
        f = tmp_path / "flag.txt"
        obs = FileChangeObserver(_meta(
            "FileChangeObserver", {"path": str(f)},
            action="Flag captured at {path}: {content}",
        ))
        obs.setup()
        f.write_text("CTF{winning}")
        result = obs.check()
        assert "CTF{winning}" in result
        assert str(f) in result

    def test_from_meta_roundtrip(self, tmp_path):
        f = tmp_path / "x.txt"
        meta = _meta("FileChangeObserver", {"path": str(f)})
        obs = FileChangeObserver.from_meta(meta)
        assert isinstance(obs, FileChangeObserver)
        assert obs._path == f.resolve()


# ── MemoryChangeObserver ──────────────────────────────────────────────────────

class TestMemoryChangeObserver:
    def test_no_trigger_on_setup(self):
        store = {("user", "name"): "Alice"}
        driver = _mem_driver(store)
        obs = MemoryChangeObserver(
            _meta("MemoryChangeObserver", {"namespace": "user", "key": "name"}),
            driver,
        )
        obs.setup()
        assert obs.check() is None

    def test_triggers_on_value_change(self):
        store = {("user", "name"): "Alice"}
        driver = _mem_driver(store)
        obs = MemoryChangeObserver(
            _meta("MemoryChangeObserver", {"namespace": "user", "key": "name"},
                  action="{key} changed to {value}"),
            driver,
        )
        obs.setup()
        store[("user", "name")] = "Bob"
        result = obs.check()
        assert result is not None
        assert "Bob" in result
        assert "name" in result

    def test_triggers_on_new_key(self):
        store: dict = {}
        driver = _mem_driver(store)
        obs = MemoryChangeObserver(
            _meta("MemoryChangeObserver", {"namespace": "ctf", "key": "flag"},
                  action="Flag set: {value}"),
            driver,
        )
        obs.setup()  # key doesn't exist yet
        store[("ctf", "flag")] = "CTF{found_it}"
        result = obs.check()
        assert result is not None
        assert "CTF{found_it}" in result

    def test_triggers_on_deletion(self):
        store = {("user", "key"): "val"}
        driver = _mem_driver(store)
        obs = MemoryChangeObserver(
            _meta("MemoryChangeObserver", {"namespace": "user", "key": "key"},
                  action="key deleted: {value}"),
            driver,
        )
        obs.setup()
        del store[("user", "key")]
        result = obs.check()
        assert result is not None
        assert "deleted" in result

    def test_no_trigger_without_memory_driver(self):
        obs = MemoryChangeObserver(
            _meta("MemoryChangeObserver", {"namespace": "user", "key": "name"}),
            memory_driver=None,
        )
        obs.setup()
        assert obs.check() is None

    def test_no_double_trigger(self):
        store = {("user", "name"): "Alice"}
        driver = _mem_driver(store)
        obs = MemoryChangeObserver(
            _meta("MemoryChangeObserver", {"namespace": "user", "key": "name"}),
            driver,
        )
        obs.setup()
        store[("user", "name")] = "Bob"
        obs.check()  # first trigger
        result = obs.check()  # second check — same value
        assert result is None


# ── WatchdogManager ───────────────────────────────────────────────────────────

class TestWatchdogManager:
    def _manager(self, tmp_path, run_fn=None, memory=None):
        return WatchdogManager(
            db_path=tmp_path / "watchdog.db",
            run_fn=run_fn or MagicMock(),
            memory_driver=memory,
        )

    def test_add_persists_to_db(self, tmp_path, monkeypatch):
        # Prevent thread from starting
        monkeypatch.setattr("macroa.kernel.watchdog.WatchdogManager._launch", lambda *a: None)
        mgr = self._manager(tmp_path)
        meta = mgr.add(
            observer_type="FileChangeObserver",
            config={"path": "/tmp/test.txt"},
            action="changed: {path}",
            session_id="s1",
        )
        observers = mgr.list_observers()
        assert len(observers) == 1
        assert observers[0].observer_id == meta.observer_id
        assert observers[0].observer_type == "FileChangeObserver"

    def test_delete_removes_from_db(self, tmp_path, monkeypatch):
        monkeypatch.setattr("macroa.kernel.watchdog.WatchdogManager._launch", lambda *a: None)
        mgr = self._manager(tmp_path)
        meta = mgr.add("FileChangeObserver", {"path": "/tmp/x"}, "a", "s")
        mgr.delete(meta.observer_id)
        assert mgr.list_observers() == []

    def test_enable_disable(self, tmp_path, monkeypatch):
        monkeypatch.setattr("macroa.kernel.watchdog.WatchdogManager._launch", lambda *a: None)
        mgr = self._manager(tmp_path)
        meta = mgr.add("FileChangeObserver", {"path": "/tmp/x"}, "a", "s")
        mgr.enable(meta.observer_id, False)
        observers = mgr.list_observers()
        assert not observers[0].enabled

    def test_unknown_observer_type_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr("macroa.kernel.watchdog.WatchdogManager._launch", lambda *a: None)
        mgr = self._manager(tmp_path)
        with pytest.raises(ValueError, match="Unknown observer type"):
            mgr.add("BogusObserver", {}, "a", "s")

    def test_start_reloads_persisted_observers(self, tmp_path, monkeypatch):
        launched = []
        monkeypatch.setattr(
            "macroa.kernel.watchdog.WatchdogManager._launch",
            lambda self, obs: launched.append(obs.observer_id),
        )
        mgr = self._manager(tmp_path)
        meta = mgr.add("FileChangeObserver", {"path": "/tmp/x"}, "a", "s")
        launched.clear()

        # New manager instance — simulates restart
        mgr2 = self._manager(tmp_path)
        mgr2.start()
        assert meta.observer_id in launched

    def test_trigger_fires_run_fn(self, tmp_path):
        """Integration: observer check() fires run_fn via real thread loop."""
        run_fn = MagicMock()
        f = tmp_path / "watch.txt"
        f.write_text("initial")

        mgr = WatchdogManager(
            db_path=tmp_path / "watchdog.db",
            run_fn=run_fn,
        )
        meta = mgr.add(
            observer_type="FileChangeObserver",
            config={"path": str(f)},
            action="changed: {path}",
            session_id="sess",
            poll_interval=1,
            once=True,
        )

        # Wait for setup to complete, then trigger change
        time.sleep(0.2)
        obs = mgr._observers.get(meta.observer_id)
        if obs:
            obs._last_mtime -= 1  # force mtime diff

        # Wait for the thread to call check() and fire
        deadline = time.time() + 5
        while time.time() < deadline:
            if run_fn.called:
                break
            time.sleep(0.1)

        mgr.stop()
        assert run_fn.called
        args = run_fn.call_args[0]
        assert "changed:" in args[0]
        assert args[1] == "sess"

    def test_once_removes_observer_after_trigger(self, tmp_path):
        run_fn = MagicMock()
        f = tmp_path / "once.txt"

        mgr = WatchdogManager(db_path=tmp_path / "watchdog.db", run_fn=run_fn)
        mgr.add(
            "FileChangeObserver",
            {"path": str(f)},
            "appeared: {path}",
            "sess",
            poll_interval=1,
            once=True,
        )

        time.sleep(0.2)
        f.write_text("hello")  # trigger "created" event

        deadline = time.time() + 5
        while time.time() < deadline:
            if run_fn.called:
                break
            time.sleep(0.1)

        time.sleep(0.3)  # let delete propagate
        mgr.stop()

        assert run_fn.call_count == 1
        assert mgr.list_observers() == []  # removed after trigger
