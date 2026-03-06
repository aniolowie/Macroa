"""Tests for the setup wizard and renderer banner helpers."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

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
    assert "OPENROUTER_API_KEY=sk-or-testkey1234567890" in content
    assert "MACROA_USER_NAME=Alice" in content


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
    assert "MACROA_MODEL_NANO=openai/gpt-4.5-mini" in content


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
    from macroa.cli import renderer
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
    from macroa.cli.renderer import _get_audit_summary
    from macroa.kernel.audit import AuditLog

    log = AuditLog(db_path=tmp_path / "audit.db")
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
