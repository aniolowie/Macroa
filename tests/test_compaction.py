"""Tests for context compaction: ContextManager.on_evict hook + ContextCompactor."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from macroa.stdlib.schema import ContextEntry, SkillResult, ModelTier


# ── ContextManager.on_evict hook ──────────────────────────────────────────────


class TestContextManagerEvictHook:
    def test_on_evict_called_when_entry_dropped(self):
        from macroa.kernel.context import ContextManager

        mgr = ContextManager(window_size=1)  # maxlen=2 (1 turn × 2 entries)
        evicted: list[ContextEntry] = []
        mgr.on_evict = evicted.append

        # Fill the buffer (2 entries)
        mgr.add_user(turn_id="t1", content="hello world this is a message")
        mgr.add_assistant(SkillResult(output="hi there", success=True, turn_id="t1", model_tier=ModelTier.NANO))
        assert len(evicted) == 0  # buffer not full yet until next add

        # This add forces an eviction
        mgr.add_user(turn_id="t2", content="second message here")
        assert len(evicted) == 1
        assert evicted[0].content == "hello world this is a message"

    def test_on_evict_not_called_when_buffer_not_full(self):
        from macroa.kernel.context import ContextManager

        mgr = ContextManager(window_size=10)
        evicted: list = []
        mgr.on_evict = evicted.append

        mgr.add_user(turn_id="t1", content="hello")
        assert len(evicted) == 0

    def test_on_evict_exception_does_not_block(self):
        from macroa.kernel.context import ContextManager

        mgr = ContextManager(window_size=1)

        def bad_evict(entry):
            raise RuntimeError("evict handler crashed")

        mgr.on_evict = bad_evict

        mgr.add_user(turn_id="t1", content="a")
        mgr.add_assistant(SkillResult(output="b", success=True, turn_id="t1", model_tier=ModelTier.NANO))
        # Should not raise
        mgr.add_user(turn_id="t2", content="c")

    def test_evict_oldest_unpinned_returns_entry(self):
        from macroa.kernel.context import ContextManager

        mgr = ContextManager(window_size=1)
        mgr.add_user(turn_id="t1", content="first")
        mgr.add_assistant(SkillResult(output="ok", success=True, turn_id="t1", model_tier=ModelTier.NANO))

        evicted = mgr._evict_oldest_unpinned()
        assert evicted is not None
        assert evicted.content == "first"

    def test_evict_returns_none_when_all_pinned(self):
        from macroa.kernel.context import ContextManager

        mgr = ContextManager(window_size=1)
        mgr.add_system(turn_id="t0", content="sys", pinned=True)
        mgr.add_assistant(
            SkillResult(output="pinned reply", success=True, pin_to_context=True, turn_id="t0", model_tier=ModelTier.NANO)
        )

        evicted = mgr._evict_oldest_unpinned()
        assert evicted is None


# ── ContextCompactor ──────────────────────────────────────────────────────────


class TestContextCompactor:
    def _make_compactor(self, summary_response: str = "User discussed task planning."):
        from macroa.memory.compactor import ContextCompactor

        llm = MagicMock()
        llm.complete.return_value = summary_response
        memory = MagicMock()
        memory.add_episode.return_value = 1
        return ContextCompactor(llm=llm, memory=memory), llm, memory

    def _make_entry(self, content: str, role: str = "user", turn_id: str = "t1") -> ContextEntry:
        return ContextEntry(turn_id=turn_id, role=role, content=content)

    def test_short_entry_skipped(self):
        compactor, llm, memory = self._make_compactor()
        entry = self._make_entry("ok")  # < _MIN_CHARS
        compactor._compact(entry)  # call synchronously
        llm.complete.assert_not_called()
        memory.add_episode.assert_not_called()

    def test_long_entry_compacted(self):
        compactor, llm, memory = self._make_compactor("User wants to build a new feature.")
        entry = self._make_entry("I want to build a new feature that handles async task queues " * 3)
        compactor._compact(entry)
        llm.complete.assert_called_once()
        memory.add_episode.assert_called_once()
        call_kwargs = memory.add_episode.call_args[1]
        assert call_kwargs["summary"] == "User wants to build a new feature."
        assert "compacted_context" in call_kwargs["tags"]
        assert "user" in call_kwargs["tags"]

    def test_empty_llm_response_skipped(self):
        compactor, llm, memory = self._make_compactor("")
        entry = self._make_entry("A reasonably long message that exceeds the minimum length threshold. " * 2)
        compactor._compact(entry)
        llm.complete.assert_called_once()
        memory.add_episode.assert_not_called()

    def test_very_short_summary_skipped(self):
        compactor, llm, memory = self._make_compactor("ok")  # < 10 chars
        entry = self._make_entry("A reasonably long message that exceeds the minimum length threshold. " * 2)
        compactor._compact(entry)
        memory.add_episode.assert_not_called()

    def test_llm_error_does_not_raise(self):
        from macroa.memory.compactor import ContextCompactor

        llm = MagicMock()
        llm.complete.side_effect = RuntimeError("LLM down")
        memory = MagicMock()
        compactor = ContextCompactor(llm=llm, memory=memory)
        entry = self._make_entry("A long enough message that should be compacted by the system.")
        compactor._compact(entry)  # must not raise

    def test_handle_eviction_spawns_thread_for_long_entry(self):
        """handle_eviction should fire a daemon thread for long entries."""
        from macroa.memory.compactor import ContextCompactor

        llm = MagicMock()
        llm.complete.return_value = "A summary of the conversation turn."
        memory = MagicMock()
        compactor = ContextCompactor(llm=llm, memory=memory)

        entry = self._make_entry("This is a long enough entry that it should be compacted. " * 3)

        # Patch threading.Thread so we can inspect it without actually running
        with patch("macroa.memory.compactor.threading.Thread") as MockThread:
            mock_thread = MagicMock()
            MockThread.return_value = mock_thread
            compactor.handle_eviction(entry)

        MockThread.assert_called_once()
        mock_thread.start.assert_called_once()
        # Verify the thread is marked as daemon
        assert MockThread.call_args[1]["daemon"] is True

    def test_handle_eviction_skips_short_entry(self):
        """Short entries should not spawn a thread."""
        from macroa.memory.compactor import ContextCompactor

        compactor = ContextCompactor(llm=MagicMock(), memory=MagicMock())
        entry = self._make_entry("hi")  # very short

        with patch("macroa.memory.compactor.threading.Thread") as MockThread:
            compactor.handle_eviction(entry)

        MockThread.assert_not_called()

    def test_assistant_role_tag_stored(self):
        compactor, llm, memory = self._make_compactor("Agent explained the memory system.")
        entry = self._make_entry(
            "The memory system uses FTS5 for full-text search and stores facts in SQLite with schema migrations.",
            role="assistant",
        )
        compactor._compact(entry)
        call_kwargs = memory.add_episode.call_args[1]
        assert "assistant" in call_kwargs["tags"]


# ── episodes injected into chat_skill system prompt ───────────────────────────


class TestEpisodesInSystemPrompt:
    def test_episodes_appear_in_prompt(self):
        from macroa.skills.chat_skill import _build_system
        from macroa.stdlib.schema import Context, DriverBundle, Intent, ModelTier

        class FakeEpisode:
            summary = "User was asking about memory architecture."

        memory = MagicMock()
        memory.get.return_value = None
        memory.get_episodes.return_value = [FakeEpisode()]
        drivers = DriverBundle(
            llm=MagicMock(), shell=MagicMock(), fs=MagicMock(),
            memory=memory, network=MagicMock(),
        )
        intent = Intent(
            raw="remind me what we discussed",
            skill_name="chat_skill",
            parameters={},
            model_tier=ModelTier.SONNET,
            routing_confidence=1.0,
            turn_id="t1",
        )

        with patch("macroa.skills.chat_skill.build_system_prompt", return_value="Base."), \
             patch("macroa.skills.chat_skill.retrieve", return_value=[]):
            result = _build_system(intent, drivers, session_id="s1")

        assert "Earlier in this conversation" in result
        assert "memory architecture" in result

    def test_no_episodes_no_section(self):
        from macroa.skills.chat_skill import _build_system
        from macroa.stdlib.schema import Context, DriverBundle, Intent, ModelTier

        memory = MagicMock()
        memory.get.return_value = None
        memory.get_episodes.return_value = []
        drivers = DriverBundle(
            llm=MagicMock(), shell=MagicMock(), fs=MagicMock(),
            memory=memory, network=MagicMock(),
        )
        intent = Intent(
            raw="hello",
            skill_name="chat_skill",
            parameters={},
            model_tier=ModelTier.SONNET,
            routing_confidence=1.0,
            turn_id="t1",
        )

        with patch("macroa.skills.chat_skill.build_system_prompt", return_value="Base."), \
             patch("macroa.skills.chat_skill.retrieve", return_value=[]):
            result = _build_system(intent, drivers, session_id="s1")

        assert "Earlier in this conversation" not in result
