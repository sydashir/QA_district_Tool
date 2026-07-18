"""Phone-number check (v1 deterministic) — net-new; nothing upstream validates phones.

This is the COC bug that started the ticket. Uses the ``phonenumbers`` library (not a
hand regex). Canonical numbers come from ``config.canonical_phones``.

Flags:
- ``tel:`` href vs displayed-number MISMATCH (the flagship — a button showing one number
  that dials another). ERROR.
- non-canonical numbers (tel: or visible) not in the brand's canonical set. WARNING.
- malformed ``tel:`` values. WARNING.

Scope / trust caveat (CLAUDE.md §7 [i]): the client says pages use CallTrackingMetrics
(CTM) DNI, which swaps the *displayed* number client-side via JS. Our static fetch does
NOT execute JS, so we audit the **hardcoded** ``tel:`` href + server-rendered visible
text — exactly what [i] asks ("audit the hardcoded target number"). We do NOT see CTM's
client-side swap; verifying the live displayed number would need a JS-rendering crawl.
On GL no call-tracking vendor script was detected in static HTML, so GL's tel: values are
hardcoded and this check is trustworthy for GL's static audit.
"""
from __future__ import annotations

import re
import urllib.parse

import phonenumbers
from bs4 import BeautifulSoup

from ..parse import ParsedPage
from ..report import Finding, Severity, make_fingerprint

CHECK = "phone"
_HAS_DIGIT = re.compile(r"\d")
_REGION = "US"


def normalize(num: str) -> str | None:
    """Return E.164 for a valid US number, else None. Converts vanity letters (keypad
    T-A-L-K -> 8255), same as a phone dialing tel:1-800-273-TALK."""
    try:
        parsed = phonenumbers.parse(num, _REGION)
    except phonenumbers.NumberParseException:
        return None
    if phonenumbers.is_valid_number(parsed):
        return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    return None


# Vanity toll-free numbers with letters (e.g. 1-800-273-TALK). PhoneNumberMatcher (used on
# visible text) does NOT convert letters, while the tel: path (normalize) does — so we scan
# for vanity tokens explicitly to keep the two extraction paths in agreement.
_VANITY_RE = re.compile(r"\b1?[\s.\-]?8\d\d[\s.\-]?[\dA-Za-z]{3}[\s.\-]?[\dA-Za-z]{4}\b")


def _vanity_numbers(text: str) -> set[str]:
    out: set[str] = set()
    for m in _VANITY_RE.finditer(text):
        tok = m.group(0)
        if any(ch.isalpha() for ch in tok):  # only vanity; pure-digit numbers -> the Matcher
            n = normalize(tok)
            if n:
                out.add(n)
    return out


_SNAP_CAVEAT = "NAP 2026-07-02 snapshot; sheet ID unverified"


def run(parsed: ParsedPage, config) -> list[Finding]:
    findings: list[Finding] = []
    # NAP-derived ruler (national + per_location = clean; stale_retired = the headline)
    # when present; else the flat legacy list with no stale/unknown split.
    canon = getattr(config, "canon", None)
    if canon is not None:
        clean = canon.current_set()
        retired = set(canon.stale_retired)
    else:
        clean = {e for e in (normalize(c) for c in config.canonical_phones) if e}
        retired = set()
    third_party = getattr(config, "third_party", None) or set()  # expected hotlines, not defects

    soup = BeautifulSoup(parsed.raw_html, "lxml")
    numbers_on_page: set[str] = set()  # E.164, for the non-canonical pass (deduped)

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href.lower().startswith("tel:"):
            continue
        raw = href[4:].strip()
        decoded = urllib.parse.unquote(raw)
        if decoded != raw:  # URL-encoded tel: — a formatting defect the client documents; keep
            findings.append(Finding(  # this AND the number's own classification below (both)
                url=parsed.url, check=CHECK, severity=Severity.WARNING,
                fingerprint=make_fingerprint(CHECK, "encoded_tel", parsed.url, raw),
                issue="tel: href has URL-encoded characters", location=f"tel:{raw}", snippet=raw,
                suggestion=f"tel: href is URL-encoded ({raw!r}); it dials {decoded!r} — "
                           f"clean the encoding.", details={"raw": raw, "decoded": decoded}))
        tel_e164 = normalize(decoded)  # classify the number BEHIND the encoding, not the raw
        if tel_e164 is None:
            findings.append(Finding(
                url=parsed.url, check=CHECK, severity=Severity.WARNING,
                fingerprint=make_fingerprint(CHECK, "malformed", parsed.url, decoded),
                issue="malformed tel: number", location=f"tel:{raw}", snippet=decoded))
            continue
        numbers_on_page.add(tel_e164)

        display = a.get_text(" ", strip=True)
        disp_e164 = normalize(display) if _HAS_DIGIT.search(display) else None
        if disp_e164 and disp_e164 != tel_e164:
            if tel_e164 in retired:  # dials a DEAD line — no call-routing story makes this OK -> ERROR
                findings.append(Finding(
                    url=parsed.url, check=CHECK, severity=Severity.ERROR,
                    fingerprint=make_fingerprint(CHECK, "dials_retired", parsed.url, tel_e164),
                    issue="click-to-call dials a retired number", location=f"tel:{raw}",
                    snippet=f"shows {display!r} but dials RETIRED {tel_e164}",
                    suggestion=f"The button dials {tel_e164}, a retired number ({_SNAP_CAVEAT}) — "
                               f"customers reach a dead line. Fix the tel: target.",
                    details={"displayed": disp_e164, "tel": tel_e164, "class": "dials_retired"}))
            else:  # displayed != dialed but the dialed line is LIVE -> likely call-tracking; a QUESTION
                findings.append(Finding(
                    url=parsed.url, check=CHECK, severity=Severity.WARNING,
                    fingerprint=make_fingerprint(CHECK, "display_dial_mismatch", parsed.url, tel_e164),
                    issue="displayed number differs from the click-to-call target", location=f"tel:{raw}",
                    snippet=f"shows {display!r} but dials {tel_e164}",
                    suggestion=f"Shows {disp_e164} but dials {tel_e164}. If this is call-tracking "
                               f"(show local, route to a central line) it is intended — confirm it's "
                               f"deliberate, not a copy-paste error on specific pages.",
                    details={"displayed": disp_e164, "tel": tel_e164, "class": "display_dial_mismatch"}))

    # visible numbers not inside a tel: link (phonenumbers matcher is validity-gated)
    for match in phonenumbers.PhoneNumberMatcher(parsed.visible_text, _REGION):
        e164 = phonenumbers.format_number(match.number, phonenumbers.PhoneNumberFormat.E164)
        numbers_on_page.add(e164)
    numbers_on_page |= _vanity_numbers(parsed.visible_text)  # align visible path with tel:

    brand = getattr(config, "brand", "this brand")
    if clean or retired:
        for e164 in sorted(numbers_on_page - clean):
            if e164 in retired:  # a KNOWN-retired number still live -> the headline
                findings.append(Finding(
                    url=parsed.url, check=CHECK, severity=Severity.ERROR,
                    fingerprint=make_fingerprint(CHECK, "retired", parsed.url, e164),
                    issue="retired phone number still present", location="page", snippet=e164,
                    suggestion=f"{e164} is a retired NAP number ({_SNAP_CAVEAT}) — replace "
                               f"with the current canonical number.",
                    details={"number": e164, "class": "stale_retired", "source": _SNAP_CAVEAT}))
            elif e164 in third_party:  # Poison Control / SAMHSA / Lifeline / RAINN -> expected
                findings.append(Finding(
                    url=parsed.url, check=CHECK, severity=Severity.INFO,
                    fingerprint=make_fingerprint(CHECK, "third_party", parsed.url, e164),
                    issue="known third-party hotline", location="page", snippet=e164,
                    suggestion=f"{e164} is a documented third-party crisis hotline ({_SNAP_CAVEAT})"
                               f" — expected on the page, not a defect.",
                    details={"number": e164, "class": "third_party", "source": _SNAP_CAVEAT}))
            elif canon is not None:  # not clean, not known-retired -> unknown to the NAP
                findings.append(Finding(
                    url=parsed.url, check=CHECK, severity=Severity.WARNING,
                    fingerprint=make_fingerprint(CHECK, "unknown", parsed.url, e164),
                    issue="unknown phone number (not in NAP)", location="page", snippet=e164,
                    suggestion=f"{e164} is not in {brand}'s NAP numbers ({_SNAP_CAVEAT}) — "
                               f"verify it belongs on this brand.",
                    details={"number": e164, "class": "unknown", "source": _SNAP_CAVEAT}))
            else:  # legacy flat list (no NAP canon): preserve the original label
                findings.append(Finding(
                    url=parsed.url, check=CHECK, severity=Severity.WARNING,
                    fingerprint=make_fingerprint(CHECK, "non_canonical", parsed.url, e164),
                    issue="non-canonical phone number", location="page", snippet=e164,
                    suggestion=f"Not in brand canonical set {sorted(clean)}.",
                    details={"number": e164}))

    return findings
