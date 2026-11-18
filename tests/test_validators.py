from __future__ import annotations

from unittest.mock import patch

import pytest

from app.exceptions import BlockedUrlError, InvalidUrlError
from app.utils.validators import ensure_public_url, validate_url_syntax


class _FakeLoop:
    def __init__(self, addresses: list[str]) -> None:
        self.addresses = addresses

    async def getaddrinfo(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return [
            (2, 1, 6, "", (address, 443))
            for address in self.addresses
        ]


def test_url_without_scheme_is_normalised_to_https() -> None:
    assert validate_url_syntax("example.com") == "https://example.com/"


def test_url_fragment_is_removed() -> None:
    assert validate_url_syntax("https://example.com/page#section") == "https://example.com/page"


@pytest.mark.parametrize(
    ("url", "exception"),
    [
        ("http://127.0.0.1/", BlockedUrlError),
        ("http://localhost:4000/", BlockedUrlError),
        ("http://[::1]/", BlockedUrlError),
        ("http://169.254.169.254/latest/meta-data", BlockedUrlError),
        ("file:///etc/passwd", InvalidUrlError),
        ("ftp://example.com/file", InvalidUrlError),
        ("http://example.com:8080/", InvalidUrlError),
        ("https://user:pass@example.com/", InvalidUrlError),
        ("https://example.com/\nmalicious", InvalidUrlError),
    ],
)
def test_unsafe_or_invalid_urls_are_rejected(url: str, exception: type[Exception]) -> None:
    with pytest.raises(exception):
        validate_url_syntax(url)


def test_internationalised_domain_is_encoded() -> None:
    assert validate_url_syntax("https://bücher.example/") == "https://xn--bcher-kva.example/"


@pytest.mark.asyncio
async def test_public_ip_literal_is_allowed() -> None:
    await ensure_public_url("https://8.8.8.8/")


@pytest.mark.asyncio
async def test_dns_answer_with_private_address_is_blocked() -> None:
    loop = _FakeLoop(["10.0.0.5"])
    with (
        patch("app.utils.validators.asyncio.get_running_loop", return_value=loop),
        pytest.raises(BlockedUrlError),
    ):
        await ensure_public_url("https://public.example/")


@pytest.mark.asyncio
async def test_dns_answer_with_only_public_addresses_is_allowed() -> None:
    loop = _FakeLoop(["93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"])
    with patch("app.utils.validators.asyncio.get_running_loop", return_value=loop):
        await ensure_public_url("https://public.example/")
