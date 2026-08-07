"""Clickable things that do not work (v1 deterministic) — the client's most-reported defect.

Two classes, both about an element a visitor will click:

**dead_cta** — a call-to-action with nothing behind it. Reported TWELVE times across the seven
attachments on ClickUp 86baawd2a ("the Verify Your Insurance button doesn't actually take the user
anywhere", "Apply Now and Learn More links don't work", "Get in Touch doesn't work"), and still live
on districtbehavioralhealth.com eight weeks later. It was invisible to this tool because
`parse.py` collected links with `find_all("a", href=True)` — a hrefless anchor never reached a check.

**social_misrouted** — a social icon wired to the wrong network. RR's footer has the LinkedIn AND
YouTube icons both pointing at Instagram; CAD's LinkedIn href has a YouTube URL welded inside it.
Structurally the same check as `cross_brand_dial`: the label says one thing, the destination says
another. Nobody clicks their own footer, so these survive indefinitely.

**Precision is the entire design problem.** GL's `/locations/` page alone carries 33 hrefless
anchors that are legitimate mega-menu toggles, and this network's real CTAs open Elementor popups
through `href="#elementor-action%3A…"`. A naive "missing href" rule would cry wolf on the one class
the client cares most about. Three conditions must ALL hold: the element looks like a
call-to-action, it has no destination, and nothing indicates JavaScript drives it.
"""
from __future__ import annotations

import re
from collections import Counter
from urllib.parse import urlparse

from ..parse import ParsedPage
from ..report import Finding, Severity, make_fingerprint

CHECK = "actions"

# Verbs and phrases that mark an element as a call to action. Taken from the buttons the client
# actually reported, plus the obvious siblings.
_CTA_TEXT = re.compile(
    r"^\s*(?:"
    r"verify|apply|call|get\s+in\s+touch|get\s+started|get\s+help|learn\s+more|read\s|"
    r"view\s|see\s+(?:our|all|centers)|contact|start|begin|book|schedule|check\s|download|"
    r"submit|send|request|enroll|join|find\s+(?:a|out|help)|talk\s+to|speak|reach|tour|explore|"
    r"claim|register|sign\s+up|subscribe|donate"
    r")", re.IGNORECASE)
# NOT in the list: "admissions". It reads as an action but on this network it is a menu-label
# NOUN — RR's "Admissions Resources" column heading, GL's "Admissions" dropdown. Every occurrence
# measured was structural, none was a defect.
# Class tokens that mean "this is styled as a button".
_BUTTON_CLASS = re.compile(r"(?:^|[\s_-])(btn|button|cta)(?:$|[\s_-])", re.IGNORECASE)
# Hrefs that go nowhere at all. "#section" and "#elementor-action…" are NOT here: the first is an
# in-page jump and the second is how this network opens its popups.
_DEAD_HREFS = {"", "#"}
_MAX_CTA_WORDS = 6          # a CTA is a short label; a sentence in an <a> is a text link

_SOCIAL_HOSTS = {
    "facebook": "facebook", "fb": "facebook",
    "instagram": "instagram",
    "linkedin": "linkedin",
    "youtube": "youtube", "youtu": "youtube",
    "twitter": "twitter", "x": "twitter",
    "tiktok": "tiktok",
    "pinterest": "pinterest",
}
_SOCIAL_WORD = re.compile(
    r"(facebook|instagram|linked\s?-?in|youtube|twitter|tiktok|pinterest)", re.IGNORECASE)


def _looks_like_cta(a, strict: bool) -> bool:
    """Is this element a call to action?

    `strict` demands an action phrase in the text, and is used when the element has NO href at
    all. Measured on live pages, the two cases carry very different evidence:

    * `href="#"` — somebody authored a link and it goes nowhere. Broken, whatever it says.
      districtbehavioralhealth.com's homepage has 17 of these today.
    * no href — ambiguous. It is equally the shape of a decorative chip. TDRC's homepage styles
      14 amenity labels ("Paintball", "Hiking", "Nutrition Plans") as `elementor-button`s with no
      href; they were never links. Connor asked about exactly these — *"are these sections supposed
      to be clickable?"* — and that question is intent, which this tool does not answer.

    So button STYLING alone is not enough when the href is absent; the words have to promise an
    action. That keeps CAD's hrefless "Verify Insurance" and drops all 14 of TDRC's.
    """
    if not (a.text or "").strip():
        return False
    if len(a.text.split()) > _MAX_CTA_WORDS:
        return False                       # a sentence is a text link, not a button
    if strict:
        return bool(_CTA_TEXT.match(a.text))
    if a.role == "button" or any(_BUTTON_CLASS.search(c) for c in a.classes):
        return True
    return bool(_CTA_TEXT.match(a.text))


def _is_dead(a) -> bool:
    """No destination AND no sign that JavaScript supplies one."""
    if a.has_js_hooks or a.role in ("tab", "menuitem"):
        return False
    if a.has_submenu:
        # A menu parent: opening the submenu IS its job. Not conditioned on being inside <nav> —
        # GL renders its mega-menu in plain <div>s, and requiring <nav> made 21 legitimate toggles
        # on /locations/ look like broken buttons.
        return False
    href = a.href
    if href is None:
        return True                        # the attribute is absent entirely
    href = href.strip()
    if href.lower().startswith("javascript:"):
        return True
    if href.startswith("#"):
        # "#" alone goes nowhere; "#anything" is an in-page jump or a popup action
        return href in _DEAD_HREFS
    return href in _DEAD_HREFS


def _network_from_host(url: str) -> str:
    host = (urlparse(url).netloc or "").lower()
    for part in host.replace("www.", "").split("."):
        if part in _SOCIAL_HOSTS:
            return _SOCIAL_HOSTS[part]
    return ""


def _network_from_label(a) -> str:
    hay = " ".join([a.aria_label or "", " ".join(a.classes), a.text or ""])
    m = _SOCIAL_WORD.search(hay)
    if not m:
        return ""
    return _SOCIAL_HOSTS.get(re.sub(r"[\s-]", "", m.group(1).lower()).replace("linkedin", "linkedin"),
                             re.sub(r"[\s-]", "", m.group(1).lower()))


def run(parsed: ParsedPage, config) -> list[Finding]:
    findings: list[Finding] = []
    seen: Counter = Counter()

    for a in getattr(parsed, "actionables", None) or ():
        # --- dead call-to-action ---
        # Anchors only. A <button> has no href by design — what it does lives in JavaScript, which
        # this tool does not execute, so "dead" is not decidable from the HTML. GL's form Submit,
        # COC's `relatedloadMoreBtn` and a modal's "x" all reached this check as <button>s and all
        # three work. Stated as a boundary in the coverage doc rather than guessed at.
        # `strict` inside nav as well as when the href is absent: a menu is full of anchors that
        # were never destinations. RR's "Admissions Resources" is `<a href="#">` inside
        # `<div class="menu_col_title">` — a mega-menu COLUMN HEADING, not a broken button. The
        # defects the client reported are body CTAs, and those still carry an action phrase.
        if a.tag == "a" and _is_dead(a) and _looks_like_cta(a, strict=a.href is None or a.in_nav):
            label = a.text or a.aria_label or "(unlabelled)"
            key = ("dead_cta", label.lower())
            occ = seen[key]
            seen[key] += 1
            slot = "dead_cta" if occ == 0 else f"dead_cta#{occ}"
            findings.append(Finding(
                url=parsed.url, check=CHECK, severity=Severity.ERROR,
                fingerprint=make_fingerprint(CHECK, slot, parsed.url, label.lower()),
                issue=f"button goes nowhere: \"{label}\"",
                location="page body",
                snippet=label,
                suggestion=(f"The \"{label}\" button has no destination, so clicking it does "
                            f"nothing. Point it at the right page. If it is meant to open a popup "
                            f"or form, that has stopped working and needs a developer."),
                details={"class": "dead_cta", "label": label, "tag": a.tag,
                         "href": a.href, "in_nav": a.in_nav}))

        # --- social icon wired to the wrong network ---
        if a.href and a.href.startswith("http"):
            actual = _network_from_host(a.href)
            intended = _network_from_label(a)
            # A share endpoint legitimately posts to another network; only profile/channel links
            # are being checked.
            is_share = "/sharer" in a.href or "/share" in a.href or "intent/tweet" in a.href
            if actual and intended and actual != intended and not is_share:
                key = ("social", intended, actual)
                occ = seen[key]
                seen[key] += 1
                slot = "social_misrouted" if occ == 0 else f"social_misrouted#{occ}"
                findings.append(Finding(
                    url=parsed.url, check=CHECK, severity=Severity.ERROR,
                    fingerprint=make_fingerprint(CHECK, slot, parsed.url, f"{intended}->{actual}"),
                    issue=f"the {intended} icon links to {actual}",
                    location="page body",
                    snippet=a.href[:160],
                    suggestion=(f"This icon is labelled {intended} but sends visitors to {actual}. "
                                f"Point it at the {intended} profile. It is usually a template "
                                f"field filled in with the wrong address, so every page has it."),
                    details={"class": "social_misrouted", "intended": intended,
                             "actual": actual, "href": a.href}))
    return findings
