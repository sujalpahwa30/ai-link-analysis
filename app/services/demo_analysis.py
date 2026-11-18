"""Deterministic, clearly-labelled demo analyzer.

This is used only when no LLM credential is configured. It keeps the API runnable
for reviewers and tests without pretending that heuristics are an LLM. Competitor
rows are deliberately omitted rather than fabricated.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from app.schemas import (
    AudienceSegment,
    Brand,
    CompetitorAnalysis,
    LLMAnalysis,
    LLMOutput,
    LLMUsage,
    SearchQuery,
    SEOImprovements,
    ServiceItem,
    SupportingContent,
    TargetAudience,
)
from app.services.llm_service import LLMResult
from app.services.website_extractor import ExtractedWebsite

_SERVICE_HINTS = {
    "service",
    "solution",
    "development",
    "design",
    "marketing",
    "consulting",
    "management",
    "software",
    "app",
    "cloud",
    "seo",
    "support",
    "installation",
    "repair",
    "training",
    "delivery",
    "logistics",
    "finance",
    "legal",
    "health",
    "care",
    "treatment",
    "strategy",
    "audit",
    "integration",
    "maintenance",
    "ecommerce",
    "commerce",
    "security",
    "analytics",
}
_GENERIC_ANCHORS = {
    "home",
    "about",
    "about us",
    "contact",
    "contact us",
    "our services",
    "services",
    "solutions",
    "products",
    "blog",
    "news",
    "careers",
    "login",
    "privacy",
    "terms",
    "menu",
    "learn more",
    "read more",
}
_GENERIC_SERVICE_NAMES = {
    "support",
    "legal",
    "sales",
    "pricing",
    "resources",
    "partners",
    "developers",
    "enterprise",
    "docs",
    "documentation",
    "community",
    "features",
    "platform",
    "apps",
    "markets",
    "customers",
    "company",
    "help",
    "help center",
    "case studies",
    "integrations",
    "security",
    "status",
}
_TAGLINE_MARKERS = (
    "very own",
    "everything",
    "to the core",
    "hyperdriven",
    "all in one",
    "built for",
    "designed for",
    "the future of",
)
_GENERIC_BRANDS = {
    "home",
    "welcome",
    "index",
    "official website",
    "website",
    "untitled",
    "example domain",
}


@dataclass(frozen=True, slots=True)
class CategoryProfile:
    name: str
    primary_keyword: str
    keywords: tuple[str, ...]
    services: tuple[str, ...]
    industries: tuple[str, ...]
    business_types: tuple[str, ...]
    customer_profiles: tuple[str, ...]
    needs: tuple[str, ...]
    interests: tuple[str, ...]
    segments: tuple[str, ...]
    advantages: tuple[str, ...]
    opportunities: tuple[str, ...]


_PROFILES: tuple[CategoryProfile, ...] = (
    CategoryProfile(
        name="Digital & Web Services",
        primary_keyword="digital services",
        keywords=("web", "website", "seo", "marketing", "design", "branding", "content", "social", "digital"),
        services=(
            "Website Development",
            "Search Engine Optimization",
            "Digital Marketing",
            "UI/UX Design",
            "E-commerce Development",
        ),
        industries=("Retail", "Professional services", "Healthcare", "Education", "Technology"),
        business_types=("Small and medium businesses", "Startups", "Enterprise marketing teams"),
        customer_profiles=(
            "Business owners without an in-house web team",
            "Marketing leaders seeking measurable growth",
            "Startups preparing a product launch",
        ),
        needs=("A credible online presence", "More qualified website traffic", "Conversion-focused digital experiences"),
        interests=("SEO best practices", "Web performance", "Customer acquisition", "Digital transformation"),
        segments=(
            "SMBs planning a website redesign",
            "Startups launching a digital product",
            "Regional service businesses investing in local SEO",
        ),
        advantages=(
            "Integrated design, development, and marketing capabilities.",
            "Positioning around measurable online business outcomes.",
        ),
        opportunities=(
            "Publish case studies with quantified outcomes.",
            "Create industry-specific service landing pages.",
            "Add comparison content for high-intent searches.",
        ),
    ),
    CategoryProfile(
        name="Software & SaaS",
        primary_keyword="software solutions",
        keywords=("software", "saas", "app", "application", "platform", "api", "cloud", "data", "automation"),
        services=(
            "Custom Software Development",
            "SaaS Product Development",
            "API Integration",
            "Cloud Migration",
            "Application Support",
        ),
        industries=("Technology", "Financial services", "Logistics", "Healthcare", "Professional services"),
        business_types=("Software companies", "Digital product teams", "Enterprise IT departments"),
        customer_profiles=(
            "Product leaders scaling a SaaS platform",
            "Operations teams replacing manual workflows",
            "CTOs modernising legacy systems",
        ),
        needs=("Reliable software delivery", "Faster product iteration", "Secure and scalable integrations"),
        interests=("Cloud architecture", "Developer productivity", "Data security", "Product analytics"),
        segments=(
            "Seed to Series B SaaS companies",
            "Mid-market operations teams",
            "Enterprises modernising internal tooling",
        ),
        advantages=(
            "Technical depth across product delivery and integration.",
            "Ability to support both new builds and legacy modernisation.",
        ),
        opportunities=(
            "Package fixed-scope discovery engagements.",
            "Publish technical case studies and architecture notes.",
            "Create vertical SaaS landing pages.",
        ),
    ),
    CategoryProfile(
        name="E-commerce & Retail",
        primary_keyword="ecommerce solutions",
        keywords=("shop", "store", "ecommerce", "e-commerce", "retail", "product", "fashion", "checkout", "order"),
        services=(
            "E-commerce Store Development",
            "Product Merchandising",
            "Order Fulfilment",
            "Retail Marketing",
            "Customer Retention",
        ),
        industries=("Retail", "Consumer goods", "Fashion", "Food and beverage", "Wholesale"),
        business_types=("Direct-to-consumer brands", "Independent retailers", "Wholesale distributors"),
        customer_profiles=(
            "Store owners expanding online",
            "Brand managers improving repeat purchase",
            "Operations leads scaling fulfilment",
        ),
        needs=("Higher conversion rates", "Efficient order operations", "Stronger customer loyalty"),
        interests=("Conversion optimisation", "Product photography", "Fulfilment automation", "Loyalty programmes"),
        segments=(
            "Independent retailers moving online",
            "DTC brands preparing seasonal campaigns",
            "Wholesalers adding a digital catalogue",
        ),
        advantages=(
            "Combined storefront, merchandising, and retention focus.",
            "Understanding of both customer experience and back-office operations.",
        ),
        opportunities=(
            "Add product-category landing pages.",
            "Publish sizing, delivery, and returns FAQs.",
            "Build seasonal campaign landing pages.",
        ),
    ),
    CategoryProfile(
        name="Professional & Financial Services",
        primary_keyword="professional services",
        keywords=("consult", "legal", "account", "advisory", "finance", "insurance", "tax", "audit", "compliance"),
        services=(
            "Business Consulting",
            "Financial Advisory",
            "Compliance Support",
            "Risk Assessment",
            "Client Reporting",
        ),
        industries=("Professional services", "Financial services", "Real estate", "Manufacturing", "Technology"),
        business_types=("SMEs", "Family businesses", "Corporate finance teams"),
        customer_profiles=(
            "Owners planning growth or succession",
            "Finance leaders improving controls",
            "Operational teams managing regulatory change",
        ),
        needs=("Trusted specialist advice", "Regulatory confidence", "Clear financial decision support"),
        interests=("Risk management", "Business planning", "Tax efficiency", "Governance"),
        segments=(
            "Growing SMEs without an in-house specialist",
            "Family-owned businesses planning succession",
            "Mid-market companies preparing for audit",
        ),
        advantages=(
            "Specialist expertise paired with client-specific advice.",
            "Potential for long-term advisory relationships.",
        ),
        opportunities=(
            "Publish sector-specific advisory pages.",
            "Add explainers for common compliance questions.",
            "Create downloadable planning checklists.",
        ),
    ),
    CategoryProfile(
        name="Health & Wellness",
        primary_keyword="healthcare services",
        keywords=("health", "clinic", "medical", "wellness", "dental", "care", "patient", "therapy", "treatment"),
        services=(
            "Patient Consultation",
            "Preventive Care",
            "Treatment Planning",
            "Wellness Programs",
            "Follow-up Care",
        ),
        industries=("Healthcare", "Wellness", "Sports", "Corporate benefits", "Senior care"),
        business_types=("Clinics", "Private practices", "Wellness providers"),
        customer_profiles=(
            "Patients seeking a trusted provider",
            "Families comparing care options",
            "Employers supporting employee wellbeing",
        ),
        needs=("Clear treatment information", "Accessible appointments", "Trustworthy, patient-centred care"),
        interests=("Preventive health", "Treatment options", "Practitioner credentials", "Patient outcomes"),
        segments=(
            "Local patients researching a provider",
            "Families comparing preventive-care options",
            "Employers building wellness benefits",
        ),
        advantages=(
            "Patient-centred service positioning.",
            "Opportunity to build trust through clear treatment information.",
        ),
        opportunities=(
            "Add condition- and treatment-specific pages.",
            "Publish practitioner profiles and credentials.",
            "Create patient FAQs about appointments and aftercare.",
        ),
    ),
    CategoryProfile(
        name="Education & Training",
        primary_keyword="training programs",
        keywords=("education", "course", "training", "school", "learn", "student", "academy", "certification", "tutor"),
        services=(
            "Course Development",
            "Instructor-Led Training",
            "Corporate Learning",
            "Certification Programs",
            "Learning Support",
        ),
        industries=("Education", "Corporate learning", "Technology", "Healthcare", "Professional development"),
        business_types=("Training providers", "Schools", "Employers"),
        customer_profiles=(
            "Learners seeking career-relevant skills",
            "HR teams building capability",
            "Educators designing new programmes",
        ),
        needs=("Clear learning outcomes", "Flexible delivery options", "Recognised credentials"),
        interests=("Career progression", "Practical exercises", "Certification value", "Time-to-completion"),
        segments=(
            "Career changers comparing programmes",
            "Employers upskilling teams",
            "Graduates adding a certification",
        ),
        advantages=(
            "Outcome-led programme positioning.",
            "Opportunity to differentiate through practical learning.",
        ),
        opportunities=(
            "Create programme and outcome landing pages.",
            "Publish learner stories and completion outcomes.",
            "Add FAQ content about schedules, prerequisites, and certification.",
        ),
    ),
)

_DEFAULT_PROFILE = CategoryProfile(
    name="General Business Services",
    primary_keyword="business services",
    keywords=(),
    services=(
        "Business Consulting",
        "Client Onboarding",
        "Service Delivery",
        "Account Management",
        "Process Improvement",
    ),
    industries=("Professional services", "Retail", "Technology", "Manufacturing", "Local services"),
    business_types=("Small and medium businesses", "Startups", "Established companies"),
    customer_profiles=(
        "Business owners seeking a reliable service partner",
        "Operations leaders improving delivery",
        "Teams evaluating specialist providers",
    ),
    needs=("A dependable service partner", "Clear scope and pricing", "Measurable business outcomes"),
    interests=("Service quality", "Turnaround time", "Specialist expertise", "Customer support"),
    segments=(
        "SMBs comparing specialist providers",
        "Growing companies formalising operations",
        "Local businesses improving their online presence",
    ),
    advantages=(
        "A focused service proposition.",
        "Potential to differentiate through customer experience and proof.",
    ),
    opportunities=(
        "Clarify services and outcomes on dedicated pages.",
        "Add proof points, case studies, and FAQs.",
        "Build location or industry-specific landing pages.",
    ),
)


def _normalise(value: str) -> str:
    return " ".join(value.replace("\xa0", " ").split()).strip()


def _unique(values: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = _normalise(value)
        key = clean.casefold()
        if clean and key not in seen:
            seen.add(key)
            output.append(clean)
    return output


def _fit(value: str, maximum: int, minimum: int = 1) -> str:
    text = _normalise(value)
    if len(text) > maximum:
        cut = text[: maximum - 1].rsplit(" ", 1)[0].rstrip(" ,.;:-")
        text = f"{cut}."
    if len(text) < minimum:
        text = (text + " " + "Additional detail is available on the website.").strip()
        text = text[:maximum].rstrip()
    return text


def _brand_name(extraction: ExtractedWebsite) -> str:
    if extraction.site_name:
        return _fit(extraction.site_name, 100)
    if extraction.title:
        parts = [
            _normalise(part)
            for part in re.split(r"\s+[|–—:·]\s+|\s+-\s+", extraction.title)
            if _normalise(part)
        ]
        for part in parts:
            if part.casefold() not in _GENERIC_BRANDS and len(part) >= 2:
                return _fit(part, 100)
    host = urlsplit(extraction.final_url).hostname or "the business"
    label = host.removeprefix("www.").split(".")[0].replace("-", " ").strip()
    return label.title() if label else "The Business"


def _profile_for(extraction: ExtractedWebsite) -> CategoryProfile:
    haystack = " ".join(
        [
            extraction.title,
            extraction.meta_description,
            " ".join(extraction.headings),
            " ".join(link.text for link in extraction.internal_links),
            extraction.text[:6000],
        ]
    ).casefold()

    best_profile = _DEFAULT_PROFILE
    best_score = 0
    for profile in _PROFILES:
        score = sum(1 for keyword in profile.keywords if keyword in haystack)
        if score > best_score:
            best_profile = profile
            best_score = score
    return best_profile


def _summary(extraction: ExtractedWebsite, brand: str, profile: CategoryProfile) -> str:
    if extraction.meta_description and len(extraction.meta_description) >= 20:
        return _fit(extraction.meta_description, 900)
    paragraphs = [line for line in extraction.text.splitlines() if len(line) >= 50]
    if paragraphs:
        return _fit(" ".join(paragraphs[:2]), 900)
    return f"{brand} is a business active in the {profile.name.lower()} category."


def _valid_service_candidate(text: str, brand: str) -> bool:
    candidate = _normalise(text)
    words = candidate.split()
    if not 3 <= len(candidate) <= 70:
        return False
    if len(words) < 2 or len(words) > 5:
        return False
    if candidate.casefold() in _GENERIC_ANCHORS or candidate.casefold() in _GENERIC_SERVICE_NAMES:
        return False
    if candidate.casefold() == brand.casefold():
        return False
    if any(mark in candidate for mark in ".!?"):
        return False
    lowered = candidate.casefold()
    if lowered.startswith(("your ", "we ", "our ", "the ", "a ", "an ")):
        return False
    if any(marker in lowered for marker in _TAGLINE_MARKERS):
        return False
    return any(hint in lowered for hint in _SERVICE_HINTS)


def _extract_service_names(extraction: ExtractedWebsite, brand: str) -> list[str]:
    scored: list[tuple[int, str]] = []
    for heading in extraction.headings:
        if _valid_service_candidate(heading, brand):
            scored.append((3, heading))
    for link in extraction.internal_links:
        if _valid_service_candidate(link.text, brand):
            scored.append((2, link.text))

    seen: set[str] = set()
    names: list[str] = []
    for _, value in sorted(scored, key=lambda item: (-item[0], len(item[1]))):
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        names.append(_fit(value, 100))
        if len(names) == 5:
            break
    return names


def _services(
    extraction: ExtractedWebsite,
    profile: CategoryProfile,
    brand: str,
) -> list[ServiceItem]:
    extracted = _extract_service_names(extraction, brand)
    names: list[tuple[str, str]] = [(name, "website") for name in extracted]

    for fallback in profile.services:
        if len(names) >= 5:
            break
        if all(fallback.casefold() != name.casefold() for name, _ in names):
            names.append((fallback, "inferred"))

    services: list[ServiceItem] = []
    for name, source in names[:5]:
        if source == "website":
            description = (
                f"{name} appears as a service or offering on the website. "
                f"The page positions it within its {profile.name.lower()} proposition."
            )
            evidence = f"Website heading or navigation label: {name}"
            benefit = f"Helps customers address {profile.needs[0].lower()}."
        else:
            description = (
                f"{name} is a category-based recommendation inferred from the site's "
                f"focus on {profile.name.lower()}; it is not explicitly confirmed on the page."
            )
            evidence = ""
            benefit = f"Could help the business address demand for {profile.needs[0].lower()}."
        services.append(
            ServiceItem(
                service_name=name,
                description=_fit(description, 700),
                target_industry=profile.industries[0],
                target_customer=profile.customer_profiles[0],
                main_benefit=_fit(benefit, 450),
                source=source,  # type: ignore[arg-type]
                evidence=_fit(evidence, 450) if evidence else "",
            )
        )
    return services


def _search_queries(
    brand: str,
    profile: CategoryProfile,
    services: list[ServiceItem],
) -> list[SearchQuery]:
    primary = profile.primary_keyword
    service_one = services[0].service_name.lower()
    service_two = services[1].service_name.lower()
    return [
        SearchQuery(
            query=_fit(f"best {primary} company", 200),
            platforms=["Google", "LinkedIn"],
            intent="Commercial investigation",
            business_relevance="High-intent buyers comparing specialist providers.",
            target_audience=profile.customer_profiles[0],
        ),
        SearchQuery(
            query=_fit(f"{service_one} services", 200),
            platforms=["Google", "Facebook"],
            intent="Service discovery",
            business_relevance=f"Direct match for a core offering from {brand}.",
            target_audience=profile.business_types[0],
        ),
        SearchQuery(
            query=_fit(f"{primary} for startups", 200),
            platforms=["LinkedIn", "X (Twitter)"],
            intent="Business discovery",
            business_relevance="Targets early-stage buyers with budget and urgency.",
            target_audience="Startup founders and small leadership teams",
        ),
        SearchQuery(
            query=_fit(f"{service_two} ideas", 200),
            platforms=["Instagram", "Facebook"],
            intent="Inspiration and research",
            business_relevance="Supports upper-funnel discovery through visual examples and education.",
            target_audience="Owners and managers researching options",
        ),
        SearchQuery(
            query=_fit(f"affordable {primary}", 200),
            platforms=["Google", "X (Twitter)"],
            intent="Price-sensitive service search",
            business_relevance="Captures buyers who are actively evaluating cost and fit.",
            target_audience=profile.business_types[0],
        ),
    ]


def _seo(
    extraction: ExtractedWebsite,
    brand: str,
    profile: CategoryProfile,
    services: list[ServiceItem],
    summary: str,
) -> SEOImprovements:
    service_keywords = [service.service_name for service in services]
    secondary = _unique([*service_keywords[:4], profile.name])[:6]
    long_tail = _unique(
        [
            f"how to choose {profile.primary_keyword}",
            f"{service_keywords[0].lower()} for small businesses",
            f"best {profile.primary_keyword} for startups",
        ]
    )
    business_keywords = _unique(
        [
            f"{profile.name.lower()} company",
            f"professional {profile.primary_keyword}",
            f"custom {profile.primary_keyword}",
        ]
    )

    title = _fit(f"{brand} | {profile.primary_keyword.title()}", 70, minimum=5)
    meta = _normalise(summary)
    if len(meta) < 140:
        meta = f"{meta.rstrip('.')} Explore {profile.primary_keyword} from {brand}."
    meta = _fit(meta, 160, minimum=20)

    supporting = [
        SupportingContent(
            title=f"{service.service_name} service page",
            content_type="service_landing_page",
            why_it_helps=f"Creates a focused page for buyers searching for {service.service_name.lower()}.",
        )
        for service in services[:2]
    ]
    supporting.extend(
        [
            SupportingContent(
                title=f"Frequently asked questions about {profile.primary_keyword}",
                content_type="faq",
                why_it_helps="Captures question-based searches and reduces pre-sales friction.",
            ),
            SupportingContent(
                title=f"{profile.industries[0]} case study",
                content_type="case_study",
                why_it_helps="Provides credible proof and supports commercial-intent queries.",
            ),
        ]
    )

    return SEOImprovements(
        primary_keyword=profile.primary_keyword,
        secondary_keywords=secondary,
        long_tail_keywords=long_tail,
        business_keywords=business_keywords,
        recommended_title=title,
        title_rationale="Combines the brand with the category keyword and stays within a concise title length.",
        meta_description=meta,
        meta_description_rationale="Uses the site's own description, the primary keyword, and a clear next step.",
        supporting_content=supporting,
    )


def _audience(profile: CategoryProfile) -> TargetAudience:
    primary = [
        AudienceSegment(
            segment=f"Growing {profile.industries[0].lower()} businesses",
            description=f"Small and mid-sized organisations that need {profile.primary_keyword}.",
            business_type="Small and medium businesses",
            needs=list(profile.needs[:2]),
            marketing_angle="Lead with practical outcomes, proof, and a low-friction consultation.",
        ),
        AudienceSegment(
            segment="Specialist teams and department leads",
            description=f"Teams evaluating a specialist partner for {profile.primary_keyword}.",
            business_type=profile.business_types[1] if len(profile.business_types) > 1 else "Specialist teams",
            needs=list(profile.needs[1:3]) or list(profile.needs[:1]),
            marketing_angle="Use detailed service pages, FAQs, and evidence of delivery capability.",
        ),
    ]
    secondary = [
        AudienceSegment(
            segment="Startups and new ventures",
            description="Early-stage teams that need a credible partner without building everything in-house.",
            business_type="Startups",
            needs=["Fast mobilisation", "Clear scope and pricing"],
            marketing_angle="Offer a defined starter engagement and transparent next steps.",
        ),
        AudienceSegment(
            segment="Established and multi-location organisations",
            description="Larger organisations comparing providers for consistency and specialist capacity.",
            business_type="Enterprise and multi-site organisations",
            needs=["Reliable delivery", "Documented processes"],
            marketing_angle="Emphasise governance, integration, and account support.",
        ),
    ]
    return TargetAudience(
        primary_audience=primary,
        secondary_audience=secondary,
        industries=list(profile.industries),
        business_types=list(profile.business_types),
        customer_profiles=list(profile.customer_profiles),
        location_focus="",
        customer_needs=list(profile.needs),
        customer_interests=list(profile.interests),
        recommended_segments=list(profile.segments),
    )


class DemoAnalyzer:
    provider = "demo"
    model = "deterministic-heuristic-v1"

    def analyze(self, extraction: ExtractedWebsite) -> LLMResult:
        profile = _profile_for(extraction)
        brand_name = _brand_name(extraction)
        summary = _summary(extraction, brand_name, profile)
        services = _services(extraction, profile, brand_name)
        queries = _search_queries(brand_name, profile, services)
        seo = _seo(extraction, brand_name, profile, services, summary)
        audience = _audience(profile)
        product_names = _unique([service.service_name for service in services])

        analysis = LLMAnalysis(
            competitor_analysis=CompetitorAnalysis(
                brand_overview=summary,
                main_category=profile.name,
                products_services=product_names,
                competitors=[],
                competitive_advantages=list(profile.advantages),
                opportunities=list(profile.opportunities),
                comparison_summary=(
                    "Competitor rows are intentionally omitted in demo mode. "
                    "Configure an LLM API key for AI-suggested competitor mapping."
                ),
            ),
            target_search_queries=queries,
            seo_improvements=seo,
            top_services=services,
            target_audience=audience,
            data_quality_notes=[
                "Demo mode uses deterministic heuristics rather than an LLM.",
                "Services marked 'inferred' are category-based suggestions, not confirmed page content.",
                "Competitor analysis is intentionally empty to avoid fabricating market data.",
            ],
        )
        output = LLMOutput(
            brand=Brand(name=brand_name, category=profile.name, summary=summary),
            analysis=analysis,
        )
        warnings = [
            "Demo mode is active. Set GEMINI_API_KEY or OPENAI_API_KEY for grounded LLM analysis "
            "and competitor suggestions.",
            *extraction.warnings,
        ]
        return LLMResult(
            output=output,
            provider=self.provider,
            model=self.model,
            fallback_used=False,
            usage=LLMUsage(
                input_tokens=0,
                output_tokens=0,
                total_tokens=0,
                estimated_cost_usd=0.0,
            ),
            warnings=warnings,
        )
