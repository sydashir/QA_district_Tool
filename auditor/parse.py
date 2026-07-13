"""HTML parsing primitives (BeautifulSoup + lxml), reused from the session-1 spike.

Pure extraction only — no pass/fail judgement (that is the checks' job, M1). The M1
checks consume ``ParsedPage``. Kept on bs4 for now; only revisit selectolax if
RR-scale (~7.8k pages) perf actually demands it.

Cross-cutting helper A (see M1 brief): ``strip_volatile`` removes the per-request
volatile bits — Cloudflare ``data-cfemail`` / ``/cdn-cgi/`` email obfuscation and
``<script>``/``<style>``/``<noscript>`` — so that (1) the placeholder check scans
*visible* text only and (2) M2's content-hash diff hashes a stable representation.
Both MUST go through this one helper so they cannot drift; ``visible_text`` below is
already normalized, and ``stable_markup()`` is the string M2 should hash.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

from bs4 import BeautifulSoup

_HEADING_RE = re.compile(r"^h[1-6]$")

# Non-content / per-request-volatile nodes stripped before text + hashing.
_VOLATILE_TAGS = ("script", "style", "noscript", "template")
_CDN_CGI_EMAIL = "/cdn-cgi/l/email-protection"


@dataclass
class Link:
    href: str  # raw href attribute
    url: str  # absolute, fragment-stripped


@dataclass
class Heading:
    level: int
    text: str


@dataclass
class ParsedPage:
    url: str
    title: str | None = None
    meta_description: str | None = None
    headings: list[Heading] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    visible_text: str = ""  # normalized: script/style/cfemail stripped
    raw_html: str = ""


def strip_volatile(soup: BeautifulSoup) -> BeautifulSoup:
    """Remove non-content + per-request-volatile nodes IN PLACE and return the soup.

    Shared by the placeholder check (visible-text scan) and M2's content-hash diff so
    the two can never drift. Handles the exact Cloudflare noise the spike + M0 hit:
    ``<script>`` guards that embed literal ``[acf field=...]`` (spike false positive)
    and rotating ``data-cfemail`` / ``/cdn-cgi/l/email-protection`` tokens (M0 hash churn).
    """
    for tag in soup.find_all(_VOLATILE_TAGS):
        tag.decompose()
    for a in soup.find_all("a", href=True):
        if _CDN_CGI_EMAIL in a["href"]:
            a.decompose()
    for el in soup.find_all(attrs={"data-cfemail": True}):
        del el["data-cfemail"]
    return soup


def stable_markup(html: str) -> str:
    """Normalized HTML string for stable hashing/diffing (M2 consumes this)."""
    return str(strip_volatile(BeautifulSoup(html, "lxml")))


def parse_html(html: str, page_url: str) -> ParsedPage:
    """Parse rendered HTML into the extraction primitives the M1 checks consume.

    Links + headings + title + meta are read from the full document; ``visible_text``
    is taken *after* ``strip_volatile`` so scans never see script/style/cfemail bytes.
    """
    soup = BeautifulSoup(html, "lxml")

    title = soup.title.get_text(strip=True) if soup.title else None
    md = soup.find("meta", attrs={"name": "description"})
    meta_description = md.get("content").strip() if md and md.get("content") else None

    headings = [
        Heading(int(h.name[1]), h.get_text(" ", strip=True))
        for h in soup.find_all(_HEADING_RE)
    ]

    links: list[Link] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        absu = urljoin(page_url, href).split("#")[0]
        if not absu.lower().startswith("http") or absu in seen:
            continue
        seen.add(absu)
        links.append(Link(href=href, url=absu))

    strip_volatile(soup)  # normalize before extracting visible text
    visible_text = soup.get_text(" ", strip=True)

    return ParsedPage(
        url=page_url,
        title=title,
        meta_description=meta_description,
        headings=headings,
        links=links,
        visible_text=visible_text,
        raw_html=html,
    )
