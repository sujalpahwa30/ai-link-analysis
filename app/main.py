"""FastAPI entry point."""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.cache import TTLCache
from app.config import Settings, get_settings
from app.exceptions import AppError
from app.logging_config import configure_logging
from app.schemas import (
    AnalysisResponse,
    ErrorDetail,
    ErrorResponse,
    HealthResponse,
    LinkAnalysisRequest,
)
from app.services.analysis_service import AnalysisArtifact, AnalysisService
from app.services.llm_service import build_llm_service
from app.services.rate_limiter import SlidingWindowRateLimiter
from app.services.website_extractor import WebsiteExtractor

logger = logging.getLogger(__name__)
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


def create_app(
    settings: Settings | None = None,
    analysis_service: AnalysisService | None = None,
) -> FastAPI:
    app_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(app_settings.log_level)
        app.state.settings = app_settings

        if analysis_service is not None:
            app.state.analysis_service = analysis_service
            app.state.http_client = None
            logger.info("Application started with injected analysis service")
            yield
            return

        http_client = httpx.AsyncClient(
            follow_redirects=False,
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
        )
        try:
            cache: TTLCache[AnalysisArtifact] = TTLCache(
                ttl_seconds=app_settings.cache_ttl_seconds,
                max_entries=app_settings.cache_max_entries,
            )
            extractor = WebsiteExtractor(app_settings, client=http_client)
            llm_service = build_llm_service(app_settings, http_client)
            service = AnalysisService(
                settings=app_settings,
                extractor=extractor,
                cache=cache,
                llm_service=llm_service,
            )
            app.state.analysis_service = service
            app.state.http_client = http_client
            logger.info(
                "Application started provider=%s model=%s demo=%s",
                app_settings.resolved_llm_provider,
                app_settings.gemini_model
                if app_settings.resolved_llm_provider == "gemini"
                else app_settings.openai_model
                if app_settings.resolved_llm_provider == "openai"
                else "deterministic-heuristic-v1",
                app_settings.is_demo_mode,
            )
            yield
        finally:
            await http_client.aclose()

    app = FastAPI(
        title=app_settings.app_name,
        version=app_settings.app_version,
        description=(
            "Analyze a public website with an LLM and return structured competitor, "
            "search-query, SEO, service, and target-audience intelligence."
        ),
        docs_url="/docs" if app_settings.docs_enabled else None,
        redoc_url="/redoc" if app_settings.docs_enabled else None,
        lifespan=lifespan,
    )

    if app_settings.cors_origins_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=app_settings.cors_origins_list,
            allow_credentials=False,
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type", "X-Request-ID"],
        )

    limiter = (
        SlidingWindowRateLimiter(
            limit=app_settings.rate_limit_requests,
            window_seconds=app_settings.rate_limit_window_seconds,
        )
        if app_settings.rate_limit_enabled
        else None
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
        supplied = request.headers.get("X-Request-ID", "")
        request_id = supplied if _REQUEST_ID_PATTERN.fullmatch(supplied) else uuid.uuid4().hex
        request.state.request_id = request_id

        def finish(response):  # type: ignore[no-untyped-def]
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Referrer-Policy"] = "no-referrer"
            return response

        if limiter is not None and request.method == "POST" and request.url.path == "/link-analysis":
            client_host = request.client.host if request.client else "unknown"
            allowed, retry_after = limiter.check(client_host)
            if not allowed:
                logger.info(
                    "Rate limit exceeded request_id=%s client=%s retry_after=%s",
                    request_id,
                    client_host,
                    retry_after,
                )
                body = ErrorResponse(
                    error=ErrorDetail(
                        code="RATE_LIMITED",
                        message="Too many analysis requests. Please retry shortly.",
                        request_id=request_id,
                    )
                )
                return finish(
                    JSONResponse(
                        status_code=429,
                        content=body.model_dump(mode="json"),
                        headers={"Retry-After": str(retry_after)},
                    )
                )

        return finish(await call_next(request))

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        if exc.status_code >= 500:
            logger.error(
                "Application error request_id=%s code=%s message=%s",
                request_id,
                exc.code,
                exc.message,
            )
        else:
            logger.info(
                "Request rejected request_id=%s code=%s message=%s",
                request_id,
                exc.code,
                exc.message,
            )
        body = ErrorResponse(
            error=ErrorDetail(
                code=exc.code,
                message=exc.message,
                request_id=request_id,
                details=exc.details,
            )
        )
        return JSONResponse(status_code=exc.status_code, content=body.model_dump(mode="json"))

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        errors = exc.errors()
        url_error = next(
            (error for error in errors if error.get("loc") and error["loc"][-1] == "url"),
            None,
        )
        if url_error:
            cause = url_error.get("ctx", {}).get("error")
            if isinstance(cause, AppError):
                message = str(url_error.get("msg", cause.message)).replace("Value error, ", "")
                body = ErrorResponse(
                    error=ErrorDetail(
                        code=cause.code,
                        message=message or cause.message,
                        request_id=request_id,
                    )
                )
                return JSONResponse(
                    status_code=cause.status_code,
                    content=body.model_dump(mode="json"),
                )

        body = ErrorResponse(
            error=ErrorDetail(
                code="VALIDATION_ERROR",
                message="The request body is invalid.",
                request_id=request_id,
                details={
                    "errors": [
                        {
                            "loc": [str(part) for part in error.get("loc", [])],
                            "msg": str(error.get("msg", "Invalid value")),
                            "type": str(error.get("type", "value_error")),
                        }
                        for error in errors[:10]
                    ]
                },
            )
        )
        return JSONResponse(status_code=422, content=body.model_dump(mode="json"))

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        logger.exception("Unhandled error request_id=%s", request_id)
        body = ErrorResponse(
            error=ErrorDetail(
                code="INTERNAL_ERROR",
                message="An unexpected internal error occurred.",
                request_id=request_id,
            )
        )
        return JSONResponse(status_code=500, content=body.model_dump(mode="json"))

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {
            "service": app_settings.app_name,
            "version": app_settings.app_version,
            "docs": "/docs",
            "endpoint": "POST /link-analysis",
        }

    @app.get(
        "/health",
        response_model=HealthResponse,
        tags=["Operations"],
        summary="Health and configuration status",
    )
    async def health(request: Request) -> HealthResponse:
        service: AnalysisService = request.app.state.analysis_service
        stats = await service.cache_stats()
        return HealthResponse(
            status="degraded" if app_settings.is_demo_mode else "ok",
            version=app_settings.app_version,
            environment=app_settings.environment,
            llm_provider=app_settings.resolved_llm_provider,
            llm_model=(
                app_settings.gemini_model
                if app_settings.resolved_llm_provider == "gemini"
                else app_settings.openai_model
                if app_settings.resolved_llm_provider == "openai"
                else "deterministic-heuristic-v1"
            ),
            demo_mode=app_settings.is_demo_mode,
            cache_entries=stats.entries,
        )

    @app.post(
        "/link-analysis",
        response_model=AnalysisResponse,
        tags=["Analysis"],
        summary="Analyze a public website",
        description=(
            "Fetches a public website, extracts meaningful text, and returns five "
            "structured business-analysis sections. Private network addresses and "
            "non-HTML responses are rejected."
        ),
        responses={
            400: {"model": ErrorResponse, "description": "Invalid or blocked URL"},
            413: {"model": ErrorResponse, "description": "Website response too large"},
            422: {"model": ErrorResponse, "description": "Inaccessible or unextractable website"},
            502: {"model": ErrorResponse, "description": "LLM or upstream failure"},
            504: {"model": ErrorResponse, "description": "Website or LLM timeout"},
        },
    )
    async def link_analysis(
        payload: LinkAnalysisRequest,
        request: Request,
    ) -> AnalysisResponse:
        service: AnalysisService = request.app.state.analysis_service
        return await service.analyze(
            payload.url,
            request_id=request.state.request_id,
        )

    return app


app = create_app()
