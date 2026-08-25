"""Broken images — the check that needs a browser and almost nothing else.

An `<img>` whose file is missing looks identical in the HTML to one that loads. The server sends the
same markup either way, which is why the text auditor cannot see it and why Blue Media's "broken
image icons in the mobile mega-menu" was invisible to every run so far. A browser knows: a decoded
image reports a non-zero `naturalWidth`, and a failed one reports 0.

**The one veto that matters, and skipping it would report the whole network as broken.** Modern
themes lazy-load: `loading="lazy"` images below the fold have not been fetched at all when the page
first settles, so their `naturalWidth` is 0 for exactly the same reason a 404 is. Probing without
scrolling first would flag every below-the-fold image on every page — thousands of findings, all
false. So the page is scrolled to the bottom, the network is allowed to settle, and only then are
images probed.

Three more shapes that are 0-width without being defects, all excluded:
  * an image the guard itself blocked — never report a defect our own blocking caused
    (the `space_before_punct` lesson, again);
  * `display:none` / zero-box images, which a theme uses for spacers and print variants;
  * `<img>` with no `src` at all, which is a markup fault the HTML layer already owns.
"""
from __future__ import annotations

from auditor.report import Finding, Severity, make_fingerprint

CHECK = "broken_image"

# Time for lazily-fetched images to actually arrive after scrolling. Generous on purpose: a false
# "broken" is far more expensive than a slow check.
_SETTLE_MS = 1500


def scroll_to_load_everything(page, *, settle_ms: int = _SETTLE_MS) -> None:
    """Walk the page to the bottom so lazy images fetch, then let the network go quiet."""
    page.evaluate("""async () => {
        const step = Math.max(200, window.innerHeight * 0.8);
        for (let y = 0; y < document.body.scrollHeight; y += step) {
            window.scrollTo(0, y);
            await new Promise(r => setTimeout(r, 60));
        }
        window.scrollTo(0, 0);
    }""")
    try:
        page.wait_for_load_state("networkidle", timeout=settle_ms * 4)
    except Exception:
        pass                       # a page with a persistent socket never goes idle; carry on
    page.wait_for_timeout(settle_ms)


def find_broken(page, blocked_urls: set[str] | None = None) -> list[dict]:
    """Images the browser tried and failed to decode. Call AFTER `scroll_to_load_everything`."""
    candidates = page.evaluate("""() => Array.from(document.images).map(img => {
        const r = img.getBoundingClientRect();
        const cs = window.getComputedStyle(img);
        return {src: img.currentSrc || img.src || '',
                natural: img.naturalWidth,
                complete: img.complete,
                boxed: r.width > 0 && r.height > 0,
                shown: cs.display !== 'none' && cs.visibility !== 'hidden',
                alt: img.getAttribute('alt'),
                html: img.outerHTML.slice(0, 160)};
    })""")
    blocked = blocked_urls or set()
    out = []
    for c in candidates:
        if not c["src"]:
            continue                       # no src is a markup fault, owned by the HTML layer
        if c["natural"] != 0:
            continue                       # decoded fine
        if not c["shown"] or not c["boxed"]:
            continue                       # spacer / print-only / hidden: nobody sees it
        if c["src"] in blocked:
            continue                       # WE stopped it. Never report our own blocking.
        out.append(c)
    return out


def to_findings(broken: list[dict], url: str, *, viewport: str,
                page_count: int = 1) -> list[Finding]:
    findings: list[Finding] = []
    for c in broken:
        src = c["src"]
        findings.append(Finding(
            url=url, check=CHECK, severity=Severity.ERROR,
            fingerprint=make_fingerprint(CHECK, viewport, url, src),
            issue="an image on this page fails to load",
            location=f"img ({viewport})", snippet=c["html"],
            suggestion=(f"The browser requested this image and got nothing back, so visitors see a "
                        f"broken-image icon or an empty gap where it should be: {src} . The image "
                        f"occupies space on the page, so it is not hidden — it is missing. Either "
                        f"re-upload it or point the tag at a file that exists."),
            details={"class": "broken_image", "src": src, "viewport": viewport,
                     "alt": c.get("alt"), "page_count": page_count, "template_sampled": True}))
    return findings
