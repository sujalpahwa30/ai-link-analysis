from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.config import Settings
from app.schemas import Competitor, LLMOutput
from app.services.demo_analysis import DemoAnalyzer
from app.services.website_extractor import ExtractedWebsite, InternalLink


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        environment="test",
        llm_provider="demo",
        cache_ttl_seconds=60,
        cache_max_entries=16,
        max_content_chars=8000,
        max_page_bytes=500_000,
    )


@pytest.fixture
def sample_extraction() -> ExtractedWebsite:
    text = (
        "Acme Cloud is a software consultancy for growing companies. "
        "We design and build custom software, migrate workloads to the cloud, "
        "connect business systems through APIs, and provide ongoing DevOps support. "
        "Our clients are technology, finance, and logistics teams that need reliable delivery. "
        "We work across discovery, implementation, and support with measurable outcomes. "
    ) * 4
    return ExtractedWebsite(
        requested_url="https://acme.example/",
        final_url="https://acme.example/",
        title="Acme Cloud | Custom Software and Cloud Consulting",
        meta_description=(
            "Acme Cloud helps growing companies design, build, and operate reliable "
            "software and cloud platforms."
        ),
        site_name="Acme Cloud",
        language="en",
        headings=[
            "Custom Software Development",
            "Cloud Migration",
            "API Integration",
            "Data Engineering",
            "DevOps Support",
        ],
        internal_links=[
            InternalLink(
                text="Custom Software Development",
                url="https://acme.example/services/software",
            ),
            InternalLink(
                text="Cloud Migration",
                url="https://acme.example/services/cloud",
            ),
        ],
        structured_data_summary='[{"@type":"Organization","name":"Acme Cloud"}]',
        text=text,
        word_count=len(text.split()),
        content_hash="a" * 64,
        truncated=False,
        fetched_at=datetime.now(UTC),
        warnings=[],
    )


@pytest.fixture
def valid_llm_output(sample_extraction: ExtractedWebsite) -> LLMOutput:
    output = DemoAnalyzer().analyze(sample_extraction).output
    competitors = [
        Competitor(
            name="Northstar Systems",
            website="https://northstar.example",
            main_service="Cloud consulting",
            target_market="Mid-market enterprises",
            strengths=["Established enterprise relationships"],
            weaknesses=["Less focused on early-stage product teams"],
            basis="ai_suggested",
        ),
        Competitor(
            name="BluePeak Labs",
            website="https://bluepeak.example",
            main_service="Custom software delivery",
            target_market="Funded startups",
            strengths=["Strong product-design capability"],
            weaknesses=["Smaller cloud-operations practice"],
            basis="ai_suggested",
        ),
        Competitor(
            name="Orbit Engineering",
            website="https://orbit.example",
            main_service="Data and platform engineering",
            target_market="Financial services",
            strengths=["Deep data engineering expertise"],
            weaknesses=["Limited end-user application design"],
            basis="ai_suggested",
        ),
    ]
    analysis = output.analysis.model_copy(
        update={
            "competitor_analysis": output.analysis.competitor_analysis.model_copy(
                update={"competitors": competitors}
            )
        }
    )
    return output.model_copy(update={"analysis": analysis})
