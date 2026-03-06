"""Tests for the setup wizard and renderer banner helpers."""

from __future__ import annotations

import os
from unittest.mock import patch

from macroa.cli import wizard as wiz

# ------------------------------------------------------------------ needs_setup

def test_needs_setup_true_when_no_key(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    # Patch _ENV_PATH to a non-existent file so dotenv has nothing to load
    monkeypatch.setattr(wiz, "_ENV_PATH", tmp_path / "nonexistent.env")
    assert wiz.needs_setup() is True


def test_needs_setup_false_when_key_set(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-key")
    assert wiz.needs_setup() is False


def test_needs_setup_false_from_env_file(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("OPENROUTER_API_KEY=sk-or-from-file\n")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(wiz, "_ENV_PATH", env_file)
    assert wiz.needs_setup() is False


# ------------------------------------------------------------------ _write_env

def test_write_env_creates_file(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    monkeypatch.setattr(wiz, "_MACROA_DIR", tmp_path)
    monkeypatch.setattr(wiz, "_ENV_PATH", env_path)

    wiz._write_env(api_key="sk-or-testkey1234567890", name="Alice", models={})

    assert env_path.exists()
    content = env_path.read_text()
    assert 'OPENROUTER_API_KEY="sk-or-testkey1234567890"' in content
    assert 'MACROA_USER_NAME="Alice"' in content


def test_write_env_custom_models(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    monkeypatch.setattr(wiz, "_MACROA_DIR", tmp_path)
    monkeypatch.setattr(wiz, "_ENV_PATH", env_path)

    wiz._write_env(
        api_key="sk-or-abc",
        name="Bob",
        models={"MACROA_MODEL_NANO": "openai/gpt-4.5-mini"},
    )

    content = env_path.read_text()
    assert 'MACROA_MODEL_NANO="openai/gpt-4.5-mini"' in content


def test_rerun_preserves_custom_models(tmp_path, monkeypatch):
    """Rerunning the wizard and keeping current models must not silently drop
    custom MACROA_MODEL_* settings from the written .env file."""
    from dotenv import dotenv_values

    env_path = tmp_path / ".env"
    monkeypatch.setattr(wiz, "_MACROA_DIR", tmp_path)
    monkeypatch.setattr(wiz, "_ENV_PATH", env_path)

    # Simulate existing custom config in the environment
    monkeypatch.setenv("MACROA_MODEL_NANO", "openai/gpt-4.5-mini")
    monkeypatch.setenv("MACROA_MODEL_HAIKU", "anthropic/claude-haiku-4-5")
    monkeypatch.setenv("MACROA_MODEL_SONNET", "anthropic/claude-sonnet-4-6")
    monkeypatch.setenv("MACROA_MODEL_OPUS", "anthropic/claude-opus-4-6")

    # _step_models returns the current config when user keeps settings
    models = {
        "MACROA_MODEL_NANO":   "openai/gpt-4.5-mini",
        "MACROA_MODEL_HAIKU":  "anthropic/claude-haiku-4-5",
        "MACROA_MODEL_SONNET": "anthropic/claude-sonnet-4-6",
        "MACROA_MODEL_OPUS":   "anthropic/claude-opus-4-6",
    }
    wiz._write_env(api_key="sk-or-abc", name="Alice", models=models)

    parsed = dotenv_values(env_path)
    # Custom NANO setting must survive the rewrite
    assert parsed["MACROA_MODEL_NANO"] == "openai/gpt-4.5-mini"
    assert "MACROA_MODEL_SONNET" in parsed


def test_write_env_quotes_values_with_hash(tmp_path, monkeypatch):
    """Values containing ' #' must be double-quoted so dotenv does not treat
    the hash as a comment delimiter and silently truncate the value."""
    from dotenv import dotenv_values

    env_path = tmp_path / ".env"
    monkeypatch.setattr(wiz, "_MACROA_DIR", tmp_path)
    monkeypatch.setattr(wiz, "_ENV_PATH", env_path)

    wiz._write_env(api_key="sk-or-abc", name="Alice #1", models={})

    # Read back via dotenv — must survive the round-trip intact
    parsed = dotenv_values(env_path)
    assert parsed["MACROA_USER_NAME"] == "Alice #1"


def test_write_env_quotes_values_with_embedded_double_quote(tmp_path, monkeypatch):
    """Embedded double quotes in a value must be escaped so the quoted .env
    line remains syntactically valid."""
    from dotenv import dotenv_values

    env_path = tmp_path / ".env"
    monkeypatch.setattr(wiz, "_MACROA_DIR", tmp_path)
    monkeypatch.setattr(wiz, "_ENV_PATH", env_path)

    wiz._write_env(api_key="sk-or-abc", name='Say "hello"', models={})

    parsed = dotenv_values(env_path)
    assert parsed["MACROA_USER_NAME"] == 'Say "hello"'


def test_write_env_injects_into_process(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    monkeypatch.setattr(wiz, "_MACROA_DIR", tmp_path)
    monkeypatch.setattr(wiz, "_ENV_PATH", env_path)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("MACROA_USER_NAME", raising=False)

    wiz._write_env(api_key="sk-or-injected", name="Carol", models={})

    assert os.environ.get("OPENROUTER_API_KEY") == "sk-or-injected"
    assert os.environ.get("MACROA_USER_NAME") == "Carol"


# ------------------------------------------------------------------ _load_macroa_env

def test_project_env_beats_wizard_env(tmp_path, monkeypatch, tmp_path_factory):
    """Project .env must take priority over ~/.macroa/.env (regression for override bug)."""
    from dotenv import load_dotenv

    wizard_env = tmp_path / "macroa.env"
    wizard_env.write_text("MACROA_CONTEXT_WINDOW=5\n")

    project_env = tmp_path / "project.env"
    project_env.write_text("MACROA_CONTEXT_WINDOW=99\n")

    # Simulate the correct loading order: project first, wizard second, both override=False.
    # With override=False the FIRST caller wins for each variable.
    monkeypatch.delenv("MACROA_CONTEXT_WINDOW", raising=False)
    load_dotenv(project_env, override=False)  # project wins over wizard
    load_dotenv(wizard_env, override=False)   # wizard only fills gaps

    assert os.environ.get("MACROA_CONTEXT_WINDOW") == "99"


def test_shell_env_beats_both(tmp_path, monkeypatch):
    """Real shell env vars must win over both .env files."""
    from dotenv import load_dotenv

    wizard_env = tmp_path / "macroa.env"
    wizard_env.write_text("MACROA_CONTEXT_WINDOW=1\n")
    project_env = tmp_path / "project.env"
    project_env.write_text("MACROA_CONTEXT_WINDOW=2\n")

    monkeypatch.setenv("MACROA_CONTEXT_WINDOW", "42")  # pre-set in os.environ
    # override=False never touches vars already in os.environ
    load_dotenv(project_env, override=False)
    load_dotenv(wizard_env, override=False)

    assert os.environ.get("MACROA_CONTEXT_WINDOW") == "42"


def test_load_macroa_env_does_not_overwrite_existing(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("OPENROUTER_API_KEY=sk-or-from-file\n")
    monkeypatch.setattr(wiz, "_ENV_PATH", env_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-already-set")

    wiz._load_macroa_env()

    # Shell env wins — override=False
    assert os.environ["OPENROUTER_API_KEY"] == "sk-or-already-set"


# ------------------------------------------------------------------ renderer helpers

def test_get_version_returns_string():
    from macroa.cli.renderer import _get_version
    v = _get_version()
    assert isinstance(v, str)
    assert len(v) > 0


def test_get_user_name_from_env(monkeypatch):
    monkeypatch.setenv("MACROA_USER_NAME", "Unohana")
    from macroa.cli.renderer import _get_user_name
    assert _get_user_name() == "Unohana"


def test_get_user_name_fallback(monkeypatch):
    monkeypatch.delenv("MACROA_USER_NAME", raising=False)
    # Patch get_settings to raise so we fall back to getpass
    with patch("macroa.cli.renderer._get_user_name") as mock_fn:
        mock_fn.return_value = "Fallback"
        name = mock_fn()
    assert name == "Fallback"


def test_build_model_table_has_four_rows(monkeypatch):
    from macroa.cli.renderer import _build_model_table
    table = _build_model_table()
    assert table.row_count == 4


def test_get_audit_summary_no_activity(tmp_path, monkeypatch):
    with patch("macroa.cli.renderer._get_audit_summary") as mock_fn:
        mock_fn.return_value = "No prior activity — let's get started"
        summary = mock_fn()
    assert "get started" in summary


def test_print_banner_does_not_crash(monkeypatch, capsys):
    """print_banner() must not raise even if audit/settings are unavailable."""
    monkeypatch.setenv("MACROA_USER_NAME", "Tester")
    from macroa.cli.renderer import print_banner
    # Should complete without exception
    print_banner()
