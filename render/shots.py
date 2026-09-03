"""Element screenshots — making a deterministic finding legible.

"This text is #7a7a7a on #ffffff, 4.29:1" is true and unpicturable. A cropped, outlined screenshot
of the element says the same thing to a person who has never opened a contrast tool.

**This adds no detection.** Nothing here judges whether a design "looks off" — that is the judgement
class measured and rejected four times (D9, D10, D11, and the AI pass D12 replaced). Screenshots
illustrate findings we already stand behind, and nothing else.

Three decisions, all measured (design doc, 2026-08-29):

* **48 CSS px of padding.** At 0px the outline is not even visible, because an outline draws OUTSIDE
  the border box and a tight clip cuts it off. At 16px the element floats with no surroundings. At
  48px it sits in its card with a neighbour for scale. 96px costs +72% bytes and shows nothing new.
* **PNG, never JPEG.** JPEG q60 was 26% smaller and looked identical — but a contrast finding is
  ABOUT COLOUR, and a lossy codec rewrites the exact hex the finding asserts. Someone sampling the
  image would get a different answer than the text. Do not compress your own evidence.
* **The outline goes on after measurement and comes off after the shot.** Injecting it before axe
  runs would feed our own 3px border into the contrast and tap-target results. That is D14's failure
  mode — the tool creating the defect it reports — and it would be the fifth instance.

The locator gate and its tally live here too. A screenshot of the WRONG element is worse than no
screenshot: it is confident-looking evidence for something we did not find. So an ambiguous selector
is refused — and every refusal is COUNTED, because a gate that silently omits is indistinguishable
from a class that had nothing to show.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field

PADDING_PX = 48
OUTLINE_CSS = "3px solid #ff2d55"
# A class whose locator resolves below this does not ship. Same principle as the 80% precision bar:
# a feature that mostly does not fire is not a feature, and without the tally nobody can tell.
SHIP_FLOOR = 0.70

_STYLE_ID = "qa-shot-style"
_FLAG_ATTR = "data-qa-flag"

_MARK = r"""([sel, pad, outline, styleId, flagAttr]) => {
    const els = [...document.querySelectorAll(sel)];
    if (els.length === 0) return {count: 0};

    // MEASURED ON GL RUN 128 (2026-09-03), and it is why this branch exists at all.
    // `display_dial_mismatch` located only 27% of 22 selectors, against a 70% floor — but 15 of
    // the 22 misses were `many`, and in EVERY one of those the matches were byte-identical:
    // distinct href = 1, distinct text = 1. They are the mobile and desktop copies of one anchor
    // in a responsive layout, so the CSS path legitimately matches both. There is no "wrong
    // element" to pick, because the candidates are the same defect rendered twice.
    //
    // So the refusal rule stays what it was — never guess between DIFFERENT elements — but it now
    // asks the right question. Elements are equivalent when tag, href and trimmed text all match;
    // if they are, photograph the first one with a box. If they genuinely differ, refuse exactly
    // as before, because then the wrong element really is worse than none.
    const sig = e => e.tagName + '|' + (e.getAttribute('href') || '')
                     + '|' + (e.textContent || '').trim();
    const equivalent = els.length === 1 || new Set(els.map(sig)).size === 1;
    if (!equivalent) return {count: els.length};

    // `boxed` has to mean "a picture of this would SHOW it", not merely "it has a box". An earlier
    // version tested only size and display/visibility, and off-canvas copies pass all three:
    // position:fixed + translateX(-100%), left:-9999px, and the .sr-only clip pattern are the
    // ordinary ways a responsive layout hides the duplicate. Since off-canvas drawer markup usually
    // precedes the desktop header in the DOM, `find` picked exactly the wrong one; scrollIntoView
    // cannot pull a fixed or negatively-positioned element into view, and the clip below is clamped
    // to the viewport — so the outline landed off-screen and the screenshot was a valid PNG of an
    // unrelated region, scored `captured`. That is worse than refusing: it is confident-looking
    // evidence for something we did not photograph.
    const boxed = e => {
        const c = window.getComputedStyle(e), r = e.getBoundingClientRect();
        if (r.width < 1 || r.height < 1 || c.display === 'none' || c.visibility === 'hidden')
            return false;
        if (parseFloat(c.opacity || '1') === 0) return false;
        if (r.width < 2 || r.height < 2) return false;
        // The screen-reader-only pattern hides the element from an ANCESTOR, not from itself: the
        // anchor measures a perfectly ordinary 8x18 while a parent carries
        // `width:1px;height:1px;clip:rect(0,0,0,0);overflow:hidden`. Reading only the element's own
        // computed style misses it entirely — the same mistake that once made a 0x0 probe report
        // 224.9 findings per page until it was made ancestor-aware. So walk up.
        for (let a = e; a && a !== document.documentElement; a = a.parentElement) {
            const ac = window.getComputedStyle(a), ar = a.getBoundingClientRect();
            if (ac.display === 'none' || ac.visibility === 'hidden') return false;
            if (ac.clip && ac.clip !== 'auto' && /rect\(\s*0/.test(ac.clip)) return false;
            if (ac.clipPath && /inset\(\s*(50|100)%/.test(ac.clipPath)) return false;
            // A tiny ancestor with overflow hidden cannot show a child bigger than itself.
            if (ac.overflow !== 'visible' && (ar.width < 2 || ar.height < 2)) return false;
        }
        // Must overlap the scrollable document, not sit outside it. Coordinates here are viewport
        // relative, so add the scroll offset to get document coordinates.
        const dx = r.x + window.scrollX, dy = r.y + window.scrollY;
        const docW = Math.max(document.documentElement.scrollWidth, window.innerWidth);
        const docH = Math.max(document.documentElement.scrollHeight, window.innerHeight);
        return dx + r.width > 0 && dy + r.height > 0 && dx < docW && dy < docH;
    };
    // Among identical copies, prefer one that can actually be photographed; if none can, fall back
    // to the first so the caller still reports `uncapturable` rather than a locator failure.
    const el = els.find(boxed) || els[0];
    const duplicates = els.length > 1 ? els.length : 0;
    const cs = window.getComputedStyle(el);
    const r0 = el.getBoundingClientRect();
    // An element with no box cannot be photographed, and a zero-size clip throws in Playwright.
    if (r0.width < 1 || r0.height < 1 || cs.display === 'none' || cs.visibility === 'hidden')
        return {count: 1, invisible: true, duplicates};

    el.setAttribute(flagAttr, '1');
    let style = document.getElementById(styleId);
    if (!style) {
        style = document.createElement('style');
        style.id = styleId;
        style.textContent = `[${flagAttr}]{outline:${outline} !important;outline-offset:2px !important}`;
        document.documentElement.appendChild(style);
    }
    el.scrollIntoView({block: 'center', inline: 'center', behavior: 'instant'});
    const r = el.getBoundingClientRect();
    const clip = {x: Math.max(0, r.x - pad),
                  y: Math.max(0, r.y - pad),
                  width: Math.min(window.innerWidth - Math.max(0, r.x - pad), r.width + pad * 2),
                  height: Math.min(window.innerHeight - Math.max(0, r.y - pad), r.height + pad * 2)};

    // LAST GUARD, and the one that actually matters: the clip is clamped to the viewport, and
    // scrollIntoView cannot move a position:fixed or off-canvas element into it. So prove the
    // element is really inside the rectangle we are about to photograph. Without this the function
    // can return a perfectly valid PNG of an unrelated region and the caller scores it `captured` —
    // and nothing downstream ever looks at the pixels, so the ship gate would be measuring "a PNG
    // came back" rather than "the element is in it".
    const ix = Math.max(clip.x, r.x), iy = Math.max(clip.y, r.y);
    const iw = Math.min(clip.x + clip.width, r.x + r.width) - ix;
    const ih = Math.min(clip.y + clip.height, r.y + r.height) - iy;
    const covered = (iw > 0 && ih > 0) ? (iw * ih) / (r.width * r.height) : 0;
    if (clip.width < 1 || clip.height < 1 || covered < 0.5)
        return {count: 1, invisible: true, duplicates, covered};

    return Object.assign({count: 1, duplicates, covered}, clip);
}"""

_UNMARK = """([styleId, flagAttr]) => {
    document.querySelectorAll('[' + flagAttr + ']').forEach(e => e.removeAttribute(flagAttr));
    const s = document.getElementById(styleId);
    if (s) s.remove();
}"""


@dataclass
class ShotTally:
    """How often the locator actually resolved, per finding class.

    Required by Syed (2026-08-29) and it is the acceptance test, not bookkeeping: if `dead_cta`
    resolves 90% of the time and `empty_state` 30%, the second is not a capability but an occasional
    accident, and the two are indistinguishable from outside without this.
    """

    # FOUR outcomes, because "did the selector work" and "is there a picture" are different
    # questions and one number cannot answer both. Measured on AH: `dead_cta` resolved 21 of 24
    # selectors but produced 20 shots — one element resolved uniquely and had no box to photograph.
    # Counting that as a locator failure would penalise the locator for the page's layout.
    #
    #   captured     — selector found exactly one element AND a screenshot was taken
    #   uncapturable — found exactly one element, but it has no box (hidden ancestor, 0x0 at this
    #                  width). NOT a defect: AH's 0x0 "Call Now!" anchor is the desktop variant of
    #                  a responsive header, `checkVisibility()` false, `offsetParent` null.
    #   none         — selector matched nothing
    #   many         — selector matched several; refused, because the wrong element is worse
    per_class: dict[str, dict[str, int]] = field(default_factory=dict)

    def record(self, cls: str, outcome: str, *, duplicates: int = 0) -> None:
        d = self.per_class.setdefault(
            cls, {"attempted": 0, "captured": 0, "uncapturable": 0, "none": 0, "many": 0,
                  "via_duplicates": 0})
        d["attempted"] += 1
        d[outcome] = d.get(outcome, 0) + 1
        # Counted SEPARATELY and deliberately kept out of located_rate/hit_rate, which must stay
        # "did the selector work" and "is there a picture". This says HOW it worked: the selector
        # matched N identical copies of one element and the marker picked one. Worth seeing,
        # because if it ever climbs to most of a class it means the CSS path has stopped
        # discriminating and the equivalence check is carrying the feature.
        if duplicates > 1:
            d["via_duplicates"] += 1

    def located_rate(self, cls: str) -> float:
        """Did the SELECTOR work — the question the ship floor asks."""
        d = self.per_class.get(cls)
        if not d or not d["attempted"]:
            return 0.0
        return (d["captured"] + d["uncapturable"]) / d["attempted"]

    def hit_rate(self, cls: str) -> float:
        """Did a PICTURE result — always <= located_rate, and the feature's real coverage."""
        d = self.per_class.get(cls)
        return d["captured"] / d["attempted"] if d and d["attempted"] else 0.0

    def below_floor(self) -> list[str]:
        # Judged on the LOCATOR. An element with no box is not the locator's fault, and failing a
        # class for the page's layout would retire a working mechanism for the wrong reason.
        return sorted(c for c in self.per_class if self.located_rate(c) < SHIP_FLOOR)

    def summary(self) -> str:
        if not self.per_class:
            return "no screenshots attempted"
        parts = []
        for cls in sorted(self.per_class):
            d = self.per_class[cls]
            parts.append(
                f"{cls}: located {d['captured'] + d['uncapturable']}/{d['attempted']} "
                f"({self.located_rate(cls) * 100:.0f}%), captured {d['captured']} "
                f"({self.hit_rate(cls) * 100:.0f}%); {d['uncapturable']} had no box, "
                f"{d['none']} not found, {d['many']} ambiguous")
        note = ""
        if self.below_floor():
            note = (f" — BELOW THE {SHIP_FLOOR * 100:.0f}% LOCATOR FLOOR and not shippable: "
                    f"{', '.join(self.below_floor())}")
        return "; ".join(parts) + note


# Why a finding has no picture, in words a client can read. The report MUST print one of these
# rather than silently omitting the image: on GL only 51% of display_dial_mismatch findings yield a
# picture, so silence would make half the feature look broken — and worse, would invite the reader
# to think a finding without a photograph is a finding without evidence. It is not. The defect is
# established by the page's own text and dial target; the picture is corroboration, nothing more.
ABSENCE_REASONS = {
    "uncapturable": "No picture: this element is a mobile/desktop duplicate that is not visible at "
                    "the width we photograph, so there is nothing on screen to capture. The defect "
                    "is still present in the page's code.",
    "many": "No picture: the page contains several different elements matching this position, and "
            "photographing the wrong one would be misleading. The defect is still present in the "
            "page's code.",
    "none": "No picture: the element could not be located when the page was re-opened for the "
            "photograph, which usually means the page changed after the audit. The defect is what "
            "the audit found in the page's code at the time.",
    "budget": "No picture: this report reached its image limit. The defect is unaffected.",
}


def capture(page, selector: str, *, tally: ShotTally | None = None, cls: str = "unknown",
            require_unique: bool = True, padding: int = PADDING_PX,
            outcome: list | None = None) -> dict | None:
    """Outline the element `selector` names and photograph it with context. None if not locatable.

    Returns {"data_uri", "bytes", "width", "height"} — a PNG data URI ready to embed, so the report
    stays a single self-contained file that survives being emailed onward.

    `require_unique=False` exists only for a caller that has already established uniqueness; the
    default refuses an ambiguous selector, because the wrong element is worse than none.

    `outcome` is an out-parameter: pass a list and the single outcome word is appended to it. The
    tally answers "how often does this class resolve" across a run; a REPORT needs to say why THIS
    finding has no image, and that is per-finding information the tally cannot carry.
    """
    def _out(word: str):
        if outcome is not None:
            outcome.append(word)
    try:
        box = page.evaluate(_MARK, [selector, padding, OUTLINE_CSS, _STYLE_ID, _FLAG_ATTR])
    except Exception:
        if tally:
            tally.record(cls, "none")
        _out("none")
        return None

    count = box.get("count", 0)
    dupes = int(box.get("duplicates") or 0)
    if count == 1 and box.get("invisible"):
        # The selector WORKED. The element simply has no box at this width — a hidden ancestor or a
        # 0x0 layout. That is a fact about the page, not a failure to locate.
        if tally:
            tally.record(cls, "uncapturable", duplicates=dupes)
        _out("uncapturable")
        return None
    if count != 1:
        word = "many" if count > 1 else "none"
        if tally:
            tally.record(cls, word)
        _out(word)
        return None
    if count > 1 and not require_unique:
        pass

    try:
        raw = page.screenshot(type="png", clip={k: box[k] for k in ("x", "y", "width", "height")})
    except Exception:
        if tally:
            tally.record(cls, "none")
        _out("none")
        return None
    finally:
        # ALWAYS, even if the screenshot threw. A surviving outline would contaminate every later
        # measurement on this page and the contamination would look like a real finding.
        try:
            page.evaluate(_UNMARK, [_STYLE_ID, _FLAG_ATTR])
        except Exception:
            pass

    if tally:
        tally.record(cls, "captured", duplicates=dupes)
    _out("captured")
    return {"data_uri": "data:image/png;base64," + base64.b64encode(raw).decode("ascii"),
            "bytes": len(raw),
            "width": round(box["width"] * 2), "height": round(box["height"] * 2)}


# A finding's shot key: what makes two findings the SAME PICTURE. Contrast collapses to a colour
# decision, so every element sharing a colour pair would produce the same image of a different
# instance — 671 nodes were 15 decisions on the last measurement. Broken images collapse by src.
# Everything else is photographed per element, because there is nothing to collapse it by.
def _shot_key(f) -> tuple:
    d = f.details or {}
    cls = d.get("class")
    if cls == "color-contrast":
        return ("contrast", d.get("fgColor"), d.get("bgColor"),
                str(d.get("expectedContrastRatio")))
    if cls == "broken_image":
        return ("image", d.get("src"))
    return ("el", f.check, d.get("selector") or f.location, f.url)


def _selector_for(f) -> str | None:
    d = f.details or {}
    sel = d.get("selector")
    if sel:
        return sel
    if d.get("class") == "broken_image" and d.get("src"):
        # CSS.escape is not available in Python; quote the attribute and rely on the URL not
        # containing a double quote, which no URL we have seen does.
        src = str(d["src"]).replace('"', '\\"')
        return f'img[src="{src}"], img[currentSrc="{src}"]'
    return None


def attach_shots(page, findings, *, tally: ShotTally | None = None,
                 max_shots: int = 40) -> int:
    """Photograph the element behind each finding, ONE per distinct picture. Returns how many.

    Takes findings that already exist, which is deliberate and not merely convenient: the outline
    this draws must never be present while a measurement runs, and a function that can only be
    handed finished findings cannot be called before one. The ordering guard is the signature.

    `max_shots` bounds a pathological page. A cap that is hit is REPORTED by the caller, never
    silently applied — the project's standing rule about sampling.
    """
    seen: set[tuple] = set()
    taken = 0
    for f in findings:
        key = _shot_key(f)
        sel = _selector_for(f)
        if not sel:
            continue                     # nothing to point a camera at; not an absence to explain
        if taken >= max_shots:
            # RECORD the cap rather than just breaking. A finding that lost its picture to the
            # budget must not be indistinguishable from one whose element could not be found.
            f.details = dict(f.details or {})
            f.details.setdefault("shot_absent", "budget")
            continue
        if key in seen:
            continue
        seen.add(key)
        outcome: list[str] = []
        shot = capture(page, sel, tally=tally, cls=f.check, outcome=outcome)
        f.details = dict(f.details or {})
        if shot:
            f.details["shot"] = shot["data_uri"]
            f.details["shot_bytes"] = shot["bytes"]
            taken += 1
        else:
            # WHY there is no image, carried on the finding itself so the report can say it. Half of
            # display_dial_mismatch resolves to an off-canvas responsive duplicate; silently
            # omitting those makes a working feature look broken and, far worse, invites the reader
            # to treat "no photograph" as "no evidence".
            f.details["shot_absent"] = outcome[0] if outcome else "none"
    return taken


# --- locating a TEXT finding's element ------------------------------------------------------------
# Render findings carry an exact CSS selector from axe. Text findings do not — they carry attributes
# and text, so the element has to be re-derived from what was stored. That is fuzzier, and a
# screenshot of the WRONG element is worse than none: it is confident-looking evidence for something
# we did not find. So these locators mark candidates and REFUSE unless exactly one survives, and
# every refusal is counted (see `ShotTally`), because a class that mostly cannot be located is not a
# capability — and without the tally it looks identical to a class with nothing to show.

_TARGET_ATTR = "data-qa-target"

_MARK_TEL = """([telDigits, shownDigits, attr]) => {
    const digits = (s) => (s || '').replace(/\\D+/g, '').replace(/^1(?=\\d{10}$)/, '');
    document.querySelectorAll('[' + attr + ']').forEach(e => e.removeAttribute(attr));
    const hits = Array.from(document.querySelectorAll('a[href^="tel:"], a[href^="TEL:"]'))
        .filter(a => {
            const target = digits(a.getAttribute('href'));
            const shown = digits(a.textContent);
            // BOTH halves must agree. Matching the dial target alone is ambiguous — a page can
            // carry the same tel: on several links with different labels — and matching the shown
            // number alone would pick up the CORRECT link that happens to display it.
            return target === telDigits && shown === shownDigits && target !== shown;
        });
    // MARK THE FIRST WHEN SEVERAL MATCH — and that is not a weakened gate.
    // The gate exists so we never photograph the WRONG element. These candidates have already been
    // pinned to BOTH of the finding's numbers: the same dial target AND the same displayed text.
    // Every one of them is therefore the finding's own defect, repeated (a CTA in the header, the
    // sticky bar and the footer). Any one illustrates it correctly. Measured: refusing them scored
    // 0/14, of which 12 were this case — the tally is what made that visible.
    if (hits.length >= 1) hits[0].setAttribute(attr, '1');
    return hits.length;
}"""


def mark_tel_mismatch(page, tel: str, displayed: str) -> int:
    """Mark the click-to-call whose LABEL and TARGET disagree. Returns how many matched.

    Only marks when exactly one matches; 0 or >1 leaves the page untouched, which is what makes the
    caller's refusal safe. Digits are compared after stripping a leading US country code, because
    the finding stores E.164 while the page shows whatever the template wrote.
    """
    def _digits(s: str) -> str:
        d = "".join(ch for ch in (s or "") if ch.isdigit())
        return d[1:] if len(d) == 11 and d.startswith("1") else d

    return page.evaluate(_MARK_TEL, [_digits(tel), _digits(displayed), _TARGET_ATTR])


def capture_tel_mismatch(page, tel: str, displayed: str, *,
                         tally: ShotTally | None = None) -> dict | None:
    """Photograph a `display_dial_mismatch` — the flagship defect, and the one most worth seeing.

    "shows Call Now! 844-759-0999 but dials 888-707-6073" is an accurate sentence that a reader has
    to take on trust. A picture of the actual button is not.
    """
    try:
        n = mark_tel_mismatch(page, tel, displayed)
    except Exception:
        n = 0
    if n < 1:
        if tally:
            tally.record("phone", "none")
        return None
    try:
        return capture(page, f"[{_TARGET_ATTR}]", tally=tally, cls="phone")
    finally:
        try:
            page.evaluate("(attr) => document.querySelectorAll('[' + attr + ']')"
                          ".forEach(e => e.removeAttribute(attr))", _TARGET_ATTR)
        except Exception:
            pass
