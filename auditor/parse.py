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

from bs4 import BeautifulSoup, NavigableString

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
    is_noindex: bool = False  # <meta name=robots content=...noindex...>
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


# Block-level elements. Text from two different blocks is NOT one sentence, so a boundary must
# survive into ``visible_text`` — otherwise an <h2> runs straight into the <p> beneath it
# ("Residential Rehab Options Because emergency workers…") and every consumer sees a malformed
# sentence that isn't on the page. This was a real defect: it produced false "missing sentence
# break" findings, and it affects EVERY check that reads visible_text, not just the AI layer.
_BLOCK_TAGS = (
    "p", "div", "section", "article", "aside", "header", "footer", "nav", "main", "figure",
    "figcaption", "blockquote", "pre", "li", "ul", "ol", "dl", "dt", "dd", "table", "thead",
    "tbody", "tr", "td", "th", "form", "fieldset", "hr", "br",
    "h1", "h2", "h3", "h4", "h5", "h6",
)
_BLOCK_SET = frozenset(_BLOCK_TAGS)
_INLINE_WS = re.compile(r"[^\S\n]+")   # runs of spaces/tabs, but never newlines
_BLANK_LINES = re.compile(r"\s*\n\s*")


def _visible_text(soup) -> str:
    """Visible text with BLOCK BOUNDARIES PRESERVED as newlines.

    ``get_text(" ")`` (the original) joins every text node with a space, silently welding an <h2>
    onto the <p> beneath it — "Residential Rehab Options Because emergency workers…" — a sentence
    that is not on the page. Every consumer of ``visible_text`` saw that, not just the AI layer.
    ``get_text("\n")`` fixes the weld but over-splits, breaking inline markup mid-sentence
    ("Call <b>now</b>" -> "Call\nnow").

    So: one pass over ``descendants``, emitting a newline when a BLOCK-level element starts and
    nothing for inline elements. Measured on a 1,500-block page: 0.049s, vs 0.004s for the old
    (incorrect) get_text and **4.1s** for the obvious recursive version / **13.1s** for the
    insert_before/insert_after version whose bs4 tree mutation is O(n^2). At ~31k pages that
    difference is 25 minutes versus days, so the cheap traversal is load-bearing, not a micro-opt.

    Known limit: the newline marks a block's START, so bare text immediately following a block at
    the same level (``<p>A</p>Text``) still welds. That shape is rare in real page markup — text
    lives inside blocks — and avoiding it costs an exit-marker walk that measured 85x slower.
    """
    out: list[str] = []
    for el in soup.descendants:
        if isinstance(el, NavigableString):
            s = str(el)
            if s.strip():
                # A separator between two inline text nodes is needed ONLY to keep two words apart
                # ("Adderall addiction" + "Detox" must not fuse into "addictionDetox"). Adding one
                # unconditionally — as the original get_text(" ", strip=True) did — invents defects:
                # live GL ships "<strong>phone rings</strong>." and it came out as "phone rings .",
                # which the AI layer then correctly reported as a punctuation error that is not on
                # the page. So insert one only where both sides of the boundary are alphanumeric;
                # markup that already carries its own spacing, and any following punctuation, is
                # left exactly as authored — including a stray " ," the client really did type.
                if out and out[-1] != "\n" and out[-1][-1:].isalnum() and s[:1].isalnum():
                    out.append(" ")
                out.append(s)
        elif el.name in _BLOCK_SET:
            out.append("\n")
    text = _INLINE_WS.sub(" ", "".join(out))   # collapse spaces/tabs, keep \n
    text = _BLANK_LINES.sub("\n", text)       # one newline per boundary
    return text.strip()


def parse_html(html: str, page_url: str, base_url: str | None = None) -> ParsedPage:
    """Parse rendered HTML into the extraction primitives the M1 checks consume.

    Links + headings + title + meta are read from the full document; ``visible_text``
    is taken *after* ``strip_volatile`` so scans never see script/style/cfemail bytes.

    ``page_url`` is the page's IDENTITY (the requested/enumerated URL — what fingerprints,
    the cache, and the run-diff all key on). ``base_url`` is the URL to resolve relative
    links against (the FINAL/landing URL after redirects); defaults to ``page_url``. Splitting
    them keeps identity stable across redirects while resolving links against where the page
    actually lives.
    """
    base_url = base_url or page_url
    soup = BeautifulSoup(html, "lxml")

    title = soup.title.get_text(strip=True) if soup.title else None
    md = soup.find("meta", attrs={"name": "description"})
    meta_description = md.get("content").strip() if md and md.get("content") else None
    mr = soup.find("meta", attrs={"name": re.compile(r"^robots$", re.I)})
    is_noindex = bool(mr and "noindex" in (mr.get("content") or "").lower())

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
        absu = urljoin(base_url, href).split("#")[0]
        if not absu.lower().startswith("http") or absu in seen:
            continue
        seen.add(absu)
        links.append(Link(href=href, url=absu))

    strip_volatile(soup)  # normalize before extracting visible text
    visible_text = _visible_text(soup)

    return ParsedPage(
        url=page_url,
        title=title,
        meta_description=meta_description,
        is_noindex=is_noindex,
        headings=headings,
        links=links,
        visible_text=visible_text,
        raw_html=html,
    )
