"""SSRF-safe website fetching and content extraction."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup, FeatureNotFound
from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings
from app.exceptions import (
    BlockedUrlError,
    ContentExtractionError,
    InvalidUrlError,
    WebsiteBlockedError,
    WebsiteFetchError,
    WebsiteTimeoutError,
    WebsiteTooLargeError,
)
from app.utils.validators import ensure_public_url, validate_url_syntax

_WHITESPACE = re.compile(r"\s+")
_MULTI_NEWLINE = re.compile(r"\n{3,}")
_BOT_CHALLENGE_MARKERS = (
    "just a moment",
    "attention required",
    "verify you are human",
    "enable javascript and cookies",
    "cf-chl-",
    "captcha-delivery",
)
_GENERIC_ANCHOR_TEXT = {
    "home",
    "about",
    "about us",
    "contact",
    "contact us",
    "blog",
    "news",
    "careers",
    "login",
    "sign in",
    "sign up",
    "register",
    "privacy",
    "privacy policy",
    "terms",
    "terms of service",
    "cookie policy",
    "menu",
    "search",
    "skip to content",
    "read more",
    "learn more",
}


class InternalLink(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    url: str


class ExtractedWebsite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested_url: str
    final_url: str
    title: str
    meta_description: str
    site_name: str
    language: str
    headings: list[str] = Field(default_factory=list)
    internal_links: list[InternalLink] = Field(default_factory=list)
    structured_data_summary: str = ""
    text: str
    word_count: int
    content_hash: str
    truncated: bool
    fetched_at: datetime
    warnings: list[str] = Field(default_factory=list)


class WebsiteExtractor:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self._client = client

    async def extract(self, url: str) -> ExtractedWebsite:
        requested_url = validate_url_syntax(url)
        await ensure_public_url(requested_url)

        if self._client is not None:
            return await self._extract_with_client(self._client, requested_url)

        timeout = httpx.Timeout(
            self.settings.fetch_timeout_seconds,
            connect=self.settings.fetch_connect_timeout_seconds,
        )
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
        ) as client:
            return await self._extract_with_client(client, requested_url)

    async def _extract_with_client(
        self,
        client: httpx.AsyncClient,
        requested_url: str,
    ) -> ExtractedWebsite:
        html, final_url = await self._fetch_html(client, requested_url)
        return self._parse_html(html, requested_url, final_url)

    async def _fetch_html(
        self,
        client: httpx.AsyncClient,
        requested_url: str,
    ) -> tuple[bytes, str]:
        current_url = requested_url
        timeout = httpx.Timeout(
            self.settings.fetch_timeout_seconds,
            connect=self.settings.fetch_connect_timeout_seconds,
        )

        for redirect_number in range(self.settings.max_redirects + 1):
            await ensure_public_url(current_url)

            try:
                request = client.build_request(
                    "GET",
                    current_url,
                    headers={
                        "User-Agent": self.settings.user_agent,
                        "Accept": "text/html,application/xhtml+xml,text/plain;q=0.8,*/*;q=0.1",
                        "Accept-Language": "en-US,en;q=0.8",
                    },
                    timeout=timeout,
                )
                response = await client.send(request, stream=True, follow_redirects=False)
            except httpx.TimeoutException as exc:
                raise WebsiteTimeoutError() from exc
            except httpx.HTTPError as exc:
                raise WebsiteFetchError("The website could not be reached.") from exc

            try:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise WebsiteFetchError("The website returned an invalid redirect.")
                    if redirect_number >= self.settings.max_redirects:
                        raise WebsiteFetchError("The website exceeded the redirect limit.")
                    current_url = validate_url_syntax(urljoin(current_url, location))
                    continue

                if response.status_code >= 400:
                    if response.status_code in {401, 403, 429}:
                        raise WebsiteBlockedError(
                            f"The website returned HTTP {response.status_code} and may block automated access."
                        )
                    raise WebsiteFetchError(f"The website returned HTTP {response.status_code}.")

                if response.status_code < 200 or response.status_code >= 300:
                    raise WebsiteFetchError(
                        f"The website returned an unsupported HTTP status ({response.status_code})."
                    )

                content_type = response.headers.get("content-type", "").split(";", 1)[0].strip()
                allowed_types = {"text/html", "application/xhtml+xml", "text/plain", ""}
                if content_type.lower() not in allowed_types:
                    safe_type = re.sub(r"[^a-zA-Z0-9/+.-]", "", content_type)[:80]
                    raise WebsiteFetchError(
                        f"Unsupported content type '{safe_type or 'unknown'}'; expected HTML."
                    )

                declared_length = response.headers.get("content-length")
                if declared_length:
                    try:
                        if int(declared_length) > self.settings.max_page_bytes:
                            raise WebsiteTooLargeError()
                    except ValueError:
                        pass

                body = bytearray()
                try:
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > self.settings.max_page_bytes:
                            raise WebsiteTooLargeError()
                except httpx.DecodingError as exc:
                    raise WebsiteFetchError("The website response could not be decoded.") from exc

                if not body:
                    raise ContentExtractionError("The website returned an empty response.")

                return bytes(body), current_url
            finally:
                await response.aclose()

        raise WebsiteFetchError("The website exceeded the redirect limit.")

    def _parse_html(
        self,
        html: bytes,
        requested_url: str,
        final_url: str,
    ) -> ExtractedWebsite:
        try:
            soup = BeautifulSoup(html, "lxml")
        except FeatureNotFound:  # pragma: no cover - lxml is a declared dependency
            soup = BeautifulSoup(html, "html.parser")

        title = self._first_text(
            soup,
            [
                ("meta", {"property": "og:title"}, "content"),
                ("meta", {"name": "twitter:title"}, "content"),
                ("title", {}, None),
            ],
        )
        site_name = self._first_text(
            soup,
            [("meta", {"property": "og:site_name"}, "content")],
        )
        meta_description = self._first_text(
            soup,
            [
                ("meta", {"name": "description"}, "content"),
                ("meta", {"property": "og:description"}, "content"),
                ("meta", {"name": "twitter:description"}, "content"),
            ],
        )
        language = ""
        if soup.html and soup.html.get("lang"):
            language = str(soup.html.get("lang"))[:30]

        structured_data = self._extract_json_ld(soup)
        headings = self._extract_headings(soup)
        internal_links = self._extract_internal_links(soup, final_url)

        for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
            tag.decompose()
        for tag in soup(
            [
                "script",
                "style",
                "noscript",
                "svg",
                "iframe",
                "canvas",
                "form",
                "input",
                "button",
                "select",
                "textarea",
                "header",
                "footer",
                "nav",
                "aside",
            ]
        ):
            tag.decompose()

        root = (
            soup.find("main")
            or soup.find(attrs={"role": "main"})
            or soup.find("article")
            or soup.body
            or soup
        )
        text = self._clean_text(root.get_text("\n"))
        if len(text) < 120:
            fallback_parts = [meta_description, *headings, *(link.text for link in internal_links)]
            fallback = self._clean_text("\n".join(fallback_parts))
            if len(fallback) > len(text):
                text = fallback

        word_count = len(text.split())
        if len(text) < 80:
            raise ContentExtractionError(
                "No meaningful text content was found. The site may require JavaScript or block crawlers."
            )

        challenge_sample = f"{title}\n{text[:1500]}".lower()
        if len(text) < 500 and any(marker in challenge_sample for marker in _BOT_CHALLENGE_MARKERS):
            raise WebsiteBlockedError("The website presented a bot-protection challenge.")

        truncated_text, truncated = self._truncate(text, self.settings.max_content_chars)
        warnings: list[str] = []
        if truncated:
            warnings.append(
                "Website text was truncated to the configured character budget before LLM analysis."
            )
        if word_count < 80:
            warnings.append("The page contains very little readable text; insights may be limited.")
        if not meta_description:
            warnings.append("The page has no standard meta description.")
        if not title:
            warnings.append("The page has no HTML title.")

        content_hash = hashlib.sha256(
            f"{title}\n{meta_description}\n{truncated_text}".encode()
        ).hexdigest()

        return ExtractedWebsite(
            requested_url=requested_url,
            final_url=final_url,
            title=title,
            meta_description=meta_description,
            site_name=site_name,
            language=language,
            headings=headings,
            internal_links=internal_links,
            structured_data_summary=structured_data,
            text=truncated_text,
            word_count=word_count,
            content_hash=content_hash,
            truncated=truncated,
            fetched_at=datetime.now(UTC),
            warnings=warnings,
        )

    @staticmethod
    def _first_text(
        soup: BeautifulSoup,
        candidates: list[tuple[str, dict[str, str], str | None]],
    ) -> str:
        for tag_name, attrs, attribute in candidates:
            tag = soup.find(tag_name, attrs=attrs)
            if tag is None:
                continue
            value = tag.get(attribute) if attribute else tag.get_text(" ", strip=True)
            if value and str(value).strip():
                return WebsiteExtractor._normalise_inline(str(value))[:2000]
        return ""

    @staticmethod
    def _normalise_inline(value: str) -> str:
        return _WHITESPACE.sub(" ", value.replace("\xa0", " ")).strip()

    @staticmethod
    def _extract_headings(soup: BeautifulSoup) -> list[str]:
        headings: list[str] = []
        seen: set[str] = set()
        for tag in soup.find_all(["h1", "h2", "h3"]):
            text = WebsiteExtractor._normalise_inline(tag.get_text(" ", strip=True))
            key = text.casefold()
            if 2 <= len(text) <= 180 and key not in seen:
                seen.add(key)
                headings.append(text)
            if len(headings) >= 50:
                break
        return headings

    @staticmethod
    def _extract_internal_links(soup: BeautifulSoup, final_url: str) -> list[InternalLink]:
        base_host = (urlsplit(final_url).hostname or "").lower()
        links: list[InternalLink] = []
        seen: set[tuple[str, str]] = set()

        for anchor in soup.find_all("a", href=True):
            text = WebsiteExtractor._normalise_inline(anchor.get_text(" ", strip=True))
            if not text or len(text) > 140 or text.casefold() in _GENERIC_ANCHOR_TEXT:
                continue
            href = str(anchor.get("href", "")).strip()
            if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue
            absolute = urljoin(final_url, href)
            parsed = urlsplit(absolute)
            host = (parsed.hostname or "").lower()
            if parsed.scheme not in {"http", "https"}:
                continue
            if host != base_host and not host.endswith(f".{base_host}"):
                continue
            clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
            key = (text.casefold(), clean_url)
            if key in seen:
                continue
            seen.add(key)
            links.append(InternalLink(text=text, url=clean_url))
            if len(links) >= 200:
                break
        return links

    @staticmethod
    def _extract_json_ld(soup: BeautifulSoup) -> str:
        documents: list[object] = []
        for script in soup.find_all("script"):
            if "ld+json" not in str(script.get("type", "")).lower():
                continue
            raw = script.string or script.get_text()
            if not raw or len(raw) > 50_000:
                continue
            try:
                parsed = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(parsed, list):
                documents.extend(parsed[:5])
            else:
                documents.append(parsed)
            if len(documents) >= 6:
                break

        if not documents:
            return ""

        serialised = json.dumps(documents, ensure_ascii=False, separators=(",", ":"))
        if len(serialised) > 5000:
            serialised = f"{serialised[:5000]}..."
        return serialised

    @staticmethod
    def _clean_text(value: str) -> str:
        value = (
            value.replace("\xa0", " ")
            .replace("\u200b", "")
            .replace("\ufeff", "")
            .replace("\r\n", "\n")
            .replace("\r", "\n")
        )
        lines: list[str] = []
        seen: set[str] = set()
        for raw_line in value.split("\n"):
            line = _WHITESPACE.sub(" ", raw_line).strip()
            if len(line) < 2:
                continue
            key = line.casefold()
            if key in seen:
                continue
            seen.add(key)
            lines.append(line)
        result = "\n".join(lines)
        return _MULTI_NEWLINE.sub("\n\n", result).strip()

    @staticmethod
    def _truncate(value: str, limit: int) -> tuple[str, bool]:
        if len(value) <= limit:
            return value, False
        separator = "\n...[middle content truncated]...\n"
        head_length = int(limit * 0.72)
        tail_length = max(0, limit - head_length - len(separator))
        return f"{value[:head_length]}{separator}{value[-tail_length:]}", True


__all__ = ["ExtractedWebsite", "InternalLink", "WebsiteExtractor", "BlockedUrlError", "InvalidUrlError"]
