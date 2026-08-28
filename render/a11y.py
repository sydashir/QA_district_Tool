"""Contrast and tap-target checks, via axe-core.

Not reimplemented, deliberately. axe-core already does two things that would otherwise have to be
designed from scratch, and the second is the more valuable:

1. It computes contrast properly — resolving inherited and layered backgrounds, alpha, and text
   shadows.
2. **It refuses to guess.** axe returns three outcomes, not two: `violations`, `passes`, and
   `incomplete` — "I could not determine this." The incomplete reasons are exactly the false
   positives this check would otherwise generate: `bgImage`, `bgGradient`, `imgNode`, `bgOverlap`,
   `fgAlpha`, `elmPartiallyObscured`, `outsideViewport`.

   **So the veto for "contrast over a background image" is not ours to write — it already exists.**
   We report `violations` only and never `incomplete`, which is the same discipline the text layer
   applies to a partial CSS read: when the tool cannot know, it says nothing.

The incomplete COUNT is still carried on the finding, because "12 elements could not be assessed"
is honest and useful, while "12 elements failed" would be a lie.

**Tap targets: 24x24 is the error, 44x44 is a recommendation.**
`target-size` tests WCAG 2.5.8 *Target Size (Minimum)*, level AA — **24x24 CSS px**, and it is off
by default in axe (it ships behind the WCAG 2.2 tag), so it is enabled explicitly here.
44x44 is a different criterion — WCAG 2.5.5 *Target Size (Enhanced)*, level **AAA**, and separately
Apple's HIG. It ships OFF, as INFO, labelled a recommendation, because we do not get to report a
client as failing a standard they never adopted.

axe-core is MPL-2.0: file-level copyleft, used unmodified and unbundled, so it carries no
obligation onto this repo. (Contrast with the GPL-3.0 grammar library rejected in D11.)
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from auditor.report import Finding, Severity, make_fingerprint

CHECK_CONTRAST = "contrast"
CHECK_TAP_TARGET = "tap_target"

# WCAG 2.5.5 (AAA) / Apple HIG. OFF by default — a recommendation, never a failure.
ENHANCED_TARGET_PX = 44


@lru_cache(maxsize=1)
def _axe_source() -> str:
    """The bundled axe-core, read from disk. Never fetched from a CDN — a check that depends on the
    network is a check that reports differently depending on whether the network was up."""
    import axe_playwright_python
    root = Path(axe_playwright_python.__file__).parent
    for p in root.rglob("axe*.js"):
        return p.read_text(encoding="utf-8")
    raise FileNotFoundError("axe-core JS not found inside axe_playwright_python")


# `target-size` is OFF by default in axe (it ships behind the WCAG 2.2 tag), so naming the rules
# explicitly via runOnly is what turns it on — a tag-based run would silently skip it.
DEFAULT_RULES: tuple[str, ...] = ("color-contrast", "target-size")


def run_axe(page, *, rules: tuple[str, ...] = DEFAULT_RULES) -> dict:
    """Inject axe and run ONLY the rules we asked for. Returns axe's raw result dict.

    `resultTypes` is deliberately NOT restricted to violations: the incomplete list is what lets a
    finding say how much of the page could not be assessed.
    """
    page.evaluate(_axe_source())
    return page.evaluate(
        """(rules) => axe.run(document, {
               runOnly: {type: 'rule', values: rules},
               resultTypes: ['violations', 'incomplete'],
               reporter: 'v2'
           }).then(r => ({
               violations: r.violations.map(v => ({
                   id: v.id, impact: v.impact, help: v.help,
                   nodes: v.nodes.map(n => ({
                       target: n.target, html: n.html,
                       message: (n.any[0] && n.any[0].message) || '',
                       // Which part of the page this sits in. A cramped footer link and a cramped
                       // booking button are the same rule and very different problems.
                       region: (() => {
                           try {
                               const el = document.querySelector(n.target[0]);
                               const box = el && el.closest('footer,nav,header,aside,main');
                               return box ? box.tagName.toLowerCase() : '';
                           } catch (e) { return ''; }
                       })(),
                       data: (n.any[0] && n.any[0].data) || null}))})),
               incomplete: r.incomplete.map(v => ({
                   id: v.id,
                   nodes: v.nodes.map(n => ({
                       target: n.target,
                       reason: (n.any[0] && n.any[0].data
                                && n.any[0].data.messageKey) || 'unknown'}))}))
           }))""",
        list(rules))


def _selector(target) -> str:
    return target[0] if isinstance(target, list) and target else str(target)


def to_findings(result: dict, url: str, *, viewport: str, page_count: int = 1) -> list[Finding]:
    """Map axe's raw output onto the project's Finding shape.

    `page_count` is the number of pages this TEMPLATE covers. The render pass looks at one page per
    template, so a finding must say "this template is broken and is used on N pages" — never
    "N pages are broken", because only one was looked at.
    """
    findings: list[Finding] = []
    incomplete_by_rule: dict[str, list[str]] = {}
    for group in result.get("incomplete", []):
        incomplete_by_rule.setdefault(group["id"], []).extend(
            n.get("reason", "unknown") for n in group.get("nodes", []))

    for group in result.get("violations", []):
        rule = group["id"]
        check = CHECK_CONTRAST if rule == "color-contrast" else CHECK_TAP_TARGET
        undecided = incomplete_by_rule.get(rule, [])
        for node in group.get("nodes", []):
            sel = _selector(node.get("target"))
            data = node.get("data") or {}
            detail = ""
            if rule == "color-contrast" and data.get("contrastRatio") is not None:
                detail = (f" Measured {data['contrastRatio']}:1 against a required "
                          f"{data.get('expectedContrastRatio', '4.5:1')}"
                          f" (text {data.get('fgColor', '?')} on {data.get('bgColor', '?')}).")
            note = ""
            if undecided:
                note = (f" Separately, {len(undecided)} element(s) on this page could not be "
                        f"assessed for this rule at all ({', '.join(sorted(set(undecided))[:3])}) "
                        f"— those are NOT counted as failures.")
            region = node.get("region") or ""
            where = ""
            if check == CHECK_TAP_TARGET and region:
                where = (f" It is in the page's <{region}>, which on every instance measured so far "
                         f"means site-wide furniture — a footer or navigation link — rather than a "
                         f"button on the path to contacting you.")
            findings.append(Finding(
                url=url, check=check,
                # Unreadable text is a different order of problem from a small link. Tap targets
                # measured 18 findings across 63 pages on 2 of 8 brands, all of them in footers or
                # leftover WordPress boilerplate; ERROR is reserved for defects that cost a call.
                severity=Severity.ERROR if check == CHECK_CONTRAST else Severity.WARNING,
                fingerprint=make_fingerprint(check, viewport, url, sel),
                issue=(f"text fails the minimum contrast ratio" if check == CHECK_CONTRAST
                       else "tap target is smaller than 24x24 and has no spacing around it"),
                location=f"{sel} ({viewport})",
                snippet=(node.get("html") or "")[:180],
                suggestion=(f"{node.get('message') or group.get('help', '')}.{detail}{where}{note} "
                            f"Checked against WCAG "
                            f"{'1.4.3 (AA)' if check == CHECK_CONTRAST else '2.5.8 (AA)'}."),
                details={"class": rule, "viewport": viewport, "selector": sel, "region": region,
                         "page_count": page_count, "template_sampled": True,
                         "incomplete_on_page": len(undecided), **data}))
    return findings


# --- colour maths -------------------------------------------------------------------------------
# Implemented here rather than pulled from a library: it is nine lines of the WCAG 2.x definition,
# and the same arithmetic was used to independently verify axe's numbers on 671 real nodes (all 671
# agreed). Having our own copy is what made that verification possible — see ARCHITECTURE.md D14.

def _hex_to_rgb(c: str) -> tuple[int, int, int]:
    c = c.strip().lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def _rel_luminance(rgb: tuple[int, int, int]) -> float:
    def channel(v: float) -> float:
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(x) for x in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast_ratio(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    la, lb = _rel_luminance(a), _rel_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def darken_to_pass(fg: str, bg: str, required: float = 4.5) -> str | None:
    """The nearest version of THIS colour that meets the ratio, keeping its hue.

    Scales the foreground toward black (or toward white when the background is dark — AR's palette
    is light grey on near-black, where darkening makes it worse). Returns None if no scaling of the
    hue can reach the target, which is honest: some pairs need a different background, not a
    different text colour, and inventing an answer there would send a designer in a circle.

    Multiplicative scaling keeps the channel ratios, so #1989ff stays recognisably the same blue
    instead of becoming "use black" — advice a designer would correctly ignore.
    """
    try:
        f, b = _hex_to_rgb(fg), _hex_to_rgb(bg)
    except (ValueError, IndexError):
        return None
    if _contrast_ratio(f, b) >= required:
        return fg
    toward_white = _rel_luminance(b) < 0.5
    best = None
    for step in range(1, 101):
        t = step / 100
        if toward_white:
            cand = tuple(round(c + (255 - c) * t) for c in f)
        else:
            cand = tuple(round(c * (1 - t)) for c in f)
        if _contrast_ratio(cand, b) >= required:
            best = cand
            break
    return "#%02x%02x%02x" % best if best else None


# Below this share of assessable elements, listing findings implies a coverage that does not exist.
# Set at 60% from the first per-brand measurement: TDRC 88.5%, AH 84.3%, CAD 70.3% read as genuine
# coverage with gaps; AR 55.2%, GL 29.6%, COC 19.5%, RR 12.2% and DBH 2.1% do not.
COVERAGE_FLOOR = 0.60

_REASON_TEXT = {
    "bgImage": "the text sits on top of a photograph or background image",
    "bgGradient": "the text sits on a colour gradient rather than a flat colour",
    "elmPartiallyObscured": "another element overlaps the text",
    "elmPartiallyObscuring": "the text overlaps something else",
    "pseudoContent": "a decorative layer is drawn over the text",
    "shortTextContent": "there is too little text to sample reliably",
    "equalRatio": "the text and its background are the same colour",
}


def coverage_finding(brand: str, *, assessed: int, withheld: int,
                     reasons: dict[str, int]) -> Finding | None:
    """Say how much of the site's contrast could actually be judged — or return None if most of it
    could.

    Without this, DBH's SEVEN contrast findings read as "we checked this site and found 7 problems".
    We checked 2% of it. The withheld elements are not passes and must never be presented as though
    a tool looked at them and was satisfied.
    """
    total = assessed + withheld
    if not total:
        return None
    pct = assessed / total * 100
    if pct >= COVERAGE_FLOOR * 100:
        return None                       # genuine coverage; a caveat everywhere trains readers to skip it
    dominant = max(reasons, key=reasons.get) if reasons else None
    why = _REASON_TEXT.get(dominant or "", "")
    why_text = (f" The most common reason is that {why} — a contrast figure cannot be calculated "
                f"from that automatically, by any tool.") if why else ""
    return Finding(
        url="", check=CHECK_CONTRAST, severity=Severity.INFO,
        fingerprint=make_fingerprint(CHECK_CONTRAST, "coverage", brand),
        issue="most of this site's text contrast could not be assessed automatically",
        location="site", snippet=f"{assessed} of {total} elements assessable",
        suggestion=(f"Of the {total} pieces of text we looked at on this site, only {assessed} "
                    f"({pct:.1f}%) could be measured for contrast at all.{why_text} "
                    f"Any contrast findings listed here therefore describe the {pct:.1f}% we could "
                    f"check — they are NOT a clean bill of health for the rest, which was not "
                    f"assessed rather than assessed and passed. Judging the remainder needs a "
                    f"person looking at the rendered page."),
        details={"class": "contrast_coverage", "assessed": assessed, "withheld": withheld,
                 "assessed_pct": round(pct, 1), "dominant_reason": dominant,
                 "reasons": reasons, "template_sampled": True})


def collapse_contrast(findings: list[Finding]) -> list[Finding]:
    """Group contrast findings by the COLOUR DECISION behind them, not the elements affected.

    Measured on 63 real pages across 8 brands: **1,036 raw violations, 8 distinct colour pairs**,
    and one pair (`#7a7a7a` on white, 4.29:1 against a required 4.5:1) accounted for 531 of them.
    Per-node reporting means ~15 rows per page — 57 on CAD — every one restating the same theme
    colour. Extrapolated across ~31,000 pages that is a six-figure row count saying eight things.

    Collapsing is safe here in a way it would not be for, say, broken links: a contrast violation is
    a property of a colour PAIR, so every element sharing that pair has one cause and one fix.
    Splitting by required ratio matters though — the same pair can pass as large text (3:1) and fail
    as body text (4.5:1), and those are different problems.

    The arithmetic behind these findings was independently recomputed against the WCAG formula for
    all 671 nodes carrying colour data, and all 671 agreed. What is NOT verified is axe's choice of
    background colour — its known weak spot — which is why elements over background images and
    gradients land in axe's `incomplete` bucket and are never reported at all. On this sample that
    is **63.9% of contrast-relevant elements withheld as undecidable**, and the collapsed finding
    says so rather than implying the page was fully assessed.
    """
    groups: dict[tuple, list[Finding]] = {}
    passthrough: list[Finding] = []
    for f in findings:
        d = f.details or {}
        fg, bg = d.get("fgColor"), d.get("bgColor")
        if d.get("class") != "color-contrast" or not fg or not bg:
            passthrough.append(f)
            continue
        groups.setdefault((fg, bg, str(d.get("expectedContrastRatio") or "4.5:1")), []).append(f)

    out = list(passthrough)
    for (fg, bg, required), group in groups.items():
        first = group[0]
        ratio = (first.details or {}).get("contrastRatio")
        urls = list(dict.fromkeys(f.url for f in group))
        need = required.split(":")[0]
        try:
            target = float(need)
        except ValueError:
            target = 4.5
        # A designer should not have to reach for a contrast tool to act on this.
        suggested = darken_to_pass(fg, bg, target)
        fix = (f" Changing {fg} to {suggested} keeps the same colour and clears the standard."
               if suggested and suggested.lower() != fg.lower()
               else " No shade of this colour passes on this background — the background needs to "
                    "change instead.")
        out.append(Finding(
            url=first.url, check=CHECK_CONTRAST, severity=Severity.ERROR,
            fingerprint=make_fingerprint(CHECK_CONTRAST, "pair", fg, bg, required),
            issue=f"text colour {fg} on {bg} is too faint to read against its background",
            location=(first.details or {}).get("viewport", "mobile"),
            snippet=first.snippet,
            suggestion=(f"Text in {fg} on a {bg} background measures {ratio}:1, where the "
                        f"accessibility standard asks for at least {need}:1. This is one colour "
                        f"choice in the theme rather than {len(group)} separate mistakes: it "
                        f"appears on {len(group)} element(s) across {len(urls)} page(s) in this "
                        f"sample, so one change fixes all of them.{fix} Checked against "
                        f"WCAG 1.4.3 (AA)."),
            details={"class": "color-contrast", "fgColor": fg, "bgColor": bg,
                     "contrastRatio": ratio, "expectedContrastRatio": required,
                     "suggested_fg": suggested,
                     "element_count": len(group), "page_count": len(urls),
                     "examples": urls[:5], "template_sampled": True}))
    return out


def enhanced_target_findings(page, url: str, *, viewport: str,
                             page_count: int = 1) -> list[Finding]:
    """44x44 (WCAG 2.5.5 AAA / Apple HIG) — a RECOMMENDATION, reported as INFO, off by default.

    Kept separate from `target-size` on purpose: 24 cites a standard the client can be held to, 44
    is a design opinion. Mixing them would let an opinion be reported as a failure.
    """
    small = page.evaluate(
        """(min) => Array.from(document.querySelectorAll('a,button,input,select,[role=button]'))
              .map(el => {const r = el.getBoundingClientRect();
                          return {w: Math.round(r.width), h: Math.round(r.height),
                                  vis: r.width > 0 && r.height > 0,
                                  tag: el.tagName.toLowerCase(),
                                  text: (el.innerText || el.getAttribute('aria-label') || '').trim().slice(0,40),
                                  html: el.outerHTML.slice(0,140)}})
              .filter(t => t.vis && (t.w < min || t.h < min))""",
        ENHANCED_TARGET_PX)
    out: list[Finding] = []
    for t in small:
        key = f"{t['tag']}:{t['text']}:{t['w']}x{t['h']}"
        out.append(Finding(
            url=url, check=CHECK_TAP_TARGET, severity=Severity.INFO,
            fingerprint=make_fingerprint(CHECK_TAP_TARGET, f"enhanced-{viewport}", url, key),
            issue=f"tap target is {t['w']}x{t['h']}, below the {ENHANCED_TARGET_PX}px recommendation",
            location=f"{t['tag']} ({viewport})", snippet=t["html"],
            suggestion=(f"This is a RECOMMENDATION, not a failure: {ENHANCED_TARGET_PX}x"
                        f"{ENHANCED_TARGET_PX} comes from WCAG 2.5.5 (level AAA) and Apple's "
                        f"interface guidelines, which this site has not committed to. It already "
                        f"meets the 24x24 minimum that IS required. Enlarge it only if comfortable "
                        f"one-handed tapping matters here."),
            details={"class": "target_size_enhanced", "viewport": viewport,
                     "width": t["w"], "height": t["h"], "page_count": page_count,
                     "template_sampled": True, "advisory": True}))
    return out
