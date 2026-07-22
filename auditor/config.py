"""Brand configuration models (pydantic) and the TOML loader.

One TOML file per brand lives in ``config/`` (e.g. ``config/gl.toml``). Values are
the real per-brand facts recorded in CLAUDE.md §6.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from .nap import CanonicalNumbers, brand_number_owners, canon_for, load_third_party

# The browser-like User-Agent the session-1 spike proved works against GL's
# Cloudflare without being challenged. Reused verbatim (see spike/gl_spike.py).
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 DistrictSiteAuditor/0.1"
)

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


class CrawlRules(BaseModel):
    """Polite-crawl settings. Defaults are the values the spike proved work on GL."""

    max_concurrency: int = 5
    delay_seconds: float = 0.25
    timeout_seconds: float = 20.0
    # Link probing splits the budget by host scope: internal links (real 404s -> real findings)
    # get the full timeout above; EXTERNAL hosts get this short one. A citation host that hasn't
    # answered in 5s won't, and we classify it `unverified` regardless of the exact status, so
    # waiting the full 20s (x2 with the GET retry) just serializes the probe tail behind slow gov
    # servers. GL's link probe took 108min/400 links before this split; 5s bounds it.
    external_link_timeout_seconds: float = 5.0
    max_retries: int = 1
    user_agent: str = DEFAULT_USER_AGENT
    # URL substrings to exclude from enumeration (jake_doc crawl.exclude).
    exclude: list[str] = Field(default_factory=lambda: ["/wp-admin", "?"])


class WPRestConfig(BaseModel):
    """WordPress REST enumeration fallback, used when the public sitemap is
    blocked/empty. GL's ``/wp-json/wp/v2`` is publicly readable, so auth is
    optional: Basic (Application-Password) auth is applied only if
    ``username_env``/``password_env`` are set AND present in the environment.
    Credentials are never stored in this repo.
    """

    enabled: bool = True
    base_url: str  # site root, e.g. https://www.gratitudelodge.com
    post_types: list[str] = Field(default_factory=lambda: ["pages", "posts"])
    username_env: str | None = None
    password_env: str | None = None


class Thresholds(BaseModel):
    """Per-brand tunable check thresholds. Hashed as a CONFIG component (not source), so a
    per-brand tune moves only that brand's check-version — no cross-brand churn (the same
    pattern as canonical_phones). Only title bounds live here today; other constants move
    here when a brand actually needs a divergent value (P4 / ARCHITECTURE §B). Defaults are
    the generic values; per-brand TOML overrides (GL: title_max = 88)."""

    title_min: int = 15
    title_max: int = 70


class BrandConfig(BaseModel):
    brand: str  # short code, e.g. "GL"
    name: str  # display name
    base_url: str
    sitemap_url: str
    canonical_phones: list[str]  # flat national numbers (TOML); legacy + fallback ruler
    crawl: CrawlRules = Field(default_factory=CrawlRules)
    wp_rest: WPRestConfig | None = None
    thresholds: Thresholds = Field(default_factory=Thresholds)
    # NAP-derived canonical (national + per_location + stale_retired). Populated by
    # load_brand from the 2026-07-02 snapshot; None if unavailable. When present, the
    # phone check classifies clean / stale-retired / unknown instead of flat non-canonical.
    canon: CanonicalNumbers | None = None
    # Global third-party hotlines (Poison Control/SAMHSA/Lifeline/RAINN) — expected, not defects.
    third_party: set[str] = Field(default_factory=set)
    # Global map: live number -> owning brand(s). Splits benign call-tracking (dials own number)
    # from cross-brand leaks (dials another brand's number).
    brand_numbers: dict[str, list[str]] = Field(default_factory=dict)

    @field_validator("base_url", "sitemap_url")
    @classmethod
    def _absolute_http(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError(f"URL must be absolute http(s): {v!r}")
        return v.rstrip("/") if v.endswith("/") and "sitemap" not in v else v


def load_brand_config(path: str | Path) -> BrandConfig:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"brand config not found: {p}")
    with p.open("rb") as fh:
        data = tomllib.load(fh)
    return BrandConfig(**data)


def load_brand(brand: str) -> BrandConfig:
    """Load ``config/<brand>.toml`` by brand code (case-insensitive) and attach the
    NAP-derived canonical numbers (2026-07-02 snapshot; NAP_SHEET_ID unverified)."""
    cfg = load_brand_config(CONFIG_DIR / f"{brand.lower()}.toml")
    cfg.canon = canon_for(cfg.brand)
    cfg.third_party = load_third_party()
    cfg.brand_numbers = brand_number_owners()
    return cfg
