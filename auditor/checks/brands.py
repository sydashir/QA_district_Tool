"""A sister brand named in another brand's body copy (v1 deterministic) — B6.

Structurally this is `cross_brand_dial` for NAMES: the page belongs to one brand and the copy names
another. Connor reported it twice, the clearest being **"Gratitude Lodge" appearing on the
Connections site**.

**Names are far messier than phone numbers, and the measurement says so.** A number is either in
another brand's NAP row or it is not. A name is a phrase that ordinary English also uses, and a
District brand may name a sister deliberately. Measured across 95 live pages on 8 brands:

* **"Connections" is the English word every single time** — "vital connections to their everyday
  obligations", "meaningful connections with loved ones", "building connections with others in
  recovery". Zero brand uses. The NAP sheet and the site's own `<title>` both give the real name as
  **"Connections Mental Health"**, so the bare word was never the brand identifier.
* **AH's disclosure copy names all nine brands on 10 of 11 pages** — "calls to this hotline are
  answered by admissions counselors from Renaissance Recovery, Gratitude Lodge, California Detox…"
  That is required disclosure, not a defect.
* **DBH is a directory** — it lists every sister facility on 11 of 12 pages, by design.
* **GL genuinely says** "our PHP services near San Diego operate through Renaissance Recovery."
  A real network relationship, deliberately written.
* Footer network lists carry sister names on RR, CAD and TDRC with **zero** body mentions.

So three gates, and the third is the one that does the real work:

1. **Body copy only.** Footer/nav network lists are the dominant legitimate pattern.
2. **Distinctive, case-sensitive names.** Multi-word and capitalised as the brand writes it, which
   is what separates "Addiction Hotline" the brand from "call an addiction hotline" the phrase.
3. **Rarity.** Applied site-wide in `audit.py::_collapse_brands`, not here: a sister named on most
   of a brand's pages is a template or a relationship; a sister named on 2 pages out of 500 is the
   anomaly Connor reported. This is the same reasoning that made the 88-page CTM case a question
   rather than an error.

Severity is WARNING and the wording is a QUESTION, because even after all three gates the tool
cannot know editorial intent — exactly the `display_dial_ambiguous` precedent.
"""
from __future__ import annotations

import re
from collections import Counter

from ..parse import ParsedPage
from ..report import Finding, Severity, make_fingerprint

CHECK = "brands"

# Brand -> the names that identify it in copy. Confirmed against the NAP sheet's "NAP Name" column
# and each site's own <title>; never guessed.
#
# NOT in this table, deliberately:
#   * bare "Connections"  — an ordinary English word, 100% false positive rate when measured.
#   * "District Behavioral Health" — the PARENT brand. Every site attributes itself to it
#     ("connected with the District Behavioral Health system"), which is correct copy, not a leak.
BRAND_NAMES: dict[str, tuple[str, ...]] = {
    "RR": ("Renaissance Recovery",),
    "GL": ("Gratitude Lodge",),
    "CAD": ("California Detox",),
    "AR": ("Alliance Recovery",),
    "COC": ("Connections Mental Health", "Connections OC"),
    "AH": ("Addiction Hotline",),
    "MHD": ("Inpatient Mental Health Finder",),
    "TDRC": ("The District Recovery Community", "District Recovery Community"),
}
# Names a brand may use for ITSELF beyond its own entry — a brand naming its own parent or its own
# former identity is not a cross-brand leak.
SELF_ALIASES: dict[str, tuple[str, ...]] = {
    "TDRC": ("District Behavioral Health",),   # TDRC's NAP name is literally a DBH-prefixed one
}

# Case-SENSITIVE, whole-phrase. Case is the signal that separates the brand from the phrase:
# "Addiction Hotline" is a name, "call an addiction hotline" is ordinary copy.
_PATTERNS: dict[str, tuple[re.Pattern, ...]] = {
    b: tuple(re.compile(r"(?<![\w-])" + re.escape(n) + r"(?![\w-])") for n in names)
    for b, names in BRAND_NAMES.items()
}


# Wording that MAKES a cross-brand mention deliberate. Taken verbatim from live pages found by the
# first live run: "Through our partnership with Renaissance Recovery within the District…",
# "…District Behavioral Health network, individuals can advance to Gratitude Lodge…". Copy that
# explains the relationship is copy somebody wrote on purpose.
_RELATIONSHIP = re.compile(
    r"\b(?:partnership|partner(?:s|ed|ing)?|network|affiliat\w*|sister|family\s+of|"
    r"part\s+of|owned\s+by|operated\s+by|advance\s+to|transition\s+to|refer(?:red|ral|s)?\s+to|"
    r"in\s+collaboration|alongside|our\s+other)\b", re.IGNORECASE)
_RELATIONSHIP_WINDOW = 90   # characters before the name to look in

# A page naming SEVERAL sister brands is a directory — "our facilities", a locations gallery, a
# site map. A copy-paste leak names exactly one wrong brand; a directory names the network. Live
# examples suppressed by this: RR's /about-us/sober-living-gallery (Alliance Recovery + District
# Recovery Community) and RR's /map.
_MAX_DISTINCT_BRANDS = 1


def _self_names(brand: str) -> set[str]:
    out = set(BRAND_NAMES.get(brand, ()))
    out.update(SELF_ALIASES.get(brand, ()))
    return out


def _context(text: str, start: int, end: int, pad: int = 60) -> str:
    return text[max(0, start - pad):min(len(text), end + pad)].strip()


def run(parsed: ParsedPage, config) -> list[Finding]:
    brand = (getattr(config, "brand", "") or "").upper()
    mine = _self_names(brand)
    # Deliberately NOT parsed.body_text: that falls back to the WHOLE page when no body region is
    # found, which would drag the footer network list back in — the single largest legitimate
    # source of sister-brand names. No body blocks means no basis for a claim.
    body = "\n".join(b.text for b in parsed.blocks if b.region == "body" and b.text)
    if not body:
        return []

    # Collect first, decide after: whether a mention is a leak depends on how many OTHER brands the
    # page names, which is only knowable once the whole page has been scanned.
    hits: list[tuple[str, str, int, int]] = []      # (other, matched, start, end)
    for other, patterns in _PATTERNS.items():
        if other == brand:
            continue
        for pat in patterns:
            for m in pat.finditer(body):
                if m.group(0) in mine:
                    continue
                hits.append((other, m.group(0), m.start(), m.end()))

    if len({h[0] for h in hits}) > _MAX_DISTINCT_BRANDS:
        return []                          # a directory page, not a leak

    # A page that is ABOUT a sister facility names it in its own address and title — DBH publishes
    # `/our_locations/gratitude-lodge-long-beach-ca` titled "Gratitude Lodge - Long Beach, CA".
    # That is the page's subject, not a leak.
    subject = f"{parsed.url} {parsed.title or ''}".lower()
    subject_slug = re.sub(r"[^a-z0-9]+", "-", subject)
    hits = [h for h in hits
            if re.sub(r"[^a-z0-9]+", "-", h[1].lower()) not in subject_slug]
    if not hits:
        return []

    findings: list[Finding] = []
    seen: Counter = Counter()
    for other, matched, start, end in hits:
        if _RELATIONSHIP.search(body[max(0, start - _RELATIONSHIP_WINDOW):start]):
            continue                       # copy that explains the relationship is deliberate
        key = ("sister_brand", other, matched)
        occ = seen[key]
        seen[key] += 1
        slot = "sister_brand" if occ == 0 else f"sister_brand#{occ}"
        findings.append(Finding(
            url=parsed.url, check=CHECK, severity=Severity.WARNING,
            fingerprint=make_fingerprint(CHECK, slot, parsed.url, other, matched),
            issue=f"another brand in this network is named here: \"{matched}\"",
            location="page body",
            snippet=_context(body, start, end),
            suggestion=(
                f"This is a {brand} page, but the wording names {matched}. That is sometimes "
                f"deliberate — the brands do refer to each other — so this is a question, not a "
                f"confirmed fault. Worth checking whether the text was copied from the {matched} "
                f"site by mistake. Menus, footers, pages that list several sister brands, and "
                f"sentences that explain a partnership are all left out."),
            details={"class": "sister_brand", "other_brand": other,
                     "matched": matched, "page_brand": brand}))
    return findings
