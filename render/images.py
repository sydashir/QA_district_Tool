"""Broken images — the check that needs a browser and almost nothing else.

An `<img>` whose file is missing looks identical in the HTML to one that loads. The server sends the
same markup either way, which is why the text auditor cannot see it and why Blue Media's "broken
image icons in the mobile mega-menu" was invisible to every run so far. A browser knows: a decoded
image reports a non-zero `naturalWidth`, and a failed one reports 0.

**`naturalWidth == 0` is NOT sufficient evidence, and trusting it cost this check its first two
findings.** On the first real run, TDRC's homepage reported two broken images. Both returned HTTP
200 with real PNG bytes. They were `loading="lazy"` and simply had never been fetched. So the check
now needs two things to agree before it will report anything:

1. **The page must actually have been scrolled** — see the smooth-scroll trap below; and
2. **a real network fetch of that exact URL must fail** — `confirm_broken`. An image the browser
   never bothered to request is not a broken image, and only a fetch can tell the two apart.

**The smooth-scroll trap — the scroll was silently doing almost nothing.** These themes set
`scroll-behavior: smooth` on `<html>`. Under that, `window.scrollTo(0, y)` starts an *animation*
rather than moving; calling it again a moment later restarts the animation from wherever it got to.
Measured on TDRC's homepage: the loop asked for ten positions spanning 6862px and actually reached
**1181px — 17% of the page.** Every lazy image below that was never requested, and every one of them
was a candidate to be reported as broken. So the scroll now forces `scroll-behavior: auto`, and it
**verifies that it reached the bottom instead of assuming it did** — the same lesson as the network
guard: configured is not the same as working.

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

# Candidates confirmed per page. A page with hundreds of genuinely missing images is already a
# story told by the first few, and an unbounded probe on a pathological page is a self-inflicted
# hammering of the client's origin. If this bites, it is REPORTED, never silent — see `find_broken`.
_MAX_PROBES = 40


def scroll_to_load_everything(page, *, settle_ms: int = _SETTLE_MS) -> dict:
    """Walk the page to the bottom so lazy images fetch, then let the network go quiet.

    Returns what actually happened — `reached_bottom` False means the page fought the scroll and
    any lazy image below `max_scroll` was never requested. Callers should not treat unfetched
    images as broken; `confirm_broken` is what makes that safe.
    """
    result = page.evaluate("""async ([settle]) => {
        // Force instant scrolling. Under `scroll-behavior: smooth` a scrollTo is an animation, and
        // a loop of them restarts the animation instead of advancing — the page barely moves.
        const styleEl = document.createElement('style');
        styleEl.textContent = 'html,body{scroll-behavior:auto !important}';
        document.documentElement.appendChild(styleEl);

        const step = Math.max(200, window.innerHeight * 0.8);
        let maxScroll = 0, guard = 0;
        // scrollHeight is re-read every iteration: a page that appends content as you scroll grows
        // underneath the loop, and a cached bound would stop short of the new material.
        for (let y = 0; y < document.body.scrollHeight && guard < 400; y += step, guard++) {
            window.scrollTo(0, y);
            await new Promise(r => setTimeout(r, 60));
            maxScroll = Math.max(maxScroll, window.scrollY);
        }
        const docH = document.body.scrollHeight;
        const bottom = maxScroll + window.innerHeight >= docH - 4;
        window.scrollTo(0, 0);
        styleEl.remove();
        return {reached_bottom: bottom, max_scroll: Math.round(maxScroll),
                doc_height: docH, viewport: window.innerHeight};
    }""", [settle_ms])
    try:
        page.wait_for_load_state("networkidle", timeout=settle_ms * 4)
    except Exception:
        pass                       # a page with a persistent socket never goes idle; carry on
    page.wait_for_timeout(settle_ms)
    return result


def confirm_broken(page, srcs: list[str], *, timeout_ms: int = 8000) -> dict:
    """Fetch each URL for real, from the page. True means the browser could not load it.

    Uses an `Image()` load rather than `fetch()` deliberately: `fetch` on a cross-origin image
    yields an opaque response whose status is always 0, which cannot distinguish a 404 from a
    success. An `Image` reports `onload` / `onerror` correctly regardless of origin — and it is
    the same code path the page itself would have taken, so a pass here means a visitor sees it.

    The probe goes through the same route handler as every other request, so the network guard
    still applies and a tracker cannot be reached through this door.
    """
    if not srcs:
        return {}
    return page.evaluate("""async ([srcs, timeout]) => {
        const probe = (src) => new Promise((resolve) => {
            const im = new Image();
            const done = (v) => { im.onload = im.onerror = null; resolve(v); };
            im.onload  = () => done(im.naturalWidth === 0);   // decoded to nothing = broken
            im.onerror = () => done(true);
            setTimeout(() => done(false), timeout);           // timed out: NOT evidence of broken
            im.src = src;
        });
        const out = {};
        for (const s of srcs) out[s] = await probe(s);
        return out;
    }""", [srcs, timeout_ms])


def find_broken(page, blocked_urls: set[str] | None = None,
                *, confirm: bool = True) -> list[dict]:
    """Images the browser tried and failed to load. Call AFTER `scroll_to_load_everything`.

    `confirm=False` skips the network re-fetch. It exists for tests against local fixtures; a
    production caller should never pass it, because `naturalWidth == 0` alone is not evidence.
    """
    candidates = page.evaluate("""() => Array.from(document.images).map(img => {
        const r = img.getBoundingClientRect();
        const cs = window.getComputedStyle(img);
        return {src: img.currentSrc || img.src || '',
                natural: img.naturalWidth,
                complete: img.complete,
                boxed: r.width > 0 && r.height > 0,
                shown: cs.display !== 'none' && cs.visibility !== 'hidden',
                lazy: (img.getAttribute('loading') || '').toLowerCase() === 'lazy',
                alt: img.getAttribute('alt'),
                html: img.outerHTML.slice(0, 160)};
    })""")
    blocked = blocked_urls or set()
    suspects = []
    for c in candidates:
        if not c["src"]:
            continue                       # no src is a markup fault, owned by the HTML layer
        if c["natural"] != 0:
            continue                       # decoded fine
        if not c["shown"] or not c["boxed"]:
            continue                       # spacer / print-only / hidden: nobody sees it
        if c["src"] in blocked:
            continue                       # WE stopped it. Never report our own blocking.
        suspects.append(c)

    if not confirm or not suspects:
        return suspects

    unique = list(dict.fromkeys(c["src"] for c in suspects))
    capped = unique[:_MAX_PROBES]
    verdicts = confirm_broken(page, capped)
    out = [c for c in suspects if verdicts.get(c["src"]) is True]
    if len(unique) > len(capped):
        # No silent caps: say what was not checked rather than implying full coverage.
        out.append({"src": "", "natural": 0, "boxed": True, "shown": True, "lazy": False,
                    "alt": None, "complete": False, "_capped": len(unique) - len(capped),
                    "html": f"[{len(unique) - len(capped)} further suspect image(s) on this page "
                            f"were not network-confirmed; probe cap is {_MAX_PROBES}]"})
    return out


def to_findings(broken: list[dict], url: str, *, viewport: str,
                page_count: int = 1) -> list[Finding]:
    findings: list[Finding] = []
    for c in broken:
        src = c["src"]
        if not src:
            continue                       # the cap notice is not a finding about the page
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
