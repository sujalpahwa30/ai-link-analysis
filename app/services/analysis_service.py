"""Analysis orchestration, competitor hygiene, and response assembly."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from app.cache import CacheStats, TTLCache
from app.config import Settings
from app.exceptions import LLMConfigurationError
from app.schemas import (
    AnalysisResponse,
    Brand,
    ExtractionMeta,
    LLMAnalysis,
    LLMRunMeta,
    LLMUsage,
    ResponseMeta,
)
from app.services.competitor_service import sanitise_competitors
from app.services.demo_analysis import DemoAnalyzer
from app.services.llm_service import LLMResult, LLMService
from app.services.prompts import PROMPT_VERSION
from app.services.website_extractor import ExtractedWebsite, WebsiteExtractor
from app.utils.validators import validate_url_syntax


@dataclass(slots=True)
class AnalysisArtifact:
    website: str
    brand: Brand
    analysis: LLMAnalysis
    extraction: ExtractedWebsite
    provider: str
    model: str
    mode: Literal["llm", "demo"]
    fallback_used: bool
    usage: LLMUsage
    warnings: list[str]
    analyzed_at: datetime


class AnalysisService:
    def __init__(
        self,
        *,
        settings: Settings,
        extractor: WebsiteExtractor,
        cache: TTLCache[AnalysisArtifact],
        llm_service: LLMService | None,
        demo_analyzer: DemoAnalyzer | None = None,
    ) -> None:
        self.settings = settings
        self.extractor = extractor
        self.cache = cache
        self.llm_service = llm_service
        self.demo_analyzer = demo_analyzer or DemoAnalyzer()
        if not settings.is_demo_mode and llm_service is None:
            raise LLMConfigurationError()

    async def analyze(self, url: str, *, request_id: str) -> AnalysisResponse:
        started = time.perf_counter()
        normalized_url = validate_url_syntax(url)
        provider = "demo" if self.settings.is_demo_mode else self.settings.resolved_llm_provider
        model = (
            self.demo_analyzer.model
            if provider == "demo"
            else (
                self.settings.gemini_model
                if provider == "gemini"
                else self.settings.openai_model
            )
        )
        cache_key = self._cache_key(normalized_url, provider, model)
        cached = await self.cache.get(cache_key)

        if cached is not None:
            artifact = cached
            is_cached = True
        else:
            extraction = await self.extractor.extract(normalized_url)
            result = await self._run_analysis(extraction)
            artifact = self._to_artifact(normalized_url, extraction, result)
            await self.cache.set(cache_key, artifact)
            is_cached = False

        duration_ms = max(0, round((time.perf_counter() - started) * 1000))
        return self._to_response(artifact, request_id=request_id, duration_ms=duration_ms, cached=is_cached)

    async def _run_analysis(self, extraction: ExtractedWebsite) -> LLMResult:
        if self.settings.is_demo_mode:
            return self.demo_analyzer.analyze(extraction)
        if self.llm_service is None:  # defensive; validated in __init__
            raise LLMConfigurationError()
        return await self.llm_service.analyze(extraction)

    def _to_artifact(
        self,
        website: str,
        extraction: ExtractedWebsite,
        result: LLMResult,
    ) -> AnalysisArtifact:
        competitors, competitor_warnings = sanitise_competitors(
            result.output.brand.name,
            result.output.analysis.competitor_analysis.competitors,
        )
        competitor_analysis = result.output.analysis.competitor_analysis.model_copy(
            update={"competitors": competitors}
        )
        notes = list(result.output.analysis.data_quality_notes)
        notes.extend(competitor_warnings)
        notes = list(dict.fromkeys(notes))[:10]
        analysis = result.output.analysis.model_copy(
            update={
                "competitor_analysis": competitor_analysis,
                "data_quality_notes": notes,
            }
        )
        warnings = list(
            dict.fromkeys(
                [
                    *extraction.warnings,
                    *result.warnings,
                    *competitor_warnings,
                ]
            )
        )
        return AnalysisArtifact(
            website=website,
            brand=result.output.brand,
            analysis=analysis,
            extraction=extraction,
            provider=result.provider,
            model=result.model,
            mode="demo" if self.settings.is_demo_mode else "llm",
            fallback_used=result.fallback_used,
            usage=result.usage,
            warnings=warnings,
            analyzed_at=datetime.now(UTC),
        )

    @staticmethod
    def _to_response(
        artifact: AnalysisArtifact,
        *,
        request_id: str,
        duration_ms: int,
        cached: bool,
    ) -> AnalysisResponse:
        extraction = artifact.extraction
        return AnalysisResponse(
            success=True,
            website=artifact.website,
            brand=artifact.brand,
            analysis=artifact.analysis,
            meta=ResponseMeta(
                request_id=request_id,
                analyzed_at=artifact.analyzed_at,
                duration_ms=duration_ms,
                cached=cached,
                llm=LLMRunMeta(
                    provider=artifact.provider,
                    model=artifact.model,
                    mode=artifact.mode,
                    fallback_used=artifact.fallback_used,
                    usage=artifact.usage,
                ),
                extraction=ExtractionMeta(
                    requested_url=extraction.requested_url,
                    final_url=extraction.final_url,
                    title=extraction.title,
                    content_chars=len(extraction.text),
                    word_count=extraction.word_count,
                    truncated=extraction.truncated,
                    fetched_at=extraction.fetched_at,
                    content_hash=extraction.content_hash,
                ),
                warnings=artifact.warnings,
            ),
        )

    def _cache_key(self, url: str, provider: str, model: str) -> str:
        raw = (
            f"{url}|{provider}|{model}|{self.settings.max_content_chars}|"
            f"{PROMPT_VERSION}|{self.settings.llm_provider}"
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    async def cache_stats(self) -> CacheStats:
        return await self.cache.stats()
