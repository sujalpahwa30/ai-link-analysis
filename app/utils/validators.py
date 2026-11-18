"""URL parsing, SSRF protection, and network validation."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit, urlunsplit

from app.exceptions import BlockedUrlError, InvalidUrlError

_ALLOWED_SCHEMES = {"http", "https"}
_ALLOWED_PORTS = {80, 443}
_BLOCKED_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
    "ip6-localhost",
    "ip6-loopback",
    "metadata",
    "metadata.google.internal",
}
_BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".home.arpa")


def _is_public_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return bool(address.is_global)


def _format_netloc(host: str, port: int | None) -> str:
    if ":" in host:
        host = f"[{host}]"
    return f"{host}:{port}" if port else host


def validate_url_syntax(raw_url: str) -> str:
    """Normalise and validate a URL without performing DNS resolution.

    A missing scheme is treated as HTTPS. Fragments are removed because they are
    never sent to the server and would create duplicate cache keys.
    """

    value = (raw_url or "").strip()
    if not value:
        raise InvalidUrlError("A non-empty URL is required.")
    if len(value) > 2048:
        raise InvalidUrlError("The URL must not exceed 2048 characters.")
    if any(character.isspace() or ord(character) < 32 for character in value):
        raise InvalidUrlError("The URL must not contain whitespace or control characters.")

    if "://" not in value:
        value = f"https://{value}"

    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise InvalidUrlError("The URL could not be parsed.") from exc

    scheme = parsed.scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise InvalidUrlError("Only http:// and https:// URLs are supported.")
    if parsed.username or parsed.password:
        raise InvalidUrlError("URLs containing embedded credentials are not allowed.")

    host = parsed.hostname
    if not host:
        raise InvalidUrlError("The URL must include a hostname.")
    host = host.rstrip(".").lower()
    if host in _BLOCKED_HOSTNAMES or host.endswith(_BLOCKED_SUFFIXES):
        raise BlockedUrlError()

    try:
        port = parsed.port
    except ValueError as exc:
        raise InvalidUrlError("The URL contains an invalid port.") from exc
    if port is not None and port not in _ALLOWED_PORTS:
        raise InvalidUrlError("Only ports 80 and 443 are allowed.")

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise BlockedUrlError()

    try:
        ascii_host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise InvalidUrlError("The hostname is not a valid internationalised domain name.") from exc

    path = parsed.path or "/"
    normalised = urlunsplit(
        (scheme, _format_netloc(ascii_host, port), path, parsed.query, "")
    )
    return normalised


async def ensure_public_url(url: str) -> None:
    """Resolve the hostname and reject if any answer is non-public.

    This blocks literal private IPs as well as DNS names that resolve to loopback,
    RFC1918, link-local, CGNAT, multicast, reserved, or unspecified addresses.

    Note: DNS validation and the subsequent HTTP connection are separate steps. A
    hostile DNS server could attempt a rebinding race. Production deployments should
    additionally use an egress firewall / proxy with a public-IP allow-list.
    """

    parsed = urlsplit(url)
    host = parsed.hostname
    if not host:
        raise InvalidUrlError("The URL must include a hostname.")

    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    try:
        literal_ip = ipaddress.ip_address(host)
    except ValueError:
        literal_ip = None
    if literal_ip is not None:
        if not literal_ip.is_global:
            raise BlockedUrlError()
        return

    loop = asyncio.get_running_loop()
    try:
        answers = await loop.getaddrinfo(
            host,
            port,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
    except socket.gaierror as exc:
        raise InvalidUrlError("The hostname could not be resolved.") from exc

    if not answers:
        raise InvalidUrlError("The hostname returned no DNS records.")

    resolved_addresses: set[str] = set()
    for answer in answers:
        address_text = answer[4][0]
        resolved_addresses.add(address_text)
        if not _is_public_ip(address_text):
            raise BlockedUrlError()

    if not resolved_addresses:
        raise InvalidUrlError("The hostname returned no usable addresses.")
