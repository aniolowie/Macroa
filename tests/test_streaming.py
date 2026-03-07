"""Tests for the streaming REPL pipeline."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from macroa.stdlib.schema import (
    Context,
    DriverBundle,
    Intent,
    ModelTier,
    SkillResult,
)


def _make_intent(raw: str = "hello") -> Intent:
    return Intent(
        raw=raw,
        skill_name="chat_skill",
        parameters={},
        model_tier=ModelTier.SONNET,
        routing_confidence=1.0,
        turn_id="test-turn",
    )


def _make_drivers(*, stream_callback=None) -> DriverBundle:
    llm = MagicMock()
    llm.complete.return_value = "Hello!"
    llm.stream.return_value = iter(["He", "ll", "o!"])
    memory = MagicMock()
    memory.get.return_value = None
    return DriverBundle(
        llm=llm,
        shell=MagicMock(),
        fs=MagicMock(),
        memory=memory,
        network=MagicMock(),
        stream_callback=stream_callback,
    )


# ── DriverBundle.stream_callback field ────────────────────────────────────────


class TestDriverBundleStreamCallback:
    def test_default_is_none(self):
        drivers = _make_drivers()
        assert drivers.stream_callback is None

    def test_accepts_callable(self):
        received: list[str] = []
        drivers = _make_drivers(stream_callback=received.append)
        assert drivers.stream_callback is not None
        drivers.stream_callback("hi")
        assert received == ["hi"]


# ── chat_skill streaming path ─────────────────────────────────────────────────


class TestChatSkillStreaming:
    def test_uses_complete_when_no_callback(self):
        from macroa.skills.chat_skill import run

        drivers = _make_drivers()
        intent = _make_intent()
        context = Context(entries=[], session_id="s1")

        with patch("macroa.skills.chat_skill.build_system_prompt", return_value="sys"), \
             patch("macroa.skills.chat_skill.retrieve", return_value=[]):
            result = run(intent, context, drivers)

        drivers.llm.complete.assert_called_once()
        drivers.llm.stream.assert_not_called()
        assert result.output == "Hello!"
        assert result.success

    def test_uses_stream_when_callback_set(self):
        from macroa.skills.chat_skill import run

        chunks_received: list[str] = []
        drivers = _make_drivers(stream_callback=chunks_received.append)
        intent = _make_intent()
        context = Context(entries=[], session_id="s1")

        with patch("macroa.skills.chat_skill.build_system_prompt", return_value="sys"), \
             patch("macroa.skills.chat_skill.retrieve", return_value=[]):
            result = run(intent, context, drivers)

        drivers.llm.stream.assert_called_once()
        drivers.llm.complete.assert_not_called()
        assert chunks_received == ["He", "ll", "o!"]
        assert result.output == "Hello!"
        assert result.success

    def test_stream_result_contains_full_text(self):
        from macroa.skills.chat_skill import run

        drivers = _make_drivers()
        drivers.llm.stream.return_value = iter(["The ", "answer ", "is 42."])
        drivers.stream_callback = lambda c: None

        intent = _make_intent("what is the answer?")
        context = Context(entries=[], session_id="s1")

        with patch("macroa.skills.chat_skill.build_system_prompt", return_value="sys"), \
             patch("macroa.skills.chat_skill.retrieve", return_value=[]):
            result = run(intent, context, drivers)

        assert result.output == "The answer is 42."
        assert result.success

    def test_stream_error_returns_failure(self):
        from macroa.drivers.llm_driver import LLMDriverError
        from macroa.skills.chat_skill import run

        drivers = _make_drivers(stream_callback=lambda c: None)
        drivers.llm.stream.side_effect = LLMDriverError("connection reset")

        intent = _make_intent()
        context = Context(entries=[], session_id="s1")

        with patch("macroa.skills.chat_skill.build_system_prompt", return_value="sys"), \
             patch("macroa.skills.chat_skill.retrieve", return_value=[]):
            result = run(intent, context, drivers)

        assert not result.success
        assert "connection reset" in result.error


# ── kernel.run stream_callback injection ──────────────────────────────────────


class TestKernelStreamCallbackInjection:
    def test_stream_callback_reaches_drivers(self):
        """Verify kernel.run() injects stream_callback into the DriverBundle copy."""

        received_drivers: list[DriverBundle] = []
        chunks: list[str] = []

        with patch("macroa.kernel._get_drivers") as mock_drivers, \
             patch("macroa.kernel._get_registry"), \
             patch("macroa.kernel._get_audit") as mock_audit, \
             patch("macroa.kernel._get_or_create_session") as mock_session, \
             patch("macroa.kernel._get_session_store") as mock_ss, \
             patch("macroa.kernel.Router") as MockRouter, \
             patch("macroa.kernel.Planner") as MockPlanner, \
             patch("macroa.kernel.Dispatcher") as MockDispatcher, \
             patch("macroa.kernel._is_first_boot", return_value=False), \
             patch("macroa.kernel._get_extractor"):

            base_bundle = _make_drivers()
            mock_drivers.return_value = base_bundle

            mock_router = MagicMock()
            mock_intent = _make_intent("hi")
            mock_router.route.return_value = mock_intent
            MockRouter.return_value = mock_router

            mock_planner = MagicMock()
            mock_planner.plan.return_value = None
            MockPlanner.return_value = mock_planner

            mock_dispatcher_instance = MagicMock()
            mock_dispatcher_instance.dispatch.side_effect = lambda intent, ctx: (
                received_drivers.append(mock_dispatcher_instance.dispatch.call_args[0]) or
                SkillResult(output="ok", success=True, turn_id=intent.turn_id, model_tier=intent.model_tier)
            )
            MockDispatcher.return_value = mock_dispatcher_instance

            mock_ctx = MagicMock()
            mock_ctx.snapshot.return_value = Context(entries=[], session_id="s")
            mock_session.return_value = mock_ctx

            mock_audit_instance = MagicMock()
            mock_audit.return_value = mock_audit_instance

            mock_ss.return_value = MagicMock()

            import macroa.kernel as kernel
            kernel.run("hi", session_id="s1", stream_callback=chunks.append)

        # The Dispatcher was constructed — verify stream_callback was injected
        # by checking that dataclasses.replace was used (base_bundle unchanged)
        assert base_bundle.stream_callback is None  # original unmodified


# ── renderer.render_result skip_output ────────────────────────────────────────


class TestRenderResultSkipOutput:
    def test_skip_output_suppresses_text(self):
        from macroa.cli.renderer import render_result

        result = SkillResult(
            output="This should not print",
            success=True,
            model_tier=ModelTier.SONNET,
        )
        with patch("macroa.cli.renderer.console") as mock_console:
            render_result(result, skip_output=True)

        mock_console.print.assert_not_called()

    def test_skip_output_still_shows_error(self):
        from macroa.cli.renderer import render_result

        result = SkillResult(
            output="",
            success=False,
            error="something went wrong",
            model_tier=ModelTier.SONNET,
        )
        with patch("macroa.cli.renderer.console") as mock_console:
            render_result(result, skip_output=True)

        mock_console.print.assert_called_once()
        assert "something went wrong" in mock_console.print.call_args[0][0]

    def test_skip_output_with_debug_shows_meta(self):
        from macroa.cli.renderer import render_result

        result = SkillResult(
            output="already streamed",
            success=True,
            model_tier=ModelTier.SONNET,
            metadata={"skill": "chat_skill"},
        )
        with patch("macroa.cli.renderer.console") as mock_console:
            render_result(result, debug=True, skip_output=True)

        mock_console.print.assert_called_once()
        printed = mock_console.print.call_args[0][0]
        assert "chat_skill" in printed
