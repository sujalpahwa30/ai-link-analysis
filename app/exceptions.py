"""Application-specific exceptions and their HTTP mapping."""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base class for expected, user-facing application errors."""

    status_code = 500
    code = "INTERNAL_ERROR"
    message = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.message
        self.code = code or self.code
        self.status_code = status_code or self.status_code
        self.details = details
        super().__init__(self.message)


class InvalidUrlError(AppError):
    status_code = 400
    code = "INVALID_URL"
    message = "The supplied URL is not valid."


class BlockedUrlError(AppError):
    status_code = 400
    code = "BLOCKED_URL"
    message = "The supplied URL points to a private, local, or otherwise blocked network address."


class WebsiteFetchError(AppError):
    status_code = 422
    code = "WEBSITE_FETCH_FAILED"
    message = "The website could not be fetched."


class WebsiteTimeoutError(WebsiteFetchError):
    status_code = 504
    code = "WEBSITE_TIMEOUT"
    message = "The website did not respond before the timeout."


class WebsiteTooLargeError(WebsiteFetchError):
    status_code = 413
    code = "WEBSITE_TOO_LARGE"
    message = "The website response is larger than the configured limit."


class WebsiteBlockedError(WebsiteFetchError):
    status_code = 422
    code = "WEBSITE_BLOCKED"
    message = "The website refused automated access or presented a bot challenge."


class ContentExtractionError(AppError):
    status_code = 422
    code = "CONTENT_EXTRACTION_FAILED"
    message = "No meaningful text content could be extracted from the website."


class LLMConfigurationError(AppError):
    status_code = 503
    code = "LLM_NOT_CONFIGURED"
    message = "No LLM provider is configured. Set GEMINI_API_KEY or OPENAI_API_KEY."


class LLMUpstreamError(AppError):
    status_code = 502
    code = "LLM_UPSTREAM_ERROR"
    message = "The language model provider could not complete the analysis."


class LLMOutputError(AppError):
    status_code = 502
    code = "LLM_INVALID_OUTPUT"
    message = "The language model returned an invalid analysis document."
