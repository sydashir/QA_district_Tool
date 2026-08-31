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

_MARK = """([sel, pad, outline, styleId, flagAttr]) => {
    const els = document.querySelectorAll(sel);
    if (els.length !== 1) return {count: els.length};
    const el = els[0];
    const cs = window.getComputedStyle(el);
    const r0 = el.getBoundingClientRect();
    // An element with no box cannot be photographed, and a zero-size clip throws in Playwright.
    if (r0.width < 1 || r0.height < 1 || cs.display === 'none' || cs.visibility === 'hidden')
        return {count: 1, invisible: true};

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
    return {count: 1,
            x: Math.max(0, r.x - pad),
            y: Math.max(0, r.y - pad),
            width: Math.min(window.innerWidth - Math.max(0, r.x - pad), r.width + pad * 2),
            height: Math.min(window.innerHeight - Math.max(0, r.y - pad), r.height + pad * 2)};
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

    per_class: dict[str, dict[str, int]] = field(default_factory=dict)

    def record(self, cls: str, outcome: str) -> None:
        d = self.per_class.setdefault(cls, {"attempted": 0, "one": 0, "none": 0, "many": 0})
        d["attempted"] += 1
        d[outcome] = d.get(outcome, 0) + 1

    def hit_rate(self, cls: str) -> float:
        d = self.per_class.get(cls)
        return d["one"] / d["attempted"] if d and d["attempted"] else 0.0

    def below_floor(self) -> list[str]:
        return sorted(c for c in self.per_class if self.hit_rate(c) < SHIP_FLOOR)

    def summary(self) -> str:
        if not self.per_class:
            return "no screenshots attempted"
        parts = []
        for cls in sorted(self.per_class):
            d = self.per_class[cls]
            parts.append(f"{cls}: {d['one']}/{d['attempted']} located "
                         f"({self.hit_rate(cls) * 100:.0f}%; {d['none']} not found, "
                         f"{d['many']} ambiguous)")
        note = ""
        if self.below_floor():
            note = (f" — BELOW THE {SHIP_FLOOR * 100:.0f}% FLOOR and not shippable: "
                    f"{', '.join(self.below_floor())}")
        return "; ".join(parts) + note


def capture(page, selector: str, *, tally: ShotTally | None = None, cls: str = "unknown",
            require_unique: bool = True, padding: int = PADDING_PX) -> dict | None:
    """Outline the element `selector` names and photograph it with context. None if not locatable.

    Returns {"data_uri", "bytes", "width", "height"} — a PNG data URI ready to embed, so the report
    stays a single self-contained file that survives being emailed onward.

    `require_unique=False` exists only for a caller that has already established uniqueness; the
    default refuses an ambiguous selector, because the wrong element is worse than none.
    """
    try:
        box = page.evaluate(_MARK, [selector, padding, OUTLINE_CSS, _STYLE_ID, _FLAG_ATTR])
    except Exception:
        if tally:
            tally.record(cls, "none")
        return None

    count = box.get("count", 0)
    if count != 1 or box.get("invisible"):
        if tally:
            tally.record(cls, "many" if count > 1 else "none")
        return None
    if count > 1 and not require_unique:
        pass

    try:
        raw = page.screenshot(type="png", clip={k: box[k] for k in ("x", "y", "width", "height")})
    except Exception:
        if tally:
            tally.record(cls, "none")
        return None
    finally:
        # ALWAYS, even if the screenshot threw. A surviving outline would contaminate every later
        # measurement on this page and the contamination would look like a real finding.
        try:
            page.evaluate(_UNMARK, [_STYLE_ID, _FLAG_ATTR])
        except Exception:
            pass

    if tally:
        tally.record(cls, "one")
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
        if taken >= max_shots:
            break
        key = _shot_key(f)
        if key in seen:
            continue
        sel = _selector_for(f)
        if not sel:
            continue
        seen.add(key)
        shot = capture(page, sel, tally=tally, cls=f.check)
        if shot:
            f.details = dict(f.details or {})
            f.details["shot"] = shot["data_uri"]
            f.details["shot_bytes"] = shot["bytes"]
            taken += 1
    return taken
