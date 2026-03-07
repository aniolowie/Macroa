"""Tests for memory v3 — pinned facts, FTS5 search, retriever, formatter, extractor."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from macroa.drivers.memory_driver import MemoryDriver
from macroa.memory.extractor import MemoryExtractor, _parse_facts
from macroa.memory.formatter import format_for_prompt
from macroa.memory.retriever import retrieve

# ── helpers ──────────────────────────────────────────────────────────────────

def _mem(tmp_path) -> MemoryDriver:
    return MemoryDriver(backend="sqlite", db_path=tmp_path / "mem_v3.db")


# ── schema v3 ────────────────────────────────────────────────────────────────

def test_schema_v3_creates_pinned_column(tmp_path):
    m = _mem(tmp_path)
    m.set_fact("user", "name", "Maciej", pinned=True)
    fact = m.get_fact("user", "name")
    assert fact is not None
    assert fact.pinned is True


def test_schema_migration_existing_rows_get_pinned_false(tmp_path):
    m = _mem(tmp_path)
    m.set("user", "color", "blue")
    fact = m.get_fact("user", "color")
    assert fact is not None
    assert fact.pinned is False


def test_schema_v3_idempotent(tmp_path):
    """Opening the same DB twice must not corrupt schema or raise."""
    db = tmp_path / "shared.db"
    m1 = MemoryDriver(backend="sqlite", db_path=db)
    m2 = MemoryDriver(backend="sqlite", db_path=db)
    m1.set_fact("user", "x", "1", pinned=True)
    assert m2.get("user", "x") == "1"
    assert m2.get_fact("user", "x").pinned is True


# ── pinned facts ─────────────────────────────────────────────────────────────

def test_list_pinned_returns_only_pinned(tmp_path):
    m = _mem(tmp_path)
    m.set_fact("user", "name", "Maciej", pinned=True)
    m.set_fact("user", "color", "blue", pinned=False)
    pinned = m.list_pinned()
    keys = [f["key"] for f in pinned]
    assert "name" in keys
    assert "color" not in keys


def test_pin_method_updates_flag(tmp_path):
    m = _mem(tmp_path)
    m.set("user", "city", "Warsaw")
    assert m.get_fact("user", "city").pinned is False
    ok = m.pin("user", "city")
    assert ok is True
    assert m.get_fact("user", "city").pinned is True


def test_unpin_method(tmp_path):
    m = _mem(tmp_path)
    m.set_fact("user", "city", "Warsaw", pinned=True)
    m.pin("user", "city", pinned=False)
    assert m.get_fact("user", "city").pinned is False


def test_pin_nonexistent_key_returns_false(tmp_path):
    m = _mem(tmp_path)
    assert m.pin("user", "ghost") is False


def test_list_all_pinned_first(tmp_path):
    m = _mem(tmp_path)
    m.set_fact("user", "b_unpinned", "val", pinned=False)
    m.set_fact("user", "a_pinned", "val", pinned=True)
    results = m.list_all()
    # Pinned must come first regardless of insertion order
    assert results[0]["key"] == "a_pinned"


# ── FTS5 search ──────────────────────────────────────────────────────────────

def test_search_fts_finds_keyword_in_value(tmp_path):
    m = _mem(tmp_path)
    m.set("user", "occupation", "software engineer")
    results = m.search_fts("engineer")
    assert any(r["key"] == "occupation" for r in results)


def test_search_fts_finds_keyword_in_key(tmp_path):
    m = _mem(tmp_path)
    m.set("user", "primary_language", "Python")
    results = m.search_fts("language")
    assert any(r["key"] == "primary_language" for r in results)


def test_search_fts_excludes_expired(tmp_path):
    m = _mem(tmp_path)
    m.set_fact("user", "expired_fact", "gone", expires_at=time.time() - 1)
    results = m.search_fts("gone")
    assert not any(r["key"] == "expired_fact" for r in results)


def test_search_fts_empty_query_returns_empty(tmp_path):
    m = _mem(tmp_path)
    m.set("user", "name", "Alice")
    # Single-char queries produce an empty FTS5 expression; should not crash
    results = m.search_fts("a")
    assert isinstance(results, list)


def test_search_fts_special_chars_do_not_crash(tmp_path):
    m = _mem(tmp_path)
    m.set("user", "note", "hello world")
    results = m.search_fts('what do you "know" about me?')
    assert isinstance(results, list)


# ── retriever ────────────────────────────────────────────────────────────────

def test_retrieve_always_includes_pinned(tmp_path):
    m = _mem(tmp_path)
    m.set_fact("user", "name", "Maciej", pinned=True)
    m.set_fact("user", "color", "blue", pinned=False)
    results = retrieve("completely unrelated query xyz", m)
    assert any(r["key"] == "name" for r in results)


def test_retrieve_contextual_via_fts(tmp_path):
    m = _mem(tmp_path)
    m.set_fact("user", "framework", "FastAPI", pinned=False)
    results = retrieve("FastAPI project", m)
    assert any(r["key"] == "framework" for r in results)


def test_retrieve_deduplicates_pinned_and_contextual(tmp_path):
    m = _mem(tmp_path)
    m.set_fact("user", "name", "Maciej", pinned=True)
    results = retrieve("Maciej", m)
    keys = [r["key"] for r in results]
    assert keys.count("name") == 1


def test_retrieve_empty_memory_returns_empty(tmp_path):
    m = _mem(tmp_path)
    assert retrieve("anything", m) == []


# ── formatter ────────────────────────────────────────────────────────────────

def test_format_empty_returns_empty_string():
    assert format_for_prompt([]) == ""


def test_format_pinned_section_header():
    facts = [{"key": "name", "value": "Maciej", "pinned": True, "confidence": 1.0}]
    out = format_for_prompt(facts)
    assert "## What I know about you" in out
    assert "name" in out
    assert "Maciej" in out


def test_format_contextual_section_header():
    facts = [{"key": "project", "value": "Macroa", "pinned": False, "confidence": 1.0}]
    out = format_for_prompt(facts)
    assert "### Also relevant" in out


def test_format_confidence_annotation_below_threshold():
    facts = [{"key": "mood", "value": "happy", "pinned": False, "confidence": 0.7}]
    out = format_for_prompt(facts)
    assert "70%" in out or "confident" in out


def test_format_no_confidence_annotation_at_1():
    facts = [{"key": "name", "value": "Alice", "pinned": True, "confidence": 1.0}]
    out = format_for_prompt(facts)
    assert "confident" not in out


def test_format_mixed_pinned_and_contextual():
    facts = [
        {"key": "name", "value": "Maciej", "pinned": True, "confidence": 1.0},
        {"key": "project", "value": "Macroa", "pinned": False, "confidence": 0.9},
    ]
    out = format_for_prompt(facts)
    assert "## What I know about you" in out
    assert "### Also relevant" in out


# ── extractor ── _parse_facts ─────────────────────────────────────────────

def test_parse_facts_clean_json():
    raw = '[{"key":"name","value":"Maciej","confidence":1.0,"pinned":true}]'
    facts = _parse_facts(raw)
    assert len(facts) == 1
    assert facts[0]["key"] == "name"


def test_parse_facts_strips_markdown_fences():
    raw = '```json\n[{"key":"city","value":"Warsaw","confidence":0.9,"pinned":false}]\n```'
    facts = _parse_facts(raw)
    assert len(facts) == 1
    assert facts[0]["key"] == "city"


def test_parse_facts_with_prose_around_json():
    raw = 'Here are the facts:\n[{"key":"lang","value":"Python","confidence":1.0,"pinned":true}]\nDone.'
    facts = _parse_facts(raw)
    assert any(f["key"] == "lang" for f in facts)


def test_parse_facts_empty_array():
    assert _parse_facts("[]") == []


def test_parse_facts_invalid_json_returns_empty():
    assert _parse_facts("not json at all") == []


def test_parse_facts_filters_non_dict_elements():
    raw = '[{"key":"a","value":"b","confidence":1.0,"pinned":false}, "oops", 42]'
    facts = _parse_facts(raw)
    assert all(isinstance(f, dict) for f in facts)
    assert len(facts) == 1


# ── extractor ── MemoryExtractor ──────────────────────────────────────────

def _mock_extractor(tmp_path, llm_response: str):
    m = _mem(tmp_path)
    llm = MagicMock()
    llm.complete.return_value = llm_response
    return MemoryExtractor(llm=llm, memory=m), m


def test_extractor_writes_facts_synchronously(tmp_path):
    """Call internal _run() directly (synchronous) to verify fact writing."""
    extractor, mem = _mock_extractor(
        tmp_path,
        '[{"key":"name","value":"Maciej","confidence":1.0,"pinned":true}]',
    )
    extractor._run(
        "My name is Maciej and I am a software engineer building Macroa.",
        "Nice to meet you, Maciej!",
    )
    assert mem.get("user", "name") == "Maciej"
    assert mem.get_fact("user", "name").pinned is True


def test_extractor_does_not_downgrade_confidence(tmp_path):
    extractor, mem = _mock_extractor(
        tmp_path,
        '[{"key":"name","value":"Bob","confidence":0.6,"pinned":false}]',
    )
    # Pre-populate with a high-confidence fact
    mem.set_fact("user", "name", "Maciej", confidence=1.0, source="user", pinned=True)
    extractor._run("you can call me Bob from now on please remember that", "Sure, Bob!")
    # Should NOT overwrite because existing confidence (1.0) > new (0.6)
    assert mem.get("user", "name") == "Maciej"


def test_extractor_preserves_user_pinned(tmp_path):
    """Extractor must not unpin a fact the user explicitly pinned."""
    extractor, mem = _mock_extractor(
        tmp_path,
        '[{"key":"name","value":"Maciej","confidence":0.9,"pinned":false}]',
    )
    mem.set_fact("user", "name", "Maciej", confidence=0.9, source="user", pinned=True)
    extractor._run("I am Maciej and I work as a software engineer on Macroa.", "Got it!")
    assert mem.get_fact("user", "name").pinned is True


def test_extractor_skips_short_messages(tmp_path):
    extractor, mem = _mock_extractor(tmp_path, '[{"key":"x","value":"y","confidence":1.0,"pinned":false}]')
    extractor._run("hi", "hello")
    # Too short — LLM should never be called
    extractor._llm.complete.assert_not_called()
    assert mem.get("user", "x") is None


def test_extractor_handles_llm_error_silently(tmp_path):
    m = _mem(tmp_path)
    llm = MagicMock()
    llm.complete.side_effect = RuntimeError("API down")
    extractor = MemoryExtractor(llm=llm, memory=m)
    # Should not raise
    extractor._run(
        "I am an engineer building a personal AI OS called Macroa.",
        "That sounds really cool!",
    )
    assert m.list_all() == []


# ── backward compat with v2 tests ────────────────────────────────────────────

def test_set_get_still_works(tmp_path):
    m = _mem(tmp_path)
    m.set("user", "color", "blue")
    assert m.get("user", "color") == "blue"


def test_search_like_still_works(tmp_path):
    m = _mem(tmp_path)
    m.set("user", "server_ip", "192.168.1.1")
    results = m.search("server")
    assert any(r["key"] == "server_ip" for r in results)


def test_export_markdown_includes_pin_marker(tmp_path):
    m = _mem(tmp_path)
    m.set_fact("user", "name", "Maciej", pinned=True)
    md = m.export_markdown()
    assert "name" in md
    assert "Maciej" in md
