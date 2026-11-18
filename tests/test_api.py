from __future__ import annotations

from fastapi.testclient import TestClient

from app.cache import TTLCache
from app.config import Settings
from app.main import create_app
from app.services.analysis_service import AnalysisArtifact, AnalysisService
from app.services.website_extractor import ExtractedWebsite


class FakeExtractor:
    def __init__(self, site: ExtractedWebsite) -> None:
        self.site = site
        self.calls = 0

    async def extract(self, url: str) -> ExtractedWebsite:
        self.calls += 1
        return self.site.model_copy(update={"requested_url": url, "final_url": url})


def build_client(
    settings: Settings,
    site: ExtractedWebsite,
) -> tuple[TestClient, FakeExtractor]:
    extractor = FakeExtractor(site)
    service = AnalysisService(
        settings=settings,
        extractor=extractor,  # type: ignore[arg-type]
        cache=TTLCache[AnalysisArtifact](ttl_seconds=60, max_entries=8),
        llm_service=None,
    )
    app = create_app(settings=settings, analysis_service=service)
    return TestClient(app), extractor


def test_link_analysis_returns_all_five_sections(
    settings: Settings,
    sample_extraction: ExtractedWebsite,
) -> None:
    client, extractor = build_client(settings, sample_extraction)

    with client:
        response = client.post(
            "/link-analysis",
            json={"url": "https://acme.example"},
            headers={"X-Request-ID": "test-request-123"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["website"] == "https://acme.example/"
    assert body["brand"]["name"] == "Acme Cloud"
    assert len(body["analysis"]["competitor_analysis"]["competitive_advantages"]) >= 1
    assert len(body["analysis"]["target_search_queries"]) == 5
    assert len(body["analysis"]["top_services"]) == 5
    assert body["analysis"]["seo_improvements"]["primary_keyword"]
    assert body["analysis"]["target_audience"]["primary_audience"]
    assert body["meta"]["llm"]["mode"] == "demo"
    assert body["meta"]["cached"] is False
    assert body["meta"]["extraction"]["word_count"] > 0
    assert response.headers["X-Request-ID"] == "test-request-123"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert extractor.calls == 1


def test_second_identical_request_is_served_from_cache(
    settings: Settings,
    sample_extraction: ExtractedWebsite,
) -> None:
    client, extractor = build_client(settings, sample_extraction)

    with client:
        first = client.post("/link-analysis", json={"url": "https://acme.example"})
        second = client.post("/link-analysis", json={"url": "https://acme.example/"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["meta"]["cached"] is False
    assert second.json()["meta"]["cached"] is True
    assert extractor.calls == 1


def test_health_endpoint_reports_demo_degradation(
    settings: Settings,
    sample_extraction: ExtractedWebsite,
) -> None:
    client, _ = build_client(settings, sample_extraction)
    with client:
        response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["demo_mode"] is True


def test_private_url_is_rejected(
    settings: Settings,
    sample_extraction: ExtractedWebsite,
) -> None:
    client, _ = build_client(settings, sample_extraction)
    with client:
        response = client.post("/link-analysis", json={"url": "http://127.0.0.1/"})
    assert response.status_code == 400
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "BLOCKED_URL"
    assert body["error"]["request_id"]


def test_invalid_url_syntax_is_rejected(
    settings: Settings,
    sample_extraction: ExtractedWebsite,
) -> None:
    client, _ = build_client(settings, sample_extraction)
    with client:
        response = client.post("/link-analysis", json={"url": "ftp://example.com/file"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_URL"


def test_missing_url_returns_structured_validation_error(
    settings: Settings,
    sample_extraction: ExtractedWebsite,
) -> None:
    client, _ = build_client(settings, sample_extraction)
    with client:
        response = client.post("/link-analysis", json={})
    assert response.status_code == 422
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["details"]["errors"]
