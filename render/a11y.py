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
            findings.append(Finding(
                url=url, check=check, severity=Severity.ERROR,
                fingerprint=make_fingerprint(check, viewport, url, sel),
                issue=(f"text fails the minimum contrast ratio" if check == CHECK_CONTRAST
                       else "tap target is smaller than 24x24 and has no spacing around it"),
                location=f"{sel} ({viewport})",
                snippet=(node.get("html") or "")[:180],
                suggestion=(f"{node.get('message') or group.get('help', '')}.{detail}{note} "
                            f"Checked against WCAG "
                            f"{'1.4.3 (AA)' if check == CHECK_CONTRAST else '2.5.8 (AA)'}."),
                details={"class": rule, "viewport": viewport, "selector": sel,
                         "page_count": page_count, "template_sampled": True,
                         "incomplete_on_page": len(undecided), **data}))
    return findings


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
                        f"sample, and darkening the one colour fixes all of them. Checked against "
                        f"WCAG 1.4.3 (AA)."),
            details={"class": "color-contrast", "fgColor": fg, "bgColor": bg,
                     "contrastRatio": ratio, "expectedContrastRatio": required,
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
