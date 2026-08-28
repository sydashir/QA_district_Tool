"""Structured data (schema.org JSON-LD) — broken markup no human can see.

Search engines read a page twice: the words a visitor sees, and a machine-readable block declaring
what the business IS — its name, phone, address, hours. That second read is invisible in a browser,
so nothing about it is noticeable by looking at the site. On healthcare pages it is also what feeds
local results, which is exactly where these brands compete.

**Ground truth first, gathered before this check was written** (318 JSON-LD blocks across all nine
brands, 2026-08-27). The sites disagree wildly, so the check must not impose one brand's convention
on another:

* **DBH and MHD publish no schema.org markup at all.** Not incomplete — absent.
* **COC ships a JSON-LD block that is not valid JSON**, so a crawler discards it silently.
* **AH, AR, CAD and COC** declare `Organization` carrying only `name` and `url` — an empty shell.
* **GL is the only complete one** (name, telephone, address, opening hours, price range, geo), which
  makes it the in-house correct example, the same role DBH's "ACROSS THE COUNTRY" played for scope.
* **RR** has name, telephone and url, but no address.

Two deliberate limits:

* **Address is NOT validated against a canonical source, because there isn't one.** The NAP sheet
  carries phone numbers only (`auditor/nap.py`), so "does the address match" cannot be answered.
  Presence is checked; correctness is not, and the finding says so rather than implying otherwise.
* **Microdata is not parsed.** Measured: 42 `itemtype` declarations across all nine brands, none of
  them a business entity (Place, Rating, Blog, Person, WebPage). JSON-LD is the whole surface.

Severity follows the rule the tap-target work settled: report a failure only against a standard the
client is actually held to. A missing telephone on a `LocalBusiness` is a Google structured-data
recommendation, not a schema.org requirement — so it is a WARNING. Malformed JSON and a phone that
contradicts the NAP sheet are certain defects, so they are ERRORs.
"""
from __future__ import annotations

import json
import re

from ..parse import ParsedPage
from ..report import Finding, Severity, make_fingerprint
from .phone import normalize

CHECK = "schema"

_LD_BLOCK = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S | re.I)

# Types that describe the BUSINESS itself. `Organization` is included because four brands use it as
# their only business node, but it is held to a lower bar than LocalBusiness (see _REQUIRES_CONTACT).
_BUSINESS_TYPES = frozenset({
    "LocalBusiness", "MedicalBusiness", "MedicalOrganization", "MedicalClinic",
    "Physician", "Hospital", "HealthAndBeautyBusiness", "Organization",
})
# Google's structured-data guidance asks for contact details on a LOCAL business specifically.
# A bare `Organization` is not expected to carry them, so it is not marked incomplete for it.
_REQUIRES_CONTACT = frozenset({
    "LocalBusiness", "MedicalBusiness", "MedicalClinic", "Physician", "Hospital",
    "HealthAndBeautyBusiness",
})


def _iter_nodes(data):
    """Every dict in a JSON-LD payload, including @graph children."""
    items = data if isinstance(data, list) else [data]
    for item in items:
        if not isinstance(item, dict):
            continue
        graph = item.get("@graph")
        if isinstance(graph, list):
            yield item
            for node in graph:
                if isinstance(node, dict):
                    yield node
        else:
            yield item


def _types(node: dict) -> set[str]:
    t = node.get("@type")
    return {x for x in (t if isinstance(t, list) else [t]) if isinstance(x, str)}



def registrable(host: str) -> str:
    """The brand-identifying part of a hostname. `help.rr.com` and `www.rr.com` are both `rr.com`."""
    parts = [p for p in host.lower().split(".") if p]
    return ".".join(parts[-2:]) if len(parts) >= 2 else host.lower()


def off_brand(page_url: str, base_url: str) -> bool:
    """True when the page finally landed on a DIFFERENT brand's domain than the one being audited.

    PUBLIC because `audit.py` skips brand-scoped checks with it — one implementation, not a copy
    per caller. It lives here rather than in a new module so it stays inside `checks_version`'s
    hash: it decides check OUTPUT, and an unhashed helper that changes findings is precisely the
    silent-staleness trap `checks_version` exists to prevent.
    """
    if not base_url:
        return False
    from urllib.parse import urlparse
    return registrable(urlparse(page_url).netloc) != registrable(urlparse(base_url).netloc)


def run(parsed: ParsedPage, config) -> list[Finding]:
    # A page that finally landed on someone else's domain is not ours to judge. Two ways that
    # happens here, both real: TDRC's /review-us/ URLs are deliberate 301s onto GL/RR/CAD, and AH's
    # page list contains a google.com/maps URL. Judging either produces a finding about a site we
    # were not auditing.
    if off_brand(parsed.url, getattr(config, "base_url", "")):
        return []
    html = parsed.raw_html or ""
    blocks = _LD_BLOCK.findall(html)
    findings: list[Finding] = []

    # --- 1) a block that is not valid JSON is silently discarded by every crawler ---
    parsed_blocks = []
    for i, raw in enumerate(blocks):
        try:
            parsed_blocks.append(json.loads(raw.strip()))
        except ValueError as e:
            findings.append(Finding(
                url=parsed.url, check=CHECK, severity=Severity.ERROR,
                fingerprint=make_fingerprint(CHECK, "invalid_json", parsed.url, str(i)),
                issue="a structured-data block on this page is not valid JSON",
                location="head", snippet=raw.strip()[:180],
                suggestion=f"Search engines parse this block and discard it whole when it will not "
                           f"read as JSON, so everything it declares is lost — silently, because "
                           f"nothing about the page looks wrong. Parser error: {e}.",
                details={"class": "invalid_json", "error": str(e)}))

    business = [n for blk in parsed_blocks for n in _iter_nodes(blk) if _types(n) & _BUSINESS_TYPES]

    # --- 2) no business schema at all ---
    if not business:
        findings.append(Finding(
            url=parsed.url, check=CHECK, severity=Severity.ERROR,
            fingerprint=make_fingerprint(CHECK, "missing", parsed.url, ""),
            issue="this page declares no business in its structured data",
            location="head", snippet="",
            suggestion="Nothing on this page tells a search engine what this business is — no name, "
                       "phone or address in machine-readable form. On a healthcare site that is "
                       "what local search results are built from, and it is invisible when looking "
                       "at the page. Add a LocalBusiness or MedicalOrganization block.",
            details={"class": "missing", "ld_blocks": len(blocks)}))
        return findings

    # --- 3) a local business with no way to contact it ---
    # The FULL NAP set — national numbers AND per-location facility lines. `canonical_phones` holds
    # only the national ones, and using it alone reported 16 GL findings that were GL's own local
    # numbers (Long Beach, Newport Beach, Orange County), every one of them in the NAP sheet.
    canon = getattr(config, "canon", None)
    canonical = set(canon.current_set()) if canon is not None else set()
    if not canonical:
        canonical = {e for e in (normalize(c)
                                 for c in (getattr(config, "canonical_phones", None) or ())) if e}
    brand_numbers = getattr(config, "brand_numbers", None) or {}   # E.164 -> owning brand(s)
    brand = getattr(config, "brand", "this brand")
    for node in business:
        types = _types(node)
        name = str(node.get("name") or "")[:60]
        if types & _REQUIRES_CONTACT:
            missing = [f for f in ("telephone", "address") if not node.get(f)]
            if missing:
                findings.append(Finding(
                    url=parsed.url, check=CHECK, severity=Severity.WARNING,
                    fingerprint=make_fingerprint(CHECK, "incomplete", parsed.url,
                                                 f"{sorted(types)}|{','.join(missing)}"),
                    issue=f"the business declared here has no {' or '.join(missing)}",
                    location="head", snippet=f"{sorted(types)} {name}",
                    suggestion=f"Google's guidance for a local business asks for {', '.join(missing)} "
                               f"so it can show contact details in search results. This is a "
                               f"recommendation rather than a rule — the markup is valid without it.",
                    details={"class": "incomplete", "missing": missing, "types": sorted(types)}))

        # --- 4) a phone in the machine-readable block that is not this brand's ---
        # Severity mirrors phone.py, which already settled this distinction: ANOTHER BRAND's number
        # is the flagship cross-brand defect and is certain; a number in nobody's set is usually a
        # legitimate local facility line (GL runs several) and is only worth a look. Measured before
        # this split: a flat "not canonical" rule reported 16 GL findings that were its own local
        # numbers.
        # A page can REDIRECT onto a sister brand's domain and still be reached from this brand's
        # sitemap: TDRC's /review-us/{code} URLs are deliberate 301s to GL, RR and CAD review pages.
        # The page that comes back is that brand's, and its phone is correct FOR IT — asserting
        # this brand's canon against it manufactures a cross-brand finding out of a working
        # redirect. Measured: 8 false ERRORs on TDRC before this guard.
        tel = node.get("telephone")
        if tel and canonical:
            e164 = normalize(str(tel))
            if e164 and e164 not in canonical:
                owners = [b for b in brand_numbers.get(e164, ()) if b != brand]
                if owners:
                    findings.append(Finding(
                        url=parsed.url, check=CHECK, severity=Severity.ERROR,
                        fingerprint=make_fingerprint(CHECK, "phone_cross_brand", parsed.url, e164),
                        issue=f"structured data publishes {owners[0]}'s phone number",
                        location="head", snippet=f"{sorted(types)} telephone={tel}",
                        suggestion=f"Search engines read {e164} as this business's number, and it "
                                   f"belongs to {owners[0]}. Nothing on the visible page reveals "
                                   f"it — the wrong brand is being advertised in a block only "
                                   f"machines read. Usually a template copied between brands.",
                        details={"class": "phone_cross_brand", "found": e164, "owner": owners[0],
                                 "canonical": sorted(canonical), "types": sorted(types)}))
                else:
                    findings.append(Finding(
                        url=parsed.url, check=CHECK, severity=Severity.WARNING,
                        fingerprint=make_fingerprint(CHECK, "phone_unknown", parsed.url, e164),
                        issue=f"structured data publishes {tel}, which is in no brand's number set",
                        location="head", snippet=f"{sorted(types)} telephone={tel}",
                        suggestion=f"{e164} is not in {brand}'s canonical set "
                                   f"({sorted(canonical)}) and belongs to no other brand either. "
                                   f"Often a legitimate local facility line — worth confirming it "
                                   f"is a real number for this location rather than a leftover.",
                        details={"class": "phone_unknown", "found": e164,
                                 "canonical": sorted(canonical), "types": sorted(types)}))
    return findings
