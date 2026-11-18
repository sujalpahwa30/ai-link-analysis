from __future__ import annotations

import httpx
import pytest

from app.config import Settings
from app.exceptions import WebsiteBlockedError, WebsiteFetchError, WebsiteTooLargeError
from app.services import website_extractor
from app.services.website_extractor import WebsiteExtractor

HTML = b"""<!doctype html>
<html lang="en">
  <head>
    <title>Acme Cloud | Custom Software</title>
    <meta name="description" content="Acme Cloud builds software and cloud platforms.">
    <meta property="og:site_name" content="Acme Cloud">
    <script type="application/ld+json">
      {"@context":"https://schema.org","@type":"Organization","name":"Acme Cloud"}
    </script>
    <style>.hidden { display: none; }</style>
  </head>
  <body>
    <nav><a href="/about">About</a><a href="/services/cloud">Cloud Migration</a></nav>
    <main>
      <h1>Acme Cloud</h1>
      <h2>Custom Software Development</h2>
      <p>We design and build reliable software for growing companies.</p>
      <h2>Cloud Migration</h2>
      <p>We move critical workloads to secure cloud infrastructure.</p>
      <script>alert('do not include this')</script>
    </main>
    <footer>Cookie banner and legal footer text.</footer>
  </body>
</html>
"""


@pytest.fixture
def extractor(settings: Settings) -> WebsiteExtractor:
    return WebsiteExtractor(settings)


@pytest.mark.asyncio
async def test_extracts_clean_content_and_metadata(
    extractor: WebsiteExtractor,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def allow(_: str) -> None:
        return None

    monkeypatch.setattr(website_extractor, "ensure_public_url", allow)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=HTML,
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        site = await WebsiteExtractor(
            extractor.settings,
            client=client,
        ).extract("https://acme.example")

    assert site.title == "Acme Cloud | Custom Software"
    assert site.site_name == "Acme Cloud"
    assert site.meta_description.startswith("Acme Cloud builds")
    assert "Custom Software Development" in site.headings
    assert "do not include this" not in site.text
    assert "Cookie banner" not in site.text
    assert site.internal_links[0].text == "Cloud Migration"
    assert "Organization" in site.structured_data_summary
    assert site.truncated is False


@pytest.mark.asyncio
async def test_redirect_to_private_address_is_blocked(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={"location": "http://127.0.0.1/private"},
            request=request,
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
    ) as client:
        with pytest.raises(Exception) as error:
            await WebsiteExtractor(settings, client=client).extract("https://8.8.8.8/")
    assert error.value.__class__.__name__ == "BlockedUrlError"


@pytest.mark.asyncio
async def test_redirect_limit_is_enforced(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def allow(_: str) -> None:
        return None

    monkeypatch.setattr(website_extractor, "ensure_public_url", allow)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "/again"}, request=request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
    ) as client:
        with pytest.raises(WebsiteFetchError, match="redirect limit"):
            await WebsiteExtractor(settings, client=client).extract("https://8.8.8.8/")


@pytest.mark.asyncio
async def test_declared_oversized_response_is_rejected(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def allow(_: str) -> None:
        return None

    monkeypatch.setattr(website_extractor, "ensure_public_url", allow)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html", "content-length": "999999999"},
            content=b"small",
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(WebsiteTooLargeError):
            await WebsiteExtractor(settings, client=client).extract("https://8.8.8.8/")


@pytest.mark.asyncio
async def test_blocked_status_is_mapped(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def allow(_: str) -> None:
        return None

    monkeypatch.setattr(website_extractor, "ensure_public_url", allow)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, content=b"forbidden", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(WebsiteBlockedError):
            await WebsiteExtractor(settings, client=client).extract("https://8.8.8.8/")


@pytest.mark.asyncio
async def test_non_html_response_is_rejected(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def allow(_: str) -> None:
        return None

    monkeypatch.setattr(website_extractor, "ensure_public_url", allow)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/pdf"},
            content=b"%PDF-1.7",
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(WebsiteFetchError, match="Unsupported content type"):
            await WebsiteExtractor(settings, client=client).extract("https://8.8.8.8/")
