"""Tests for router JSON extraction robustness and LLM driver JSON mode."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from macroa.kernel.router import Router, _extract_json
from macroa.stdlib.schema import Context

# ------------------------------------------------------------------ _extract_json


def test_extract_json_plain():
    raw = '{"skill_name": "chat_skill", "parameters": {}, "confidence": 0.9, "reasoning": ""}'
    assert json.loads(_extract_json(raw)) == {
        "skill_name": "chat_skill",
        "parameters": {},
        "confidence": 0.9,
        "reasoning": "",
    }


def test_extract_json_fenced_json():
    raw = '```json\n{"skill_name": "chat_skill", "parameters": {}, "confidence": 0.5, "reasoning": ""}\n```'
    assert json.loads(_extract_json(raw))["skill_name"] == "chat_skill"


def test_extract_json_fenced_no_lang():
    raw = '```\n{"skill_name": "shell_skill", "parameters": {}, "confidence": 1.0, "reasoning": ""}\n```'
    assert json.loads(_extract_json(raw))["skill_name"] == "shell_skill"


def test_extract_json_whitespace():
    raw = '   \n  {"skill_name": "chat_skill", "parameters": {}, "confidence": 0.1, "reasoning": ""}  \n'
    assert json.loads(_extract_json(raw))["skill_name"] == "chat_skill"


# ------------------------------------------------------------------ Router.route


def _make_router(llm_response: str) -> tuple[Router, MagicMock]:
    llm = MagicMock()
    llm.complete.return_value = llm_response

    registry = MagicMock()
    entry = MagicMock()
    entry.manifest.model_tier = None
    registry.get.return_value = entry
    registry.all_manifests.return_value = []

    router = Router(llm=llm, registry=registry)
    return router, registry


def _empty_context() -> Context:
    return Context(entries=[], session_id="test")


def test_router_valid_json():
    payload = json.dumps({
        "skill_name": "chat_skill",
        "parameters": {},
        "confidence": 0.8,
        "reasoning": "general question",
    })
    router, _ = _make_router(payload)
    intent = router.route("hello", _empty_context())
    assert intent.skill_name == "chat_skill"
    assert intent.routing_confidence == pytest.approx(0.8)


def test_router_empty_response_falls_back():
    """Empty LLM response (JSONDecodeError) should silently fall back to chat_skill."""
    router, _ = _make_router("")
    intent = router.route("hello", _empty_context())
    assert intent.skill_name == "chat_skill"
    assert intent.routing_confidence == 0.0


def test_router_markdown_fenced_json():
    payload = "```json\n" + json.dumps({
        "skill_name": "memory_skill",
        "parameters": {"action": "search", "query": "name"},
        "confidence": 0.95,
        "reasoning": "user asking about stored facts",
    }) + "\n```"
    router, _ = _make_router(payload)
    intent = router.route("what do you know about me", _empty_context())
    assert intent.skill_name == "memory_skill"
    assert intent.parameters["action"] == "search"


def test_router_unknown_skill_falls_back():
    payload = json.dumps({
        "skill_name": "nonexistent_skill",
        "parameters": {},
        "confidence": 0.7,
        "reasoning": "",
    })
    llm = MagicMock()
    llm.complete.return_value = payload
    registry = MagicMock()
    # get() returns None for unknown skill, but a valid entry for chat_skill fallback
    chat_entry = MagicMock()
    chat_entry.manifest.model_tier = None
    registry.get.side_effect = lambda name: None if name == "nonexistent_skill" else chat_entry
    registry.all_manifests.return_value = []

    router = Router(llm=llm, registry=registry)
    intent = router.route("do something weird", _empty_context())
    assert intent.skill_name == "chat_skill"
    assert intent.routing_confidence == pytest.approx(0.3)
