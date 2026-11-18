"""Pydantic request, LLM-output, and API response contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.utils.validators import validate_url_syntax

Platform = Literal["Google", "Facebook", "Instagram", "LinkedIn", "X (Twitter)"]
ServiceSource = Literal["website", "inferred"]
CompetitorBasis = Literal["ai_suggested", "public_information"]
ContentType = Literal[
    "service_landing_page",
    "industry_page",
    "faq",
    "blog_post",
    "location_page",
    "article",
    "case_study",
    "other",
]


class APIModel(BaseModel):
    """Shared strict model configuration."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LinkAnalysisRequest(APIModel):
    url: str = Field(
        ...,
        min_length=1,
        max_length=2048,
        description="Public website URL. A missing scheme is treated as HTTPS.",
        examples=["https://example.com"],
    )

    @field_validator("url")
    @classmethod
    def normalise_url(cls, value: str) -> str:
        return validate_url_syntax(value)


class Brand(APIModel):
    name: str = Field(..., min_length=1, max_length=200)
    category: str = Field(..., min_length=1, max_length=200)
    summary: str = Field(..., min_length=1, max_length=1500)


class Competitor(APIModel):
    name: str = Field(..., min_length=1, max_length=200)
    website: str = Field(default="", max_length=500)
    main_service: str = Field(..., min_length=1, max_length=300)
    target_market: str = Field(..., min_length=1, max_length=300)
    strengths: list[str] = Field(..., min_length=1, max_length=6)
    weaknesses: list[str] = Field(..., min_length=1, max_length=6)
    basis: CompetitorBasis


class CompetitorAnalysis(APIModel):
    brand_overview: str = Field(..., min_length=1, max_length=2000)
    main_category: str = Field(..., min_length=1, max_length=200)
    products_services: list[str] = Field(..., min_length=1, max_length=15)
    competitors: list[Competitor] = Field(default_factory=list, max_length=6)
    competitive_advantages: list[str] = Field(..., min_length=1, max_length=10)
    opportunities: list[str] = Field(..., min_length=1, max_length=10)
    comparison_summary: str = Field(..., min_length=1, max_length=2000)


class SearchQuery(APIModel):
    query: str = Field(..., min_length=2, max_length=250)
    platforms: list[Platform] = Field(..., min_length=1, max_length=5)
    intent: str = Field(..., min_length=1, max_length=200)
    business_relevance: str = Field(..., min_length=1, max_length=600)
    target_audience: str = Field(..., min_length=1, max_length=300)

    @field_validator("platforms")
    @classmethod
    def unique_platforms(cls, value: list[Platform]) -> list[Platform]:
        if len(value) != len(set(value)):
            raise ValueError("platforms must not contain duplicates")
        return value


class SupportingContent(APIModel):
    title: str = Field(..., min_length=1, max_length=250)
    content_type: ContentType
    why_it_helps: str = Field(..., min_length=1, max_length=600)


class SEOImprovements(APIModel):
    primary_keyword: str = Field(..., min_length=1, max_length=150)
    secondary_keywords: list[str] = Field(..., min_length=2, max_length=12)
    long_tail_keywords: list[str] = Field(..., min_length=2, max_length=12)
    business_keywords: list[str] = Field(..., min_length=2, max_length=12)
    recommended_title: str = Field(..., min_length=5, max_length=120)
    title_rationale: str = Field(..., min_length=1, max_length=500)
    meta_description: str = Field(..., min_length=20, max_length=220)
    meta_description_rationale: str = Field(..., min_length=1, max_length=500)
    supporting_content: list[SupportingContent] = Field(..., min_length=1, max_length=10)


class ServiceItem(APIModel):
    service_name: str = Field(..., min_length=1, max_length=200)
    description: str = Field(..., min_length=1, max_length=800)
    target_industry: str = Field(..., min_length=1, max_length=250)
    target_customer: str = Field(..., min_length=1, max_length=250)
    main_benefit: str = Field(..., min_length=1, max_length=500)
    source: ServiceSource
    evidence: str = Field(default="", max_length=500)


class AudienceSegment(APIModel):
    segment: str = Field(..., min_length=1, max_length=200)
    description: str = Field(..., min_length=1, max_length=800)
    business_type: str = Field(..., min_length=1, max_length=200)
    needs: list[str] = Field(..., min_length=1, max_length=8)
    marketing_angle: str = Field(..., min_length=1, max_length=600)


class TargetAudience(APIModel):
    primary_audience: list[AudienceSegment] = Field(..., min_length=1, max_length=5)
    secondary_audience: list[AudienceSegment] = Field(default_factory=list, max_length=5)
    industries: list[str] = Field(..., min_length=1, max_length=10)
    business_types: list[str] = Field(..., min_length=1, max_length=10)
    customer_profiles: list[str] = Field(..., min_length=1, max_length=10)
    location_focus: str = Field(default="", max_length=500)
    customer_needs: list[str] = Field(..., min_length=1, max_length=10)
    customer_interests: list[str] = Field(..., min_length=1, max_length=10)
    recommended_segments: list[str] = Field(..., min_length=1, max_length=10)


class LLMAnalysis(APIModel):
    competitor_analysis: CompetitorAnalysis
    target_search_queries: list[SearchQuery] = Field(..., min_length=5, max_length=5)
    seo_improvements: SEOImprovements
    top_services: list[ServiceItem] = Field(..., min_length=5, max_length=5)
    target_audience: TargetAudience
    data_quality_notes: list[str] = Field(default_factory=list, max_length=10)


class LLMOutput(APIModel):
    """The exact document requested from the language model."""

    brand: Brand
    analysis: LLMAnalysis


class LLMUsage(APIModel):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    estimated_cost_usd: float | None = Field(default=None, ge=0)


class LLMRunMeta(APIModel):
    provider: str
    model: str
    mode: Literal["llm", "demo"]
    fallback_used: bool = False
    usage: LLMUsage = Field(default_factory=LLMUsage)


class ExtractionMeta(APIModel):
    requested_url: str
    final_url: str
    title: str
    content_chars: int = Field(..., ge=0)
    word_count: int = Field(..., ge=0)
    truncated: bool
    fetched_at: datetime
    content_hash: str


class ResponseMeta(APIModel):
    request_id: str
    analyzed_at: datetime
    duration_ms: int = Field(..., ge=0)
    cached: bool = False
    llm: LLMRunMeta
    extraction: ExtractionMeta
    warnings: list[str] = Field(default_factory=list)


class AnalysisResponse(APIModel):
    success: Literal[True] = True
    website: str
    brand: Brand
    analysis: LLMAnalysis
    meta: ResponseMeta


class ErrorDetail(APIModel):
    code: str
    message: str
    request_id: str | None = None
    details: dict[str, object] | None = None


class ErrorResponse(APIModel):
    success: Literal[False] = False
    error: ErrorDetail


class HealthResponse(APIModel):
    status: Literal["ok", "degraded"]
    version: str
    environment: str
    llm_provider: str
    llm_model: str
    demo_mode: bool
    cache_entries: int
