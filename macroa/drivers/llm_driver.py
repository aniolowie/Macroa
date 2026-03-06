"""LLM driver — OpenRouter via openai SDK."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from openai import APIError, OpenAI

from macroa.stdlib.schema import ModelTier


class LLMDriverError(Exception):
    pass


class LLMDriver:
    def __init__(
        self,
        api_key: str,
        model_map: dict[ModelTier, str],
        http_referer: str = "",
        app_title: str = "Macroa",
    ) -> None:
        self._model_map = model_map
        extra_headers: dict[str, str] = {}
        if http_referer:
            extra_headers["HTTP-Referer"] = http_referer
        if app_title:
            extra_headers["X-Title"] = app_title

        self._client = OpenAI(
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
            default_headers=extra_headers if extra_headers else None,
        )

    def complete(
        self,
        messages: list[dict[str, str]],
        tier: ModelTier,
        *,
        expect_json: bool = False,
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> str:
        model_id = self._model_map.get(tier)
        if not model_id:
            raise LLMDriverError(f"No model mapped for tier {tier!r}")

        system_messages = [m for m in messages if m["role"] == "system"]
        other_messages = [m for m in messages if m["role"] != "system"]

        if expect_json:
            json_instruction = "Respond with only valid JSON, no other text."
            if system_messages:
                system_messages = [
                    {**m, "content": m["content"] + "\n\n" + json_instruction}
                    for m in system_messages
                ]
            else:
                system_messages = [{"role": "system", "content": json_instruction}]

        final_messages = system_messages + other_messages

        kwargs: dict[str, Any] = {
            "model": model_id,
            "messages": final_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if expect_json:
            kwargs["response_format"] = {"type": "json_object"}

        try:
            response = self._client.chat.completions.create(**kwargs)
            content = response.choices[0].message.content
            return content or ""
        except APIError as exc:
            raise LLMDriverError(f"OpenRouter API error: {exc}") from exc
        except Exception as exc:
            raise LLMDriverError(f"Unexpected LLM error: {exc}") from exc

    def complete_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        tier: ModelTier,
        *,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> tuple[str, list]:
        """Call LLM with tool schemas. Returns (content, tool_calls).

        content   — text the model produced alongside (or instead of) tool calls
        tool_calls — list of ChatCompletionMessageToolCall objects; empty when done
        """
        model_id = self._model_map.get(tier)
        if not model_id:
            raise LLMDriverError(f"No model mapped for tier {tier!r}")

        try:
            response = self._client.chat.completions.create(
                model=model_id,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                temperature=temperature,
                max_tokens=max_tokens,
            )
            msg = response.choices[0].message
            return (msg.content or "", list(msg.tool_calls or []))
        except APIError as exc:
            raise LLMDriverError(f"OpenRouter API error: {exc}") from exc
        except Exception as exc:
            raise LLMDriverError(f"Unexpected LLM error: {exc}") from exc

    def stream(
        self,
        messages: list[dict[str, str]],
        tier: ModelTier,
        *,
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> Iterator[str]:
        """
        Streaming variant — yields text chunks as they arrive from the API.
        Raises LLMDriverError on connection failures; individual chunk errors
        are silently skipped (empty delta).
        """
        model_id = self._model_map.get(tier)
        if not model_id:
            raise LLMDriverError(f"No model mapped for tier {tier!r}")

        try:
            with self._client.chat.completions.create(
                model=model_id,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
            ) as stream:
                for chunk in stream:
                    delta = chunk.choices[0].delta.content if chunk.choices else None
                    if delta:
                        yield delta
        except APIError as exc:
            raise LLMDriverError(f"OpenRouter stream error: {exc}") from exc
        except Exception as exc:
            raise LLMDriverError(f"Unexpected stream error: {exc}") from exc
