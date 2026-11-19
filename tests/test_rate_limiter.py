from __future__ import annotations

from app.services.rate_limiter import SlidingWindowRateLimiter, resolve_client_key


def test_edge_address_wins_over_forwarded_for() -> None:
    headers = {
        "x-forwarded-for": "203.0.113.9, 198.51.100.4",
        "cf-connecting-ip": "198.51.100.4",
    }
    assert resolve_client_key(headers, "10.0.0.7") == "198.51.100.4"


def test_forwarded_for_alone_cannot_select_the_bucket() -> None:
    # A caller can send any X-Forwarded-For value; without the edge header the
    # socket peer is used, so the header cannot be used to rotate buckets.
    headers = {"x-forwarded-for": "203.0.113.9"}
    assert resolve_client_key(headers, "10.0.0.7") == "10.0.0.7"


def test_blank_edge_header_falls_back_to_socket_peer() -> None:
    headers = {"cf-connecting-ip": "   "}
    assert resolve_client_key(headers, "10.0.0.7") == "10.0.0.7"


def test_missing_client_and_headers_use_unknown() -> None:
    assert resolve_client_key({}, None) == "unknown"


def test_header_lookup_is_case_insensitive() -> None:
    assert resolve_client_key({"CF-Connecting-IP": "198.51.100.4"}, None) == "198.51.100.4"


def test_limiter_allows_limit_then_blocks() -> None:
    limiter = SlidingWindowRateLimiter(limit=2, window_seconds=60)
    assert limiter.check("a") == (True, 0)
    assert limiter.check("a") == (True, 0)
    allowed, retry_after = limiter.check("a")
    assert allowed is False
    assert retry_after >= 1


def test_limiter_is_per_key() -> None:
    limiter = SlidingWindowRateLimiter(limit=1, window_seconds=60)
    assert limiter.check("a") == (True, 0)
    assert limiter.check("b") == (True, 0)
    assert limiter.check("a")[0] is False
