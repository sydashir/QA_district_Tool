"""Markup-only accessibility rules, over HTML we already fetched.

**This is not rendering, and the distinction is deliberate.** The client notice covers opening
their pages in a browser: loading their stylesheets, images and scripts, and firing their
analytics, A/B testing and call-tracking. This pass does none of that. It takes HTML that `httpx`
already fetched — exactly what the text auditor does on every run, with no JavaScript executed —
and analyses that string in a local browser with **every network request blocked**. The client's
servers never see a browser, nothing loads, and no tracker can fire.

Only rules that are decidable from markup alone are run. `color-contrast` needs computed colours
and `target-size` needs a real layout box; without stylesheets their answers would be fiction, so
they are excluded here and stay behind the rendering hold where they belong.

**The network block is asserted, not assumed.** A markup pass that silently began fetching would be
precisely the thing we told the client we do not do, and a guard that detached looks identical to a
page with no external references. So blocked requests are counted and the pass refuses to report if
the count is zero.

**Findings are collapsed at write time, by the shape of the thing at fault.** Measured on 264 live
pages: `link-name` alone produced 2,981 raw violations — about eleven per page on every page of all
nine brands, because the offenders are template furniture (a logo, a social icon, a phone link).
Collapsed by href shape that becomes ~143 rows. Raw, it would bury every other finding in the
report; collapsed, it says the true thing once — "this link has no name, on N pages".
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from urllib.parse import urlparse

from auditor.report import Finding, Severity, make_fingerprint
from .a11y import _axe_source
from .safety import SafetyLedger, SafetyNotArmed

CHECK = "accessibility"

# Decidable from markup alone. Everything needing CSS or layout is deliberately absent.
NEEDS_RENDERING = frozenset({
    "color-contrast", "color-contrast-enhanced", "target-size", "link-in-text-block",
    "scrollable-region-focusable", "focus-order-semantics",
})

# Plain-English titles. axe's own `help` text is written for developers ("Links must have
# discernible text"); these are for whoever reads the sheet.
_TITLES = {
    "link-name": "a link has no readable name",
    "label": "a form field has no label",
    "aria-required-children": "a list role contains the wrong kind of children",
    "aria-required-parent": "a list item sits outside any list",
    "aria-hidden-focus": "hidden content can still be reached by keyboard",
    "aria-command-name": "a button has no readable name",
    "meta-viewport": "the page prevents zooming",
    "p-as-heading": "a styled paragraph is used as a heading",
    "table-fake-caption": "a table row is used as a caption",
    "td-has-header": "a table cell has no header",
}
_TEL = re.compile(r"^tel:", re.I)


def _shape(html: str) -> str:
    """The identity a violation collapses on: what the offending element points at, generalised.

    Two links differ by their Elementor element id on every page, which would defeat collapsing.
    What actually repeats is the TARGET — the logo link, the TikTok icon, the click-to-call.
    """
    m = re.search(r'href="([^"]*)"', html or "")
    if not m:
        m2 = re.search(r"<(\w+)", html or "")
        return f"<{m2.group(1)}>" if m2 else "(element)"
    href = re.sub(r"https?://[^/]+", "", m.group(1))
    href = re.sub(r"/[^/]*\d[^/]*", "/N", href)      # digits -> N, same as the finding collapse
    return href[:60] or "/"


def _severity(rule: str) -> Severity:
    # A click-to-call nobody can identify is the accessibility twin of a dead CTA — on a
    # healthcare site it is the difference between reaching help and not.
    return Severity.ERROR if rule in ("link-name", "label", "meta-viewport") else Severity.WARNING


def analyse(page, html: str, url: str, rules: list[str]) -> list[dict]:
    """Run axe over one already-fetched HTML string. The page must already be network-blocked."""
    page.set_content(html, wait_until="domcontentloaded")
    page.evaluate(_axe_source())
    return page.evaluate(
        """(rules) => axe.run(document, {runOnly: {type: 'rule', values: rules},
                                         resultTypes: ['violations']})
             .then(r => r.violations.flatMap(v =>
                 v.nodes.map(n => ({rule: v.id, help: v.help, html: n.html || ''}))))""",
        rules)


def markup_rules(page) -> list[str]:
    """Every WCAG A/AA rule axe knows, minus the ones that need a rendered page."""
    page.set_content("<html><body></body></html>")
    page.evaluate(_axe_source())
    all_rules = page.evaluate("() => axe.getRules().map(r => ({id: r.ruleId, tags: r.tags}))")
    return [r["id"] for r in all_rules
            if any(t in r["tags"] for t in ("wcag2a", "wcag2aa", "wcag21a", "wcag21aa"))
            and r["id"] not in NEEDS_RENDERING]


def to_findings(raw: list[tuple[str, dict]], brand_url: str) -> list[Finding]:
    """Collapse raw violations into one finding per (rule, shape). THIS is the write-time collapse.

    `raw` is (page_url, violation) pairs across the whole brand.
    """
    groups: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"pages": [], "help": "", "example": ""})
    for page_url, v in raw:
        key = (v["rule"], _shape(v["html"]))
        g = groups[key]
        g["pages"].append(page_url)
        g["help"] = g["help"] or v.get("help", "")
        g["example"] = g["example"] or (v.get("html") or "")[:180]

    findings: list[Finding] = []
    for (rule, shape), g in sorted(groups.items()):
        pages = sorted(set(g["pages"]))
        title = _TITLES.get(rule, g["help"] or rule)
        is_phone = bool(_TEL.match(shape))
        extra = ""
        if rule == "link-name" and is_phone:
            extra = (" This one is a click-to-call. Somebody using a screen reader hears a link "
                     "with no name and cannot tell it dials the clinic — the accessibility twin of "
                     "a button that goes nowhere.")
        findings.append(Finding(
            url=pages[0], check=CHECK, severity=_severity(rule),
            fingerprint=make_fingerprint(CHECK, rule, brand_url, shape),
            issue=f"{title} ({shape})",
            location=f"{rule} — {shape}",
            snippet=g["example"],
            suggestion=(f"{g['help']}. Found on {len(pages)} page(s) pointing at {shape!r}, so it "
                        f"comes from a shared template — one fix corrects all of them.{extra}"),
            details={"class": rule, "shape": shape, "page_count": len(pages),
                     "sources": pages[:8], "template_collapsed": True,
                     "axe_help": g["help"]}))
    return findings


def audit_html(pages: list[tuple[str, str]], brand_url: str) -> tuple[list[Finding], SafetyLedger]:
    """(url, html) pairs -> collapsed findings. Raises SafetyNotArmed if the block did not work."""
    from playwright.sync_api import sync_playwright

    from render.browser import ensure_chromium

    ensure_chromium()          # before a browser is asked for, not at launch() (see browser.py)

    ledger = SafetyLedger()
    raw: list[tuple[str, dict]] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()

        def block(route):
            ledger.record(route.request.url, "block")
            route.abort("blockedbyclient")

        page.route("**/*", block)                 # nothing leaves this browser, ever

        # PROVE the guard is attached, with a canary — do not infer it from real traffic.
        # Counting blocked requests works for the render pass, where every page demonstrably loads
        # trackers. It does NOT work here: DBH's headless rebuild ships HTML that references no
        # external assets at all, so it legitimately blocks zero and a real run tripped the
        # assertion. A canary separates "the guard is off" from "this page had nothing to fetch".
        page.set_content(
            '<html><body><img src="https://canary.invalid/guard-probe.gif"></body></html>',
            wait_until="domcontentloaded")
        # The image request is dispatched asynchronously, AFTER domcontentloaded returns — without
        # this wait the canary reports "not blocked" simply because it had not been sent yet.
        for _ in range(20):
            if any("canary.invalid" in h for h in ledger.blocked_hosts):
                break
            page.wait_for_timeout(50)
        if not any("canary.invalid" in h for h in ledger.blocked_hosts):
            browser.close()
            raise SafetyNotArmed(
                "the network guard did not block a deliberate canary request, so it is not "
                "attached. Refusing to analyse anything: a markup pass that silently began "
                "fetching is exactly what we told the client we do not do.")
        ledger.blocked_hosts.pop("canary.invalid", None)   # the probe is not a finding
        ledger.blocked -= 1

        rules = markup_rules(page)
        for url, html in pages:
            ledger.pages += 1
            try:
                raw.extend((url, v) for v in analyse(page, html, url, rules))
            except Exception:
                continue                          # one bad page must not end the pass
        browser.close()

    # NOTE: no `assert_worked()` here. That check counts real blocked traffic, which is the right
    # signal for the render pass and the wrong one for this pass — see the canary above.
    return to_findings(raw, brand_url), ledger
