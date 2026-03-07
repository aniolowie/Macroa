"""Tests for channel adapters: BaseAdapter, TelegramAdapter, DiscordAdapter."""

from __future__ import annotations

from unittest.mock import MagicMock, patch, call


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_result(output="Hello from kernel"):
    from macroa.stdlib.schema import ModelTier, SkillResult
    return SkillResult(output=output, success=True, turn_id="t1", model_tier=ModelTier.HAIKU)


def _run_fn(text: str, session_id: str):
    return _make_result(f"echo: {text}")


# ── BaseAdapter ───────────────────────────────────────────────────────────────


class TestBaseAdapter:
    def _make_adapter(self):
        from macroa.channels.base import BaseAdapter

        class ConcreteAdapter(BaseAdapter):
            _platform = "test"
            polled: list = []
            sent: list = []

            def _poll_once(self):
                return self.polled.pop(0) if self.polled else []

            def _send(self, user_id, text):
                self.sent.append((user_id, text))

        return ConcreteAdapter(_run_fn)

    def test_get_session_creates_session(self):
        adapter = self._make_adapter()
        with patch("macroa.kernel.resolve_session", return_value="session-uuid") as mock:
            sid = adapter._get_session("user123")
        assert sid == "session-uuid"
        mock.assert_called_once_with("test_user123")

    def test_get_session_cached(self):
        adapter = self._make_adapter()
        with patch("macroa.kernel.resolve_session", return_value="session-uuid"):
            s1 = adapter._get_session("u1")
            s2 = adapter._get_session("u1")
        assert s1 == s2

    def test_handle_routes_to_run_fn(self):
        adapter = self._make_adapter()
        calls: list = []

        def tracking_run(text, session_id):
            calls.append((text, session_id))
            return _make_result("pong")

        adapter._run = tracking_run

        with patch("macroa.kernel.resolve_session", return_value="sid"):
            adapter._handle({"user_id": "u1", "text": "ping"})

        assert len(calls) == 1
        assert calls[0][0] == "ping"
        assert len(adapter.sent) == 1
        assert adapter.sent[0] == ("u1", "pong")

    def test_handle_empty_text_skipped(self):
        adapter = self._make_adapter()
        adapter._handle({"user_id": "u1", "text": "   "})
        assert len(adapter.sent) == 0

    def test_handle_run_error_sends_apology(self):
        adapter = self._make_adapter()

        def error_run(text, session_id):
            raise RuntimeError("LLM down")

        adapter._run = error_run
        with patch("macroa.kernel.resolve_session", return_value="sid"):
            adapter._handle({"user_id": "u1", "text": "hello"})

        assert len(adapter.sent) == 1
        assert "went wrong" in adapter.sent[0][1].lower()

    def test_start_creates_daemon_thread(self):
        import threading
        adapter = self._make_adapter()
        started: list = []
        with patch("threading.Thread") as MockThread:
            mock_t = MagicMock()
            MockThread.return_value = mock_t
            adapter.start()
        MockThread.assert_called_once()
        mock_t.start.assert_called_once()
        assert MockThread.call_args[1]["daemon"] is True

    def test_stop_sets_event(self):
        adapter = self._make_adapter()
        with patch.object(adapter, "_run_loop"):
            adapter.start()
        adapter.stop()
        assert adapter._stop.is_set()


# ── TelegramAdapter ───────────────────────────────────────────────────────────


class TestTelegramAdapter:
    def _make_adapter(self, **kwargs):
        from macroa.channels.telegram import TelegramAdapter
        return TelegramAdapter(token="test-token", run_fn=_run_fn, **kwargs)

    def test_poll_once_parses_updates(self):
        adapter = self._make_adapter()
        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.is_success = True
        fake_response.json.return_value = {
            "ok": True,
            "result": [
                {
                    "update_id": 100,
                    "message": {
                        "chat": {"id": 42},
                        "from": {"id": 42, "first_name": "Alice"},
                        "text": "hello bot",
                    },
                }
            ],
        }
        adapter._client = MagicMock()
        adapter._client.get.return_value = fake_response

        messages = adapter._poll_once()
        assert len(messages) == 1
        assert messages[0]["text"] == "hello bot"
        assert messages[0]["user_id"] == "42"
        assert adapter._offset == 101  # ACKed

    def test_poll_once_401_raises_adapter_error(self):
        from macroa.channels.base import AdapterError
        adapter = self._make_adapter()
        fake_response = MagicMock()
        fake_response.status_code = 401
        fake_response.is_success = False
        adapter._client = MagicMock()
        adapter._client.get.return_value = fake_response

        try:
            adapter._poll_once()
            assert False, "Expected AdapterError"
        except AdapterError:
            pass

    def test_poll_once_network_error_returns_empty(self):
        import httpx
        adapter = self._make_adapter()
        adapter._client = MagicMock()
        adapter._client.get.side_effect = httpx.RequestError("timeout")

        result = adapter._poll_once()
        assert result == []

    def test_allowed_users_filters(self):
        adapter = self._make_adapter(allowed_users={"999"})
        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.is_success = True
        fake_response.json.return_value = {
            "ok": True,
            "result": [
                {
                    "update_id": 1,
                    "message": {
                        "chat": {"id": 123},
                        "from": {"id": 123},
                        "text": "sneaky message",
                    },
                }
            ],
        }
        adapter._client = MagicMock()
        adapter._client.get.return_value = fake_response
        adapter._client.post.return_value = MagicMock()

        messages = adapter._poll_once()
        assert messages == []
        # Denied message sent
        adapter._client.post.assert_called_once()

    def test_send_splits_long_message(self):
        adapter = self._make_adapter()
        adapter._client = MagicMock()
        long_text = "x" * 5000
        adapter._send("42", long_text)
        assert adapter._client.post.call_count == 2  # 5000 / 4096 → 2 chunks

    def test_start_command_sends_welcome(self):
        adapter = self._make_adapter()
        sent: list = []

        def fake_send(uid, text):
            sent.append((uid, text))

        adapter._send = fake_send
        with patch("macroa.kernel.resolve_session", return_value="s"):
            adapter._handle({"user_id": "42", "text": "/start", "first_name": "Bob"})

        assert len(sent) == 1
        assert "Bob" in sent[0][1]

    def test_clear_command_clears_session(self):
        adapter = self._make_adapter()
        sent: list = []
        adapter._send = lambda uid, text: sent.append(text)

        with patch("macroa.kernel.resolve_session", return_value="s"), \
             patch("macroa.kernel.clear_session") as mock_clear:
            adapter._handle({"user_id": "42", "text": "/clear", "first_name": ""})

        mock_clear.assert_called_once()
        assert any("cleared" in t.lower() for t in sent)


# ── DiscordAdapter ────────────────────────────────────────────────────────────


class TestDiscordAdapter:
    def _make_adapter(self, **kwargs):
        from macroa.channels.discord import DiscordAdapter
        return DiscordAdapter(token="test-token", run_fn=_run_fn, **kwargs)

    def test_validate_token_ok(self):
        adapter = self._make_adapter()
        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.is_success = True
        fake_resp.json.return_value = {"id": "1", "username": "testbot"}
        adapter._client = MagicMock()
        adapter._client.get.return_value = fake_resp

        info = adapter.validate_token()
        assert info["username"] == "testbot"

    def test_validate_token_401_raises(self):
        from macroa.channels.base import AdapterError
        adapter = self._make_adapter()
        fake_resp = MagicMock()
        fake_resp.status_code = 401
        fake_resp.is_success = False
        adapter._client = MagicMock()
        adapter._client.get.return_value = fake_resp

        try:
            adapter.validate_token()
            assert False, "Expected AdapterError"
        except AdapterError:
            pass

    def test_on_message_bot_ignored(self):
        adapter = self._make_adapter()
        send_calls: list = []
        adapter._send_to_channel = lambda ch, text: send_calls.append(text)

        adapter._on_message({
            "author": {"id": "bot1", "bot": True},
            "channel_id": "ch1",
            "content": "I am a bot",
        })
        assert send_calls == []

    def test_on_message_routes_to_kernel(self):
        adapter = self._make_adapter(channel_ids=["ch1"])
        sent: list = []
        adapter._send_to_channel = lambda ch, text: sent.append(text)

        with patch("macroa.kernel.resolve_session", return_value="sid"):
            adapter._on_message({
                "author": {"id": "user1", "bot": False},
                "channel_id": "ch1",
                "content": "hello discord",
            })

        assert len(sent) == 1
        assert "echo:" in sent[0]

    def test_on_message_wrong_channel_ignored(self):
        adapter = self._make_adapter(channel_ids=["allowed-ch"])
        sent: list = []
        adapter._send_to_channel = lambda ch, text: sent.append(text)

        with patch("macroa.kernel.resolve_session", return_value="sid"):
            adapter._on_message({
                "author": {"id": "user1", "bot": False},
                "channel_id": "other-ch",
                "content": "hello",
            })

        assert sent == []

    def test_send_to_channel_splits_long_message(self):
        adapter = self._make_adapter()
        adapter._client = MagicMock()
        adapter._client.post.return_value = MagicMock()

        long_text = "y" * 4500
        adapter._send_to_channel("ch1", long_text)
        assert adapter._client.post.call_count == 3  # 4500 / 2000 → 3 chunks


# ── _split_message ────────────────────────────────────────────────────────────


class TestSplitMessage:
    def test_short_message_unchanged(self):
        from macroa.channels.telegram import _split_message
        assert _split_message("hello", 100) == ["hello"]

    def test_long_message_split_at_newline(self):
        from macroa.channels.telegram import _split_message
        text = "line1\nline2\n" + "x" * 10
        chunks = _split_message(text, 10)
        assert len(chunks) > 1
        # All chunks within limit
        assert all(len(c) <= 10 for c in chunks)

    def test_no_newline_splits_at_max_len(self):
        from macroa.channels.telegram import _split_message
        text = "a" * 25
        chunks = _split_message(text, 10)
        assert all(len(c) <= 10 for c in chunks)
        assert "".join(chunks) == text
