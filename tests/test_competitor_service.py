from __future__ import annotations

from app.schemas import Competitor
from app.services.competitor_service import sanitise_competitors


def _competitor(name: str, website: str = "https://example.com") -> Competitor:
    return Competitor(
        name=name,
        website=website,
        main_service="Consulting",
        target_market="SMBs",
        strengths=["A strength"],
        weaknesses=["A gap"],
        basis="ai_suggested",
    )


def test_duplicate_and_brand_competitors_are_removed() -> None:
    competitors, warnings = sanitise_competitors(
        "Acme Cloud",
        [
            _competitor("Acme Cloud"),
            _competitor("Northstar"),
            _competitor("northstar"),
            _competitor("BluePeak"),
            _competitor("Orbit"),
        ],
    )
    assert [competitor.name for competitor in competitors] == ["Northstar", "BluePeak", "Orbit"]
    assert any("AI-suggested" in warning for warning in warnings)


def test_malformed_competitor_url_is_removed() -> None:
    competitors, warnings = sanitise_competitors(
        "Acme Cloud",
        [
            _competitor("Northstar", "not a url"),
            _competitor("BluePeak"),
            _competitor("Orbit"),
        ],
    )
    assert competitors[0].website == ""
    assert any("malformed" in warning.lower() for warning in warnings)


def test_fewer_than_three_competitors_is_surfaced() -> None:
    competitors, warnings = sanitise_competitors("Acme Cloud", [_competitor("Northstar")])
    assert len(competitors) == 1
    assert any("minimum is 3" in warning for warning in warnings)


def test_empty_competitor_set_is_not_fabricated() -> None:
    competitors, warnings = sanitise_competitors("Acme Cloud", [])
    assert competitors == []
    assert any("unverified" in warning for warning in warnings)


def test_placeholder_competitors_are_removed() -> None:
    competitors, warnings = sanitise_competitors(
        "Example",
        [
            _competitor("example.org"),
            _competitor("example.net"),
            _competitor("dummyurl.com"),
            _competitor("Localhost"),
            _competitor("Placeholder.com"),
            _competitor("Northstar"),
        ],
    )
    assert [competitor.name for competitor in competitors] == ["Northstar"]
    assert any("placeholder" in warning for warning in warnings)


def test_reserved_documented_domains_are_removed_by_name() -> None:
    competitors, _ = sanitise_competitors(
        "Acme Cloud",
        [_competitor("ample.test"), _competitor("Northstar")],
    )
    assert [competitor.name for competitor in competitors] == ["Northstar"]
