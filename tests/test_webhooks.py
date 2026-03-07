"""Tests for webhook store and template rendering."""

from __future__ import annotations

from pathlib import Path

# ── render_template ───────────────────────────────────────────────────────────


class TestRenderTemplate:
    def test_body_token(self):
        from macroa.web.webhooks import render_template

        result = render_template("received: {{body}}", {"event": "push"})
        assert '"event"' in result
        assert '"push"' in result

    def test_top_level_field(self):
        from macroa.web.webhooks import render_template

        result = render_template("repo is {{repo}}", {"repo": "myrepo"})
        assert result == "repo is myrepo"

    def test_dot_path(self):
        from macroa.web.webhooks import render_template

        result = render_template("user: {{user.name}}", {"user": {"name": "Alice"}})
        assert result == "user: Alice"

    def test_missing_field_left_unreplaced(self):
        from macroa.web.webhooks import render_template

        result = render_template("value: {{missing}}", {"other": "x"})
        assert "{{missing}}" in result

    def test_none_body_leaves_template(self):
        from macroa.web.webhooks import render_template

        result = render_template("hello {{name}}", None)
        assert "{{name}}" in result

    def test_no_placeholders_unchanged(self):
        from macroa.web.webhooks import render_template

        tmpl = "summarise the latest deploy status"
        assert render_template(tmpl, {"x": 1}) == tmpl

    def test_string_body_parsed_as_json(self):
        from macroa.web.webhooks import render_template

        result = render_template("event: {{type}}", '{"type": "alert"}')
        assert result == "event: alert"

    def test_multiple_placeholders(self):
        from macroa.web.webhooks import render_template

        result = render_template(
            "{{action}} on {{repo}} by {{actor}}",
            {"action": "push", "repo": "core", "actor": "bob"},
        )
        assert result == "push on core by bob"


# ── WebhookStore ──────────────────────────────────────────────────────────────


class TestWebhookStore:
    def test_create_and_get(self, tmp_path: Path):
        from macroa.web.webhooks import WebhookConfig, WebhookStore

        store = WebhookStore(db_path=tmp_path / "wh.db")
        wh = WebhookConfig(
            name="test-hook",
            command_template="ping {{host}}",
            session_id="s1",
        )
        store.create(wh)

        retrieved = store.get("test-hook")
        assert retrieved is not None
        assert retrieved.name == "test-hook"
        assert retrieved.command_template == "ping {{host}}"
        assert retrieved.session_id == "s1"
        assert len(retrieved.secret_key) > 10

    def test_secret_key_auto_generated(self, tmp_path: Path):
        from macroa.web.webhooks import WebhookConfig, WebhookStore

        store = WebhookStore(db_path=tmp_path / "wh.db")
        wh1 = WebhookConfig(name="a", command_template="x", session_id="s")
        wh2 = WebhookConfig(name="b", command_template="y", session_id="s")
        store.create(wh1)
        store.create(wh2)
        assert wh1.secret_key != wh2.secret_key

    def test_list_all(self, tmp_path: Path):
        from macroa.web.webhooks import WebhookConfig, WebhookStore

        store = WebhookStore(db_path=tmp_path / "wh.db")
        for name in ("hook-a", "hook-b", "hook-c"):
            store.create(WebhookConfig(name=name, command_template="t", session_id="s"))

        all_wh = store.list_all()
        assert len(all_wh) == 3
        names = {w.name for w in all_wh}
        assert names == {"hook-a", "hook-b", "hook-c"}

    def test_delete(self, tmp_path: Path):
        from macroa.web.webhooks import WebhookConfig, WebhookStore

        store = WebhookStore(db_path=tmp_path / "wh.db")
        store.create(WebhookConfig(name="del-me", command_template="t", session_id="s"))
        deleted = store.delete("del-me")
        assert deleted is True
        fetched = store.get("del-me")
        assert fetched is None

    def test_delete_nonexistent_returns_false(self, tmp_path: Path):
        from macroa.web.webhooks import WebhookStore

        store = WebhookStore(db_path=tmp_path / "wh.db")
        result = store.delete("nope")
        assert result is False

    def test_get_nonexistent_returns_none(self, tmp_path: Path):
        from macroa.web.webhooks import WebhookStore

        store = WebhookStore(db_path=tmp_path / "wh.db")
        assert store.get("missing") is None

    def test_record_trigger_increments_count(self, tmp_path: Path):
        from macroa.web.webhooks import WebhookConfig, WebhookStore

        store = WebhookStore(db_path=tmp_path / "wh.db")
        store.create(WebhookConfig(name="counter", command_template="t", session_id="s"))

        store.record_trigger("counter")
        store.record_trigger("counter")

        wh = store.get("counter")
        assert wh.trigger_count == 2
        assert wh.last_triggered_at is not None

    def test_record_trigger_stores_error(self, tmp_path: Path):
        from macroa.web.webhooks import WebhookConfig, WebhookStore

        store = WebhookStore(db_path=tmp_path / "wh.db")
        store.create(WebhookConfig(name="err-hook", command_template="t", session_id="s"))
        store.record_trigger("err-hook", error="LLM timeout")

        wh = store.get("err-hook")
        assert wh.last_error == "LLM timeout"

    def test_persist_across_instances(self, tmp_path: Path):
        """Data should survive closing and reopening the store."""
        from macroa.web.webhooks import WebhookConfig, WebhookStore

        db = tmp_path / "persist.db"
        store1 = WebhookStore(db_path=db)
        store1.create(WebhookConfig(name="persistent", command_template="cmd", session_id="s1"))

        store2 = WebhookStore(db_path=db)
        wh = store2.get("persistent")
        assert wh is not None
        assert wh.command_template == "cmd"
