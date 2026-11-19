"""Post-processing for LLM-generated competitor suggestions.

Competitor discovery is intentionally part of the single analysis call to avoid an
extra API request. This module applies deterministic hygiene and surfaces caveats
instead of silently presenting model output as verified market research.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from app.schemas import Competitor

_PLACEHOLDER_NAMES = {
    "example",
    "dummy",
    "localhost",
    "placeholder",
    "n/a",
    "none",
    "unknown",
    "your company",
    "your brand",
    "company name",
    "competitor",
    "competitor name",
}
_PLACEHOLDER_PREFIXES = (
    "example.",
    "example ",
    "dummy",
    "localhost",
    "placeholder",
    "your company",
    "your brand",
    "insert ",
)
_PLACEHOLDER_SUFFIXES = (
    ".example",
    ".example.com",
    ".example.net",
    ".example.org",
    ".test",
    ".invalid",
    ".localhost",
)


def _is_placeholder(competitor: Competitor) -> bool:
    """Reject obvious filler rows such as ``example.org`` or ``dummyurl.com``.

    Thin pages make models fall back to reserved documentation domains. Those
    rows are not competitors, and returning them would present invented market
    research as fact.
    """

    name = competitor.name.casefold().strip()
    if name in _PLACEHOLDER_NAMES:
        return True
    return name.startswith(_PLACEHOLDER_PREFIXES) or name.endswith(_PLACEHOLDER_SUFFIXES)



def sanitise_competitors(
    brand_name: str,
    competitors: list[Competitor],
) -> tuple[list[Competitor], list[str]]:
    warnings: list[str] = []
    output: list[Competitor] = []
    seen: set[str] = set()
    placeholders = 0

    for competitor in competitors:
        key = competitor.name.casefold().strip()
        if not key or key == brand_name.casefold().strip() or key in seen:
            continue
        if _is_placeholder(competitor):
            placeholders += 1
            continue
        seen.add(key)

        website = competitor.website.strip()
        if website:
            parsed = urlsplit(website)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                website = ""
                warnings.append(
                    f"A malformed competitor URL was removed for {competitor.name}."
                )

        output.append(competitor.model_copy(update={"website": website}))
        if len(output) >= 6:
            break

    if placeholders:
        warnings.append(
            f"{placeholders} placeholder competitor entr"
            f"{'y was' if placeholders == 1 else 'ies were'} removed instead of being reported as market research."
        )

    if not output:
        warnings.append(
            "No competitor set was returned. Treat competitor positioning as unverified."
        )
    elif len(output) < 3:
        warnings.append(
            f"Only {len(output)} competitor(s) were identified; the requested minimum is 3 where reliable."
        )

    if any(competitor.basis == "ai_suggested" for competitor in output):
        warnings.append(
            "One or more competitors are AI-suggested category peers, not verified market research."
        )

    return output, warnings
