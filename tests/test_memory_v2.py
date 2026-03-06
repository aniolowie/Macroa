"""Tests for memory driver v2 — semantic facts + episodic memory."""

import time

from macroa.drivers.memory_driver import Episode, Fact, MemoryDriver


def _mem(tmp_path) -> MemoryDriver:
    return MemoryDriver(backend="sqlite", db_path=tmp_path / "mem.db")


# ------------------------------------------------------------------ backward compat

def test_set_get_compat(tmp_path):
    m = _mem(tmp_path)
    m.set("user", "color", "blue")
    assert m.get("user", "color") == "blue"


def test_delete_compat(tmp_path):
    m = _mem(tmp_path)
    m.set("user", "x", "y")
    assert m.delete("user", "x") is True
    assert m.get("user", "x") is None


def test_search_compat(tmp_path):
    m = _mem(tmp_path)
    m.set("user", "server_ip", "192.168.1.1")
    results = m.search("server")
    assert any(r["key"] == "server_ip" for r in results)


def test_list_all_compat(tmp_path):
    m = _mem(tmp_path)
    m.set("user", "a", "1")
    m.set("user", "b", "2")
    assert len(m.list_all()) == 2


# ------------------------------------------------------------------ set_fact with metadata

def test_set_fact_confidence(tmp_path):
    m = _mem(tmp_path)
    m.set_fact("user", "mood", "happy", confidence=0.6, source="inferred")
    fact = m.get_fact("user", "mood")
    assert fact is not None
    assert fact.confidence == 0.6
    assert fact.source == "inferred"


def test_get_fact_full_object(tmp_path):
    m = _mem(tmp_path)
    m.set("user", "name", "Alice")
    fact = m.get_fact("user", "name")
    assert isinstance(fact, Fact)
    assert fact.key == "name"
    assert fact.value == "Alice"
    assert fact.namespace == "user"


def test_expires_at_respected(tmp_path):
    m = _mem(tmp_path)
    past = time.time() - 1  # already expired
    m.set_fact("user", "temp", "val", expires_at=past)
    assert m.get("user", "temp") is None
    assert m.get_fact("user", "temp") is None
    results = m.search("val")
    assert not any(r["key"] == "temp" for r in results)


def test_future_expiry_still_readable(tmp_path):
    m = _mem(tmp_path)
    future = time.time() + 3600
    m.set_fact("user", "session_token", "abc123", expires_at=future)
    assert m.get("user", "session_token") == "abc123"


def test_purge_expired(tmp_path):
    m = _mem(tmp_path)
    m.set_fact("user", "old", "gone", expires_at=time.time() - 1)
    m.set("user", "keep", "here")
    removed = m.purge_expired()
    assert removed == 1
    assert m.get("user", "keep") == "here"


def test_search_excludes_expired(tmp_path):
    m = _mem(tmp_path)
    m.set_fact("user", "expired_key", "expired_val", expires_at=time.time() - 1)
    m.set("user", "live_key", "live_val")
    results = m.search("val")
    keys = [r["key"] for r in results]
    assert "live_key" in keys
    assert "expired_key" not in keys


def test_upsert_overwrites(tmp_path):
    m = _mem(tmp_path)
    m.set("user", "color", "blue")
    m.set("user", "color", "red")
    assert m.get("user", "color") == "red"


# ------------------------------------------------------------------ episodic memory

def test_add_episode(tmp_path):
    m = _mem(tmp_path)
    ep_id = m.add_episode("sess-1", "User asked about the weather.", tags=["weather"], turn_count=3)
    assert ep_id > 0


def test_get_episodes(tmp_path):
    m = _mem(tmp_path)
    m.add_episode("sess-1", "Summary one", tags=["work"])
    m.add_episode("sess-1", "Summary two", tags=["personal"])
    episodes = m.get_episodes("sess-1")
    assert len(episodes) == 2
    assert all(isinstance(e, Episode) for e in episodes)


def test_get_episodes_all_sessions(tmp_path):
    m = _mem(tmp_path)
    m.add_episode("sess-a", "A")
    m.add_episode("sess-b", "B")
    all_eps = m.get_episodes()
    assert len(all_eps) == 2


def test_search_episodes(tmp_path):
    m = _mem(tmp_path)
    m.add_episode("s1", "User discussed machine learning topics", tags=["ml"])
    m.add_episode("s2", "User asked about recipes")
    results = m.search_episodes("machine learning")
    assert len(results) == 1
    assert results[0].session_id == "s1"


def test_episode_tags_roundtrip(tmp_path):
    m = _mem(tmp_path)
    m.add_episode("s1", "summary", tags=["a", "b", "c"])
    ep = m.get_episodes("s1")[0]
    assert ep.tags == ["a", "b", "c"]


# ------------------------------------------------------------------ export

def test_export_markdown(tmp_path):
    m = _mem(tmp_path)
    m.set("user", "name", "Alice")
    m.add_episode("s1", "Alice asked questions.", tags=["intro"])
    md = m.export_markdown()
    assert "name" in md
    assert "Alice" in md
    assert "Alice asked questions" in md
    assert "# Macroa Memory Snapshot" in md


# ------------------------------------------------------------------ schema migration guard

def test_two_instances_same_db(tmp_path):
    """Opening the same DB twice should not corrupt schema."""
    db = tmp_path / "shared.db"
    m1 = MemoryDriver(backend="sqlite", db_path=db)
    m2 = MemoryDriver(backend="sqlite", db_path=db)
    m1.set("user", "x", "1")
    assert m2.get("user", "x") == "1"
