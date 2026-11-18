from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.exceptions import LLMOutputError
from app.schemas import LLMOutput
from app.services.json_schema import make_strict_json_schema
from app.services.llm_service import (
    GeminiProvider,
    LLMProviderError,
    LLMService,
    OpenAICompatibleProvider,
    ProviderResult,
    parse_json_object,
)
from app.services.pricing import estimate_cost_usd


def _assert_strict_objects(node: Any) -> None:
    if isinstance(node, list):
        for item in node:
            _assert_strict_objects(item)
        return
    if not isinstance(node, dict):
        return
    if node.get("type") == "object":
        properties = node.get("properties", {})
        assert node.get("additionalProperties") is False
        assert set(node.get("required", [])) == set(properties)
    for value in node.values():
        _assert_strict_objects(value)


def test_strict_schema_is_inlined_and_openai_compatible() -> None:
    schema = make_strict_json_schema(LLMOutput)
    assert "$defs" not in schema
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    _assert_strict_objects(schema)
    assert "target_search_queries" in json.dumps(schema)


def test_parse_json_object_handles_fences_and_prose() -> None:
    assert parse_json_object('```json\n{"ok": true}\n```') == {"ok": True}
    assert parse_json_object('Here is the result: {"ok": true} Thanks') == {"ok": True}


def test_parse_json_object_rejects_non_json() -> None:
    with pytest.raises(LLMOutputError):
        parse_json_object("definitely not json")


def test_cost_estimate_uses_current_list_price() -> None:
    cost = estimate_cost_usd("gemini-3.5-flash-lite", 6_000, 3_000)
    assert cost == pytest.approx(0.0093)
    assert estimate_cost_usd("unknown-model", 100, 100) is None


@pytest.mark.asyncio
async def test_openai_provider_uses_strict_structured_output() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": '{"ok": true}'},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 120, "completion_tokens": 30},
            },
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            api_key="test-key",
            model="gpt-6-luna",
            base_url="https://api.openai.com/v1",
            max_output_tokens=5000,
            reasoning_effort="none",
            timeout=10,
            client=client,
        )
        result = await provider.complete(
            system_prompt="system",
            user_prompt="user",
            schema={"type": "object", "properties": {}, "additionalProperties": False},
        )

    assert captured["url"] == "https://api.openai.com/v1/chat/completions"
    assert captured["headers"]["authorization"] == "Bearer test-key"
    assert captured["body"]["response_format"]["type"] == "json_schema"
    assert captured["body"]["response_format"]["json_schema"]["strict"] is True
    assert captured["body"]["reasoning_effort"] == "none"
    assert result.text == '{"ok": true}'
    assert result.input_tokens == 120
    assert result.output_tokens == 30


@pytest.mark.asyncio
async def test_gemini_provider_uses_json_mode_and_reads_usage() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "internal thought", "thought": True},
                                {"text": '{"ok": true}'},
                            ]
                        },
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 100,
                    "candidatesTokenCount": 25,
                    "thoughtsTokenCount": 5,
                },
            },
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = GeminiProvider(
            api_key="gemini-key",
            model="gemini-3.5-flash-lite",
            base_url="https://generativelanguage.googleapis.com/v1beta",
            temperature=0.2,
            max_output_tokens=5000,
            timeout=10,
            client=client,
        )
        result = await provider.complete(
            system_prompt="system",
            user_prompt="user",
            schema={"type": "object"},
        )

    assert ":generateContent" in captured["url"]
    assert captured["headers"]["x-goog-api-key"] == "gemini-key"
    assert captured["body"]["generationConfig"]["responseMimeType"] == "application/json"
    assert captured["body"]["systemInstruction"]["parts"][0]["text"] == "system"
    assert result.text == '{"ok": true}'
    assert result.output_tokens == 30


class StubProvider:
    def __init__(self, name: str, outcomes: list[object]) -> None:
        self.name = name
        self.model = "stub-model-1"
        self.outcomes = outcomes
        self.calls: list[str] = []

    async def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: dict[str, Any],
    ) -> ProviderResult:
        del system_prompt, schema
        self.calls.append(user_prompt)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return ProviderResult(
            text=str(outcome),
            provider=self.name,
            model=self.model,
            input_tokens=100,
            output_tokens=50,
        )


@pytest.mark.asyncio
async def test_invalid_output_triggers_one_repair_call(
    sample_extraction,
    valid_llm_output: LLMOutput,
) -> None:
    valid_json = json.dumps(valid_llm_output.model_dump(mode="json"))
    provider = StubProvider("stub", ["not json", valid_json])
    service = LLMService(providers=[provider], repair_attempts=1)

    result = await service.analyze(sample_extraction)

    assert len(provider.calls) == 2
    assert "CORRECTION REQUIRED" in provider.calls[1]
    assert result.output.brand.name == "Acme Cloud"
    assert result.fallback_used is False


@pytest.mark.asyncio
async def test_provider_failure_falls_back(
    sample_extraction,
    valid_llm_output: LLMOutput,
) -> None:
    valid_json = json.dumps(valid_llm_output.model_dump(mode="json"))
    primary = StubProvider("primary", [LLMProviderError("upstream down")])
    fallback = StubProvider("fallback", [valid_json])
    service = LLMService(providers=[primary, fallback], repair_attempts=1)

    result = await service.analyze(sample_extraction)

    assert result.fallback_used is True
    assert result.provider == "fallback"
    assert any("fallback" in warning.lower() for warning in result.warnings)
