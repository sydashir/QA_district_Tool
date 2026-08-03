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

# --- hidden content ------------------------------------------------------------------------
# Text the user cannot see must not reach ``visible_text``. Live GL ships
#     <style>.geo-topic a span{display:none}</style>
#     <a href="…">Adderall addiction<span>Detox</span></a>
# where the span is a hover-only label. Extracting it produced "addictionDetox" — originally
# mis-diagnosed as a missing separator and "fixed" by inserting a space, which only changed the
# fabricated text to "addiction Detox". The defect was reading hidden content at all.
#
# This is not only an AI-layer concern: ``visible_text`` is what the blank/thin-section check
# measures, so hidden text can make an EMPTY section look populated — the exact failure the client
# asked us to catch (blank ACF sections).
_HIDDEN_INLINE = re.compile(r"(display\s*:\s*none|visibility\s*:\s*hidden)", re.IGNORECASE)
_CSS_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_AT_BLOCK = re.compile(r"@[a-zA-Z-]+[^{]*\{")


def _strip_at_blocks(css: str) -> str:
    """Drop @media/@supports blocks. A rule inside one hides content only at some breakpoints —
    a mobile menu is real copy on desktop — so honouring it would delete visible text."""
    out, i = [], 0
    while i < len(css):
        m = _AT_BLOCK.search(css, i)
        if not m:
            out.append(css[i:])
            break
        out.append(css[i:m.start()])
        depth, j = 1, m.end()
        while j < len(css) and depth:
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
            j += 1
        i = j
    return "".join(out)


_SIMPLE_CLASS = re.compile(r"^\.([A-Za-z0-9_-]+)$")


def _strip_hidden(soup) -> None:
    """Remove elements the rendered page does not show. Mutates ``soup`` in place."""
    # find_all, NOT soup.select("[hidden]"): the CSS attribute selector goes through soupsieve and
    # measured 633ms on a 4,334-element RR page, versus a few ms for bs4's native attribute scan.
    for el in soup.find_all(attrs={"hidden": True}):
        el.decompose()
    for el in soup.find_all(style=_HIDDEN_INLINE):
        el.decompose()

    selectors: list[str] = []
    seen: set[str] = set()
    for style in soup.find_all("style"):
        css = _strip_at_blocks(style.get_text() or "")
        for m in _CSS_RULE.finditer(css):
            if _HIDDEN_INLINE.search(m.group(2)):
                for sel in m.group(1).split(","):
                    sel = sel.strip()
                    # ":hover"/"::after" state and pseudo-element rules do not hide static content,
                    # and soupsieve cannot evaluate them anyway.
                    if sel and sel not in seen and ":" not in sel:
                        seen.add(sel)
                        selectors.append(sel)
    if not selectors:
        return

    # PRE-FILTER, because this runs on ~31k pages. A WordPress/Elementor page ships bundled library
    # CSS (swiper, lightbox) whose display:none rules reference classes that are not on the page at
    # all, yet soupsieve still walks the tree for each one — measured 1.65s for 68 selectors over
    # 3,904 elements. A selector that names a class or id absent from the document provably matches
    # nothing, so it can be dropped without evaluating it.
    by_class: dict[str, list] = {}
    present: set[str] = set()
    for el in soup.find_all(True):
        cls = el.get("class")
        if cls:
            present.update(cls)
            for c in cls:
                by_class.setdefault(c, []).append(el)
        el_id = el.get("id")
        if el_id:
            present.add("#" + el_id)

    simple, complex_ = [], []
    for sel in selectors:
        if sel == "[hidden]" or sel in _VOLATILE_TAGS:
            continue  # already removed above / by strip_volatile
        names = re.findall(r"\.([A-Za-z0-9_-]+)", sel)
        ids = re.findall(r"#([A-Za-z0-9_-]+)", sel)
        if not (all(n in present for n in names) and all("#" + i in present for i in ids)):
            continue  # cannot match this document — never hand it to soupsieve
        m = _SIMPLE_CLASS.match(sel)
        if m:
            simple.append(m.group(1))
        else:
            complex_.append(sel)

    # A bare ".class" needs no CSS engine — the index above already answers it. This matters
    # because soupsieve is the dominant cost here (~600-700ms/page on RR and GL).
    for cls in simple:
        for el in by_class.get(cls, ()):
            if el.parent is not None:
                el.decompose()
    if not complex_:
        return
    try:
        for el in soup.select(", ".join(complex_)):
            el.decompose()
    except Exception:
        for sel in complex_:  # one unparseable selector must not lose the whole batch
            try:
                for el in soup.select(sel):
                    el.decompose()
            except Exception:
                continue


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
            if not s.strip():
                # A WHITESPACE-ONLY node is real spacing in the markup — "<strong>details here:</strong>
                # <a>Outpatient</a>" renders with a space. Dropping it fused those into
                # "here:Outpatient", because ":" is not alphanumeric so no separator was re-inserted.
                if out and out[-1] != "\n":
                    out.append(" ")
                continue
            # Otherwise a separator is needed ONLY to keep two words apart ("Adderall addiction" +
            # "Detox" must not fuse). Adding one unconditionally — as the original
            # get_text(" ", strip=True) did — invents defects: live GL ships
            # "<strong>phone rings</strong>." and it came out as "phone rings .", which the AI layer
            # then correctly reported as a punctuation error that is not on the page. So insert one
            # only where both sides of the boundary are alphanumeric; markup that carries its own
            # spacing, and any following punctuation, is left exactly as authored — including a
            # stray " ," the client really did type.
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

    # ORDER MATTERS: _strip_hidden reads <style> rules, and strip_volatile deletes <style>.
    _strip_hidden(soup)   # drop what the rendered page does not show
    strip_volatile(soup)  # then normalize away script/style/cfemail noise
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
