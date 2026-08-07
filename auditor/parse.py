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

from bs4 import BeautifulSoup, CData, Comment, Declaration, Doctype, NavigableString, ProcessingInstruction

_HEADING_RE = re.compile(r"^h[1-6]$")
# Class names that mean "JavaScript opens something when this is clicked". A dropdown toggle has no
# href by design, and treating one as a broken button is how a dead-link check cries wolf.
_DROPDOWN_CLASSES = frozenset({
    "dropbtn", "dropdown", "dropdown-toggle", "submenu", "sub-menu", "has-children",
    "menu-item-has-children", "expander", "disclosure"})

# Non-content / per-request-volatile nodes stripped before text + hashing.
_VOLATILE_TAGS = ("script", "style", "noscript", "template")
# Block-level units worth reasoning about individually. Deliberately NOT <div>: divs nest without
# limit, so collecting them yields the same sentence at five levels of depth.
# NOTE the name: `_BLOCK_TAGS` already exists further down for visible-text newline separation.
# Shadowing it made _blocks() match every <div>/<header> and inflated body_text past visible_text.
_TEXT_BLOCK_TAGS = ("p", "li", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6",
                    "dd", "dt", "blockquote", "figcaption")
_TEXT_BLOCK_SET = frozenset(_TEXT_BLOCK_TAGS)
# Content that occupies a slot without contributing text — an icon cell is populated, not empty.
_MEDIA_TAGS = frozenset({"img", "svg", "picture", "video", "iframe", "canvas", "object",
                         "embed", "input", "select", "textarea"})
# Page furniture. A brand named in here is boilerplate; the same name in body copy is content.
_REGION_TAGS = ("nav", "header", "footer", "aside")
# WordPress/Elementor mark furniture with classes far more often than with landmark elements, so
# both are needed — COC's footer is a <div class="site-footer">, not a <footer>.
# BARE "header"/"footer" are deliberately absent. `page-header`, `entry-header`, `section-header`
# and `elementor-heading-title` are CONTENT, and matching them classified a body <h2> as furniture
# — which silently shrank body text for every check reading it. COC's archived page hid its
# "California Detox" heading that way. Only the site-furniture compounds are matched here; the
# real <header>/<footer>/<nav> landmark elements are handled separately and are reliable.
_REGION_CLASS = re.compile(
    r"(?:^|\s)(?:"
    r"(?:site|main|global|primary|sticky|top|bottom)[_-](?:header|footer|nav|navigation|menu|bar)"
    r"|masthead|colophon|navbar|nav-?menu|mega-?menu|breadcrumbs?|sidebar|widget-area"
    r")(?:\s|$)", re.IGNORECASE)
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
class Actionable:
    """A thing a visitor can click — `<a>` (WITH OR WITHOUT an href) or `<button>`.

    Links are collected separately and deliberately require an href. That is correct for link
    checking and was a BLIND SPOT for everything else: `find_all("a", href=True)` discarded every
    hrefless anchor before any check could see it, so a dead button — the single most-reported
    defect in the client's own attachments, 12 times — was structurally invisible to this tool.
    """
    tag: str                      # "a" | "button"
    text: str
    href: str | None              # None when the attribute is absent entirely
    classes: tuple[str, ...] = ()
    role: str = ""
    aria_label: str = ""
    # attributes that mean JavaScript drives this element, so a "#" href is not proof of a dead
    # control: popup/modal/tab/accordion triggers all legitimately look like that in the markup.
    has_js_hooks: bool = False
    in_nav: bool = False
    has_submenu: bool = False
    # Same region/group vocabulary as Block. A repeated CTA down a long page is normal design; the
    # SAME link twice inside ONE list is the duplicate the client reported, and only the container
    # tells those apart.
    region: str = "body"
    group: int = 0


@dataclass
class Block:
    """One block-level unit of visible text, with the page region it belongs to.

    Added in ONE batched change (see CLAUDE.md: parse.py is `_GLOBAL_SRC`, so every edit costs a
    full nine-brand re-crawl) to serve three checks at once:

    * **region** — separates BODY COPY from nav/header/footer boilerplate. A sister brand named in
      a footer network list is intentional; the same name in body copy is the defect Connor
      reported ("Gratitude Lodge" on the Connections site).
    * **text + group** — a repeated paragraph inside one page, and an EMPTY slot sitting beside
      populated siblings, are both statements about a block and the group it belongs to.
    """
    tag: str                      # p, li, td, h2, ...
    text: str                     # normalized visible text of this block alone
    region: str                   # "body" | "nav" | "header" | "footer" | "aside"
    group: int                    # id of the parent container — siblings share it
    # An empty <td> holding an icon is populated; an empty <td> holding nothing is a missing value.
    # Without this the empty-slot check cannot tell them apart and would flag every icon cell.
    has_media: bool = False


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
    actionables: list["Actionable"] = field(default_factory=list)
    blocks: list["Block"] = field(default_factory=list)
    # Whether the CSS needed to know what is HIDDEN was actually readable. "ok" (or "none" — the
    # page links no stylesheets) means visible_text is trustworthy. "partial"/"unavailable" means
    # display:none rules may have been missed, so hidden text can still be in visible_text and any
    # finding derived from it is lower-confidence. Same principle as withholding coverage findings
    # on a partial sitemap read: a partial read must never produce a confident claim.
    css_status: str = "none"

    @property
    def body_text(self) -> str:
        """Visible text with nav/header/footer boilerplate removed.

        Falls back to the whole page when no block carries a body region, so a page built without
        recognisable landmarks degrades to today's behaviour instead of silently going empty.
        """
        body = "\n".join(b.text for b in self.blocks if b.region == "body" and b.text)
        return body or self.visible_text


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


def _region_map(soup) -> dict[int, str]:
    """id(element) -> region name, for every element inside page furniture.

    Top-down: find the furniture roots once and mark their subtrees, rather than walking ancestors
    from each of the ~400 blocks on a page. Outermost root wins, so a <nav> inside a <footer> is
    reported as footer — which is what a reader would call it.
    """
    marked: dict[int, str] = {}
    for el in soup.find_all(_REGION_TAGS):
        region = el.name if el.name != "aside" else "aside"
        for d in el.find_all(True):
            marked.setdefault(id(d), region)
        marked.setdefault(id(el), region)
    for el in soup.find_all(attrs={"class": _REGION_CLASS}):
        if id(el) in marked:
            continue
        m = _REGION_CLASS.search(" ".join(el.get("class") or ()))
        word = (m.group(0) if m else "").lower()
        region = ("footer" if "footer" in word or "colophon" in word
                  else "header" if "header" in word or "masthead" in word
                  else "aside" if "sidebar" in word or "widget-area" in word else "nav")
        for d in el.find_all(True):
            marked.setdefault(id(d), region)
        marked.setdefault(id(el), region)
    return marked


def _blocks(soup) -> list["Block"]:
    """Leaf block-level text units with their page region.

    LEAF only — an element containing another block tag is a container, and collecting it as well
    would report the same sentence twice (once as the <li>, once as the <p> inside it).
    """
    regions = _region_map(soup)
    groups: dict[int, int] = {}
    out: list[Block] = []
    for el in soup.find_all(_TEXT_BLOCK_TAGS):
        kids = el.find_all(True, recursive=True)
        if any(c.name in _TEXT_BLOCK_SET for c in kids):
            continue                      # a container, not a leaf
        parent = el.parent
        pid = id(parent) if parent is not None else 0
        group = groups.setdefault(pid, len(groups))
        out.append(Block(
            tag=el.name,
            text=" ".join((el.get_text(" ", strip=True) or "").split()),
            region=regions.get(id(el), "body"),
            group=group,
            has_media=any(c.name in _MEDIA_TAGS for c in kids)))
    return out


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
# NavigableString subclasses that are markup, not words a reader sees.
_NON_TEXT = (Comment, CData, Doctype, ProcessingInstruction, Declaration)
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


def _strip_hidden(soup, extra_css: str = "") -> None:
    """Remove elements the rendered page does not show. Mutates ``soup`` in place.

    ``extra_css`` is stylesheet text fetched from <link rel=stylesheet> — the rules that hide the
    live GL `.geo-topic` chips live there, not in an inline <style>, so inline-only stripping left
    text on the page that a browser renders as nothing at all.
    """
    # find_all, NOT soup.select("[hidden]"): the CSS attribute selector goes through soupsieve and
    # measured 633ms on a 4,334-element RR page, versus a few ms for bs4's native attribute scan.
    for el in soup.find_all(attrs={"hidden": True}):
        el.decompose()
    for el in soup.find_all(style=_HIDDEN_INLINE):
        el.decompose()

    selectors: list[str] = []
    seen: set[str] = set()
    sources = [st.get_text() or "" for st in soup.find_all("style")]
    if extra_css:
        sources.append(extra_css)
    for raw_css in sources:
        css = _strip_at_blocks(raw_css)
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
    # The parent guard matters: decomposing an ancestor orphans elements still in this list, and
    # calling decompose() on an orphan raises — which previously aborted the whole batch and left
    # hidden content in visible_text. Skip what a prior decompose already removed.
    try:
        for el in soup.select(", ".join(complex_)):
            if el.parent is not None:
                el.decompose()
    except Exception:
        for sel in complex_:  # one unparseable selector must not lose the whole batch
            try:
                for el in soup.select(sel):
                    if el.parent is not None:
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
            # bs4's Comment/CData/Doctype/ProcessingInstruction are all SUBCLASSES of
            # NavigableString, so a naive descendants walk reads them as page copy — get_text()
            # excludes them for exactly this reason. Commented-out markup, developer TODOs and
            # disabled script blocks were reaching visible_text and being counted as content.
            if isinstance(el, _NON_TEXT):
                continue
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


def parse_html(html: str, page_url: str, base_url: str | None = None,
               extra_css: str = "", css_status: str = "none") -> ParsedPage:
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

    actionables: list[Actionable] = []
    # Computed here rather than shared with _blocks(): _strip_hidden() runs between the two and
    # decomposes elements, so ids from a map built now could be recycled by later objects.
    act_regions = _region_map(soup)
    act_groups: dict[int, int] = {}
    for el in soup.find_all(["a", "button"]):
        raw_href = el.get("href")
        classes = tuple(el.get("class") or ())
        attrs = el.attrs
        js_hook = bool(
            el.get("onclick") or el.get("aria-controls") or el.get("aria-haspopup")
            or el.get("data-toggle") or el.get("data-bs-toggle") or el.get("data-target")
            or any(k.startswith("data-elementor") or k.startswith("data-popup") for k in attrs)
            or any("popup" in c or "modal" in c or "toggle" in c or "accordion" in c or "tab" in c
                   for c in classes)
            or any(c.lower() in _DROPDOWN_CLASSES for c in classes))
        # A disclosure control — mega-menu parent, accordion header — has no destination of its own
        # because opening the container that follows IS its job. GL builds these as
        # `<a class="dropbtn">` with a sibling <div>, NOT the <li><a>+<ul> the first version assumed,
        # so key on the structure that is actually true: a container of links immediately after.
        # TWO or more links, because a menu has items; one following link is not a menu.
        sib = el.find_next_sibling(["ul", "div", "nav"])
        submenu = bool(sib and len(sib.find_all("a", href=True)) >= 2)
        actionables.append(Actionable(
            tag=el.name,
            text=" ".join((el.get_text(" ", strip=True) or "").split())[:120],
            href=raw_href.strip() if isinstance(raw_href, str) else None,
            classes=classes,
            role=(el.get("role") or ""),
            aria_label=(el.get("aria-label") or el.get("title") or ""),
            has_js_hooks=js_hook,
            in_nav=bool(el.find_parent(["nav", "header"])),
            has_submenu=submenu,
            region=act_regions.get(id(el), "body"),
            group=act_groups.setdefault(id(el.parent) if el.parent is not None else 0,
                                        len(act_groups)),
        ))

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
    _strip_hidden(soup, extra_css)   # drop what the rendered page does not show
    strip_volatile(soup)  # then normalize away script/style/cfemail noise
    visible_text = _visible_text(soup)
    # AFTER the hidden/volatile strip, so blocks describe what a reader actually sees — the same
    # discipline visible_text follows, and the reason the hidden-span bug was a correctness issue.
    blocks = _blocks(soup)

    return ParsedPage(
        url=page_url,
        actionables=actionables,
        blocks=blocks,
        title=title,
        meta_description=meta_description,
        is_noindex=is_noindex,
        headings=headings,
        links=links,
        visible_text=visible_text,
        raw_html=html,
    )
