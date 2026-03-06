"""Tests for named session store — persistence and thread safety."""

from __future__ import annotations

import threading

from macroa.kernel.sessions import SessionStore
from macroa.stdlib.schema import ContextEntry


def _store(tmp_path) -> SessionStore:
    return SessionStore(db_path=tmp_path / "sessions.db")


def _entry(role: str = "user", content: str = "hello") -> ContextEntry:
    return ContextEntry(turn_id="t1", role=role, content=content)


# ------------------------------------------------------------------ basic CRUD

def test_get_or_create_new(tmp_path):
    store = _store(tmp_path)
    meta = store.get_or_create("work")
    assert meta.name == "work"
    assert len(meta.session_id) == 36  # UUID format


def test_get_or_create_idempotent(tmp_path):
    store = _store(tmp_path)
    m1 = store.get_or_create("work")
    m2 = store.get_or_create("work")
    assert m1.session_id == m2.session_id


def test_list_sessions(tmp_path):
    store = _store(tmp_path)
    store.get_or_create("alpha")
    store.get_or_create("beta")
    names = [s.name for s in store.list_sessions()]
    assert "alpha" in names
    assert "beta" in names


def test_delete_session(tmp_path):
    store = _store(tmp_path)
    store.get_or_create("temp")
    deleted = store.delete("temp")
    assert deleted is True
    names = [s.name for s in store.list_sessions()]
    assert "temp" not in names


def test_delete_nonexistent(tmp_path):
    store = _store(tmp_path)
    result = store.delete("ghost")
    assert result is False


def test_get_by_id(tmp_path):
    store = _store(tmp_path)
    meta = store.get_or_create("hello")
    fetched = store.get_by_id(meta.session_id)
    assert fetched is not None
    assert fetched.name == "hello"


def test_get_by_id_missing(tmp_path):
    store = _store(tmp_path)
    assert store.get_by_id("00000000-0000-0000-0000-000000000000") is None


# ------------------------------------------------------------------ context persistence

def test_save_and_load_context(tmp_path):
    store = _store(tmp_path)
    meta = store.get_or_create("ctx-test")
    entries = [
        _entry("user", "what is 2+2"),
        _entry("assistant", "4"),
    ]
    store.save_context(meta.session_id, entries)
    loaded = store.load_context(meta.session_id)
    assert len(loaded) == 2
    assert loaded[0].content == "what is 2+2"
    assert loaded[1].role == "assistant"


def test_context_overwrite(tmp_path):
    store = _store(tmp_path)
    meta = store.get_or_create("overwrite")
    store.save_context(meta.session_id, [_entry(content="old")])
    store.save_context(meta.session_id, [_entry(content="new")])
    loaded = store.load_context(meta.session_id)
    assert len(loaded) == 1
    assert loaded[0].content == "new"


def test_context_pinned_roundtrip(tmp_path):
    store = _store(tmp_path)
    meta = store.get_or_create("pinned")
    e = ContextEntry(turn_id="t1", role="system", content="always here", pinned=True)
    store.save_context(meta.session_id, [e])
    loaded = store.load_context(meta.session_id)
    assert loaded[0].pinned is True


def test_turn_count_updated(tmp_path):
    store = _store(tmp_path)
    meta = store.get_or_create("turns")
    entries = [_entry("user"), _entry("assistant"), _entry("user"), _entry("assistant")]
    store.save_context(meta.session_id, entries)
    updated = store.get_by_id(meta.session_id)
    assert updated.turn_count == 2  # 2 user entries


def test_delete_cascades_context(tmp_path):
    store = _store(tmp_path)
    meta = store.get_or_create("cascade")
    store.save_context(meta.session_id, [_entry()])
    store.delete("cascade")
    loaded = store.load_context(meta.session_id)
    assert loaded == []


# ------------------------------------------------------------------ thread safety

def test_concurrent_session_creation(tmp_path):
    store = _store(tmp_path)
    results = []

    def create():
        meta = store.get_or_create("shared-name")
        results.append(meta.session_id)

    threads = [threading.Thread(target=create) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # All threads must get the same session_id
    assert len(set(results)) == 1


def test_concurrent_save_load(tmp_path):
    store = _store(tmp_path)
    meta = store.get_or_create("concurrent")
    errors = []

    def write_and_read():
        try:
            store.save_context(meta.session_id, [_entry(content="data")])
            store.load_context(meta.session_id)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=write_and_read) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
