"""Provider-agnostic LLM integration with strict validation and one repair attempt."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import quote

import httpx
from pydantic import ValidationError

from app.config import Settings
from app.exceptions import LLMOutputError, LLMUpstreamError
from app.schemas import LLMOutput, LLMUsage
from app.services.json_schema import make_strict_json_schema
from app.services.pricing import estimate_cost_usd
from app.services.prompts import (
    SYSTEM_PROMPT,
    build_repair_prompt,
    build_user_prompt,
    compact_error,
)
from app.services.website_extractor import ExtractedWebsite

logger = logging.getLogger(__name__)


class LLMProviderError(Exception):
    """A provider request failed before producing usable output."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        self.message = message
        self.retryable = retryable
        super().__init__(message)


@dataclass(slots=True)
class ProviderResult:
    text: str
    provider: str
    model: str
    input_tokens: int | None
    output_tokens: int | None


@dataclass(slots=True)
class LLMResult:
    output: LLMOutput
    provider: str
    model: str
    fallback_used: bool
    usage: LLMUsage
    warnings: list[str]


class LLMProvider(Protocol):
    name: str
    model: str

    async def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: dict[str, Any],
    ) -> ProviderResult: ...


def _extract_error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = None

    message = ""
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = str(error.get("message", ""))
        elif error:
            message = str(error)
        if not message:
            message = str(payload.get("message", ""))
    if not message:
        message = response.text[:400]
    message = " ".join(message.split())[:500]
    return message or f"HTTP {response.status_code}"


async def _post_with_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    """POST JSON with one retry for rate limits and transient gateway errors."""

    last_error: LLMProviderError | None = None
    for attempt in range(2):
        try:
            response = await client.post(
                url,
                headers=headers,
                json=payload,
                timeout=httpx.Timeout(timeout, connect=min(10.0, timeout)),
            )
        except httpx.TimeoutException as exc:
            last_error = LLMProviderError("LLM provider timed out.", retryable=True)
            if attempt == 0:
                await asyncio.sleep(0.75)
                continue
            raise last_error from exc
        except httpx.HTTPError as exc:
            last_error = LLMProviderError("LLM provider connection failed.", retryable=True)
            if attempt == 0:
                await asyncio.sleep(0.75)
                continue
            raise last_error from exc

        if response.status_code < 400:
            try:
                body = response.json()
            except ValueError as exc:
                raise LLMProviderError("LLM provider returned a non-JSON HTTP body.") from exc
            if not isinstance(body, dict):
                raise LLMProviderError("LLM provider returned an unexpected JSON body.")
            return body

        message = _extract_error_message(response)
        transient = response.status_code in {408, 409, 429, 500, 502, 503, 504}
        last_error = LLMProviderError(
            f"LLM provider returned HTTP {response.status_code}: {message}",
            retryable=transient,
        )
        if transient and attempt == 0:
            await asyncio.sleep(1.0)
            continue
        raise last_error

    raise last_error or LLMProviderError("LLM provider request failed.")


class OpenAICompatibleProvider:
    name = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        max_output_tokens: int,
        reasoning_effort: str,
        timeout: float,
        client: httpx.AsyncClient,
    ) -> None:
        self._api_key = api_key
        self.model = model
        self._base_url = base_url.rstrip("/")
        self._max_output_tokens = max_output_tokens
        self._reasoning_effort = reasoning_effort
        self._timeout = timeout
        self._client = client

    async def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: dict[str, Any],
    ) -> ProviderResult:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "website_analysis",
                    "strict": True,
                    "schema": schema,
                },
            },
            "max_completion_tokens": self._max_output_tokens,
        }
        if self._reasoning_effort:
            payload["reasoning_effort"] = self._reasoning_effort

        body = await _post_with_retry(
            self._client,
            f"{self._base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            payload=payload,
            timeout=self._timeout,
        )

        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LLMProviderError("OpenAI response contained no choices.")
        choice = choices[0] if isinstance(choices[0], dict) else {}
        finish_reason = choice.get("finish_reason")
        if finish_reason == "length":
            raise LLMProviderError("OpenAI response was truncated at the output token limit.")
        message = choice.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            content = "".join(
                str(part.get("text", ""))
                for part in content
                if isinstance(part, dict)
            )
        if not isinstance(content, str) or not content.strip():
            raise LLMProviderError("OpenAI returned an empty response.")

        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        input_tokens = usage.get("prompt_tokens")
        output_tokens = usage.get("completion_tokens")
        return ProviderResult(
            text=content,
            provider=self.name,
            model=self.model,
            input_tokens=input_tokens if isinstance(input_tokens, int) else None,
            output_tokens=output_tokens if isinstance(output_tokens, int) else None,
        )


class GeminiProvider:
    name = "gemini"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        temperature: float,
        max_output_tokens: int,
        timeout: float,
        client: httpx.AsyncClient,
    ) -> None:
        self._api_key = api_key
        self.model = model
        self._base_url = base_url.rstrip("/")
        self._temperature = temperature
        self._max_output_tokens = max_output_tokens
        self._timeout = timeout
        self._client = client

    async def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: dict[str, Any],
    ) -> ProviderResult:
        # JSON mode is used rather than responseSchema: JSON mode is supported across
        # all current Gemini text models and avoids provider-specific schema keyword
        # incompatibilities. Pydantic remains the final authority on the contract.
        del schema
        model_path = quote(self.model, safe="-._")
        payload = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {
                "temperature": self._temperature,
                "maxOutputTokens": self._max_output_tokens,
                "responseMimeType": "application/json",
            },
        }
        body = await _post_with_retry(
            self._client,
            f"{self._base_url}/models/{model_path}:generateContent",
            headers={
                "x-goog-api-key": self._api_key,
                "Content-Type": "application/json",
            },
            payload=payload,
            timeout=self._timeout,
        )

        prompt_feedback = body.get("promptFeedback")
        if isinstance(prompt_feedback, dict) and prompt_feedback.get("blockReason"):
            raise LLMProviderError("Gemini blocked the request due to safety settings.")

        candidates = body.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise LLMProviderError("Gemini response contained no candidates.")
        candidate = candidates[0] if isinstance(candidates[0], dict) else {}
        finish_reason = candidate.get("finishReason")
        if finish_reason == "MAX_TOKENS":
            raise LLMProviderError("Gemini response was truncated at the output token limit.")

        content = candidate.get("content")
        parts = content.get("parts") if isinstance(content, dict) else None
        if not isinstance(parts, list):
            raise LLMProviderError("Gemini returned no content parts.")
        text = "".join(
            str(part.get("text", ""))
            for part in parts
            if isinstance(part, dict) and not part.get("thought")
        ).strip()
        if not text:
            raise LLMProviderError("Gemini returned an empty response.")

        usage = body.get("usageMetadata") if isinstance(body.get("usageMetadata"), dict) else {}
        input_tokens = usage.get("promptTokenCount")
        candidates_tokens = usage.get("candidatesTokenCount", 0)
        thoughts_tokens = usage.get("thoughtsTokenCount", 0)
        output_tokens: int | None = None
        if isinstance(candidates_tokens, int):
            output_tokens = candidates_tokens + (
                thoughts_tokens if isinstance(thoughts_tokens, int) else 0
            )
        return ProviderResult(
            text=text,
            provider=self.name,
            model=self.model,
            input_tokens=input_tokens if isinstance(input_tokens, int) else None,
            output_tokens=output_tokens,
        )


def parse_json_object(raw: str) -> dict[str, Any]:
    text = raw.strip().lstrip("\ufeff")
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise LLMOutputError("The model response did not contain a JSON object.")

    candidate = text[start : end + 1]
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise LLMOutputError(f"The model response was not valid JSON: {exc.msg}.") from exc
    if not isinstance(parsed, dict):
        raise LLMOutputError("The model response must be a JSON object.")
    return parsed


class LLMService:
    def __init__(
        self,
        *,
        providers: list[LLMProvider],
        repair_attempts: int = 1,
    ) -> None:
        if not providers:
            raise ValueError("At least one LLM provider is required.")
        self._providers = providers
        self._repair_attempts = repair_attempts
        self._schema = make_strict_json_schema(LLMOutput)

    async def analyze(self, extraction: ExtractedWebsite) -> LLMResult:
        base_prompt = build_user_prompt(extraction)
        errors: list[str] = []

        for provider_index, provider in enumerate(self._providers):
            prompt = base_prompt
            for attempt in range(self._repair_attempts + 1):
                try:
                    result = await provider.complete(
                        system_prompt=SYSTEM_PROMPT,
                        user_prompt=prompt,
                        schema=self._schema,
                    )
                except LLMProviderError as exc:
                    logger.warning(
                        "LLM provider failed provider=%s model=%s attempt=%s error=%s",
                        provider.name,
                        provider.model,
                        attempt + 1,
                        exc.message,
                    )
                    errors.append(f"{provider.name}: {exc.message}")
                    break

                try:
                    parsed = parse_json_object(result.text)
                    output = LLMOutput.model_validate(parsed)
                except (LLMOutputError, ValidationError) as exc:
                    logger.warning(
                        "LLM output validation failed provider=%s model=%s attempt=%s",
                        provider.name,
                        provider.model,
                        attempt + 1,
                    )
                    errors.append(f"{provider.name}: {compact_error(exc)}")
                    if attempt < self._repair_attempts:
                        prompt = build_repair_prompt(base_prompt, compact_error(exc))
                        continue
                    break

                total_tokens = None
                if result.input_tokens is not None and result.output_tokens is not None:
                    total_tokens = result.input_tokens + result.output_tokens
                usage = LLMUsage(
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    total_tokens=total_tokens,
                    estimated_cost_usd=estimate_cost_usd(
                        result.model,
                        result.input_tokens,
                        result.output_tokens,
                    ),
                )
                warnings: list[str] = []
                if provider_index > 0:
                    warnings.append(
                        "Primary LLM provider failed; analysis was completed with the fallback provider."
                    )
                return LLMResult(
                    output=output,
                    provider=result.provider,
                    model=result.model,
                    fallback_used=provider_index > 0,
                    usage=usage,
                    warnings=warnings,
                )

        logger.error("All configured LLM providers failed: %s", " | ".join(errors)[:1500])
        raise LLMUpstreamError(
            "All configured language-model providers failed to produce a valid analysis."
        )


def build_llm_service(settings: Settings, client: httpx.AsyncClient) -> LLMService | None:
    """Build the primary provider and an optional cross-provider fallback."""

    resolved = settings.resolved_llm_provider
    if resolved == "demo":
        return None

    providers: list[LLMProvider] = []
    if resolved == "gemini" and settings.gemini_api_key:
        providers.append(
            GeminiProvider(
                api_key=settings.gemini_api_key.get_secret_value(),
                model=settings.gemini_model,
                base_url=settings.gemini_base_url,
                temperature=settings.gemini_temperature,
                max_output_tokens=settings.llm_max_output_tokens,
                timeout=settings.llm_timeout_seconds,
                client=client,
            )
        )
    elif resolved == "openai" and settings.openai_api_key:
        providers.append(
            OpenAICompatibleProvider(
                api_key=settings.openai_api_key.get_secret_value(),
                model=settings.openai_model,
                base_url=settings.openai_base_url,
                max_output_tokens=settings.llm_max_output_tokens,
                reasoning_effort=settings.openai_reasoning_effort,
                timeout=settings.llm_timeout_seconds,
                client=client,
            )
        )

    if settings.enable_llm_fallback:
        if resolved != "gemini" and settings.gemini_api_key:
            providers.append(
                GeminiProvider(
                    api_key=settings.gemini_api_key.get_secret_value(),
                    model=settings.gemini_model,
                    base_url=settings.gemini_base_url,
                    temperature=settings.gemini_temperature,
                    max_output_tokens=settings.llm_max_output_tokens,
                    timeout=settings.llm_timeout_seconds,
                    client=client,
                )
            )
        elif resolved != "openai" and settings.openai_api_key:
            providers.append(
                OpenAICompatibleProvider(
                    api_key=settings.openai_api_key.get_secret_value(),
                    model=settings.openai_model,
                    base_url=settings.openai_base_url,
                    max_output_tokens=settings.llm_max_output_tokens,
                    reasoning_effort=settings.openai_reasoning_effort,
                    timeout=settings.llm_timeout_seconds,
                    client=client,
                )
            )

    if not providers:
        return None
    return LLMService(providers=providers, repair_attempts=settings.llm_repair_attempts)
