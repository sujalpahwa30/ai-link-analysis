"""Post-processing for LLM-generated competitor suggestions.

Competitor discovery is intentionally part of the single analysis call to avoid an
extra API request. This module applies deterministic hygiene and surfaces caveats
instead of silently presenting model output as verified market research.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from app.schemas import Competitor


def sanitise_competitors(
    brand_name: str,
    competitors: list[Competitor],
) -> tuple[list[Competitor], list[str]]:
    warnings: list[str] = []
    output: list[Competitor] = []
    seen: set[str] = set()

    for competitor in competitors:
        key = competitor.name.casefold().strip()
        if not key or key == brand_name.casefold().strip() or key in seen:
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
