"""Target company list — companies that hire globally at SF pay bands.

`verified_pay_parity=True` = we've confirmed they run one pay band worldwide
(published handbook, transparent bands, or public reports).
`verified_global_hire=True` = they publicly hire from anywhere on the planet.
"""
from __future__ import annotations

from dataclasses import dataclass

from .models import Source


@dataclass(frozen=True)
class Target:
    source: Source
    slug: str
    display_name: str
    verified_global_hire: bool = False
    verified_pay_parity: bool = False
    notes: str = ""


TARGETS: list[Target] = [
    # --- Tier 1: pay parity, global hire, transparent bands ---
    Target(Source.GREENHOUSE, "gitlab", "GitLab", True, True,
           "Handbook-first, publishes bands"),
    Target(Source.LEVER, "automattic", "Automattic", True, True,
           "Fully distributed since day one"),
    Target(Source.GREENHOUSE, "remotecom", "Remote.com", True, True,
           "Global pay by design"),
    Target(Source.GREENHOUSE, "deel", "Deel", True, True,
           "Same"),
    Target(Source.WORKABLE, "buffer", "Buffer", True, True,
           "Publishes every salary"),
    Target(Source.WORKABLE, "doist", "Doist", True, True,
           "Sf-rate benchmark, global"),
    Target(Source.LEVER, "posthog", "PostHog", True, True,
           "Handbook, transparent bands"),

    # --- Tier 2: global remote, likely SF band ---
    Target(Source.GREENHOUSE, "canonical", "Canonical", True, False,
           "Ubuntu, global remote"),
    Target(Source.ASHBY, "huggingface", "Hugging Face", True, False,
           "Global remote, strong bands"),
    Target(Source.ASHBY, "supabase", "Supabase", True, False,
           "Global remote"),
    Target(Source.ASHBY, "replit", "Replit", False, False,
           "Some roles global, many US"),
    Target(Source.GREENHOUSE, "vercel", "Vercel", False, False,
           "Mostly US/EU; check per role"),
    Target(Source.ASHBY, "prisma", "Prisma", True, False,
           "EU-leaning, some global"),
    Target(Source.GREENHOUSE, "grafana", "Grafana Labs", False, False,
           "Country list, verify"),
    Target(Source.GREENHOUSE, "elastic", "Elastic", False, False,
           "Country list"),
    Target(Source.GREENHOUSE, "hashicorp", "HashiCorp", False, False,
           "Country list"),
    Target(Source.LEVER, "linear", "Linear", False, False,
           "Global remote, senior heavy"),
    Target(Source.ASHBY, "clickhouse", "ClickHouse", True, False,
           "Global"),

    # --- Tier 3: AI/ML labs (Kelyn's domain intersection) ---
    Target(Source.ASHBY, "anthropic", "Anthropic", False, False,
           "Mostly SF/Zurich but check ML roles"),
    Target(Source.GREENHOUSE, "openai", "OpenAI", False, False,
           "SF-heavy, some remote"),
    Target(Source.ASHBY, "scale", "Scale AI", False, False,
           "SF-heavy"),
    Target(Source.GREENHOUSE, "cohere", "Cohere", False, False,
           "TO / London / SF"),
    Target(Source.ASHBY, "mistral", "Mistral", False, False,
           "EU"),

    # --- Tier 3b: research-engineering employers (ADR 0021) ---
    # Research engineering on both sides of the candidate's background:
    # AI/ML labs, and computational-biology outfits where the work is
    # building systems and models rather than wet-lab science. These
    # post on the same ATSes karani already reads, which is why this is
    # a target list and not a scraper.
    Target(Source.GREENHOUSE, "deepmind", "Google DeepMind", False, False,
           "London/global, research engineering"),
    Target(Source.GREENHOUSE, "allenai", "Allen Institute for AI", False,
           False, "Seattle, open research"),
    Target(Source.ASHBY, "perplexityai", "Perplexity", False, False, "SF"),
    Target(Source.GREENHOUSE, "together", "Together AI", False, False,
           "distributed"),
    Target(Source.ASHBY, "runwayml", "Runway", False, False, "NY/remote"),
    Target(Source.GREENHOUSE, "databricks", "Databricks", False, False,
           "research + platform, sponsors"),
    # Computational biology — systems and modelling roles.
    Target(Source.GREENHOUSE, "recursionpharmaceuticals", "Recursion", False,
           False, "ML on cellular imaging at scale"),
    Target(Source.LEVER, "insitro", "insitro", False, False,
           "ML-driven drug discovery"),
    Target(Source.GREENHOUSE, "isomorphiclabs", "Isomorphic Labs", False,
           False, "London, AlphaFold lineage, sponsors"),
    Target(Source.LEVER, "deepgenomics", "Deep Genomics", False, False,
           "Toronto, RNA therapeutics ML"),
    Target(Source.GREENHOUSE, "benevolentai", "BenevolentAI", False, False,
           "London/EU, sponsors"),
    Target(Source.GREENHOUSE, "arcinstitute", "Arc Institute", False, False,
           "Palo Alto, research engineering"),

    # --- Tier 4: EU / Japan companies known to sponsor relocation ---
    # (Kelyn accepts relocation with visa sponsorship; EU and Japan are
    # the preferred destinations.)
    Target(Source.LEVER, "spotify", "Spotify", False, False,
           "EU, relocates to Stockholm"),
    Target(Source.GREENHOUSE, "datadog", "Datadog", False, False,
           "Paris/EU eng hubs, sponsors"),
    Target(Source.GREENHOUSE, "mercari", "Mercari", False, False,
           "Tokyo, strong relocation program"),
    Target(Source.GREENHOUSE, "wise", "Wise", False, False,
           "London/Tallinn, sponsors visas"),

    # --- Tier 5: Africa-native scale-ups (regional pay, keep for optionality) ---
    Target(Source.GREENHOUSE, "flutterwave", "Flutterwave", True, False,
           "Kenya-friendly"),
    Target(Source.LEVER, "mkopa", "M-KOPA", True, False, "Nairobi HQ"),
    Target(Source.LEVER, "sunking", "Sun King", True, False, "Nairobi"),
    Target(Source.GREENHOUSE, "wasoko", "Wasoko", True, False, "Kenya-native"),
]


# Sources without per-company slugs (feed-based)
FEED_SOURCES: list[Source] = [
    Source.REMOTEOK,
    Source.HIMALAYAS,
    Source.WEWORKREMOTELY,
    Source.REMOTIVE,
    Source.AIJOBS,
]


def active_targets() -> list[Target]:
    return TARGETS


def parity_targets() -> list[Target]:
    """Targets we've verified as global + pay-parity — highest signal."""
    return [t for t in TARGETS if t.verified_pay_parity and t.verified_global_hire]
