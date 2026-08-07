"""An empty slot sitting beside populated ones (v1 deterministic) — B4.

Reported five times: "this table row is blank", "the accordion has an empty entry", "there is a
'read more' with no read-more button", "the widget is empty". The page looks finished at a glance
because the structure is all there — only the value is missing. That is the same failure
`blank.py` catches at PAGE level, one level down: `blank.py` asks whether a page has content,
this asks whether one CELL of a populated group does.

**The discriminator is the group, not the cell.** An empty `<td>` on its own means nothing —
layout tables are full of spacer cells, and plenty of designs use a blank cell deliberately. An
empty `<td>` in a row where every OTHER cell carries text is a value that failed to render. So a
finding needs a group with a clear majority populated and a small minority empty; a half-empty
group is a layout, not a defect.

Media counts as content. An icon-only cell is populated — `parse.py` records `has_media` for
exactly this reason, because keying on text alone would flag every icon in every comparison table.
"""
from __future__ import annotations

from collections import Counter, defaultdict

from ..parse import ParsedPage
from ..report import Finding, Severity, make_fingerprint

CHECK = "empty_row"

# Tags where an empty slot is a missing VALUE. <p> is excluded: an empty paragraph is a spacer,
# used constantly by page builders, and carries no promise of content.
_SLOT_TAGS = frozenset({"td", "li", "dd"})
_MIN_SIBLINGS = 3          # need a real group before "the odd one out" means anything
# At least this share of the INTERIOR must carry content. Measured on the interior rather than the
# whole group, which is smaller, so the bar sits lower than it looks: one gap in a four-cell row
# leaves a three-cell interior at 0.67. The heavy lifting against half-empty layouts is done by
# _MAX_EMPTY, not by this.
_MIN_POPULATED = 0.6
_MAX_EMPTY = 2             # more than a couple of blanks is a layout, not a failed merge


def _empty_slots(parsed: ParsedPage):
    groups: dict[tuple[int, str], list] = defaultdict(list)
    for b in parsed.blocks:
        if b.region == "body" and b.tag in _SLOT_TAGS:
            groups[(b.group, b.tag)].append(b)
    for (_gid, tag), blocks in groups.items():
        if len(blocks) < _MIN_SIBLINGS:
            continue
        # TRAILING blanks are grid padding, not missing values. AR's "Addictions Alliance Recovery
        # Treats" table lays names out five per row, so its last row is four names and one empty
        # cell — measured, this single shape was 492 of 492 findings across 507 live pages. A gap
        # only means something when populated content FOLLOWS it.
        last_filled = max((i for i, b in enumerate(blocks) if b.text or b.has_media), default=-1)
        interior = blocks[:last_filled]
        if len(interior) < _MIN_SIBLINGS:
            continue
        empty = [b for b in interior if not b.text and not b.has_media]
        if not empty or len(empty) > _MAX_EMPTY:
            continue
        populated = len(interior) - len(empty)
        if populated / len(interior) < _MIN_POPULATED:
            continue                      # half-empty -> a layout, not a missing value
        example = next((b.text for b in interior if b.text), "")
        yield tag, len(empty), len(interior), example


def run(parsed: ParsedPage, config) -> list[Finding]:
    findings: list[Finding] = []
    seen: Counter = Counter()
    label = {"td": "table cell", "li": "list item", "dd": "list entry"}
    for tag, n_empty, n_total, example in _empty_slots(parsed):
        what = label.get(tag, tag)
        key = (tag, n_empty, n_total, example[:40])
        occ = seen[key]
        seen[key] += 1
        slot = "empty_row" if occ == 0 else f"empty_row#{occ}"
        findings.append(Finding(
            url=parsed.url, check=CHECK, severity=Severity.WARNING,
            fingerprint=make_fingerprint(CHECK, slot, parsed.url, tag, example[:60]),
            issue=f"{n_empty} blank {what}{'s' if n_empty > 1 else ''} "
                  f"in a group of {n_total}",
            location="page body",
            snippet=(f"beside: {example[:120]}" if example else what),
            suggestion=(f"{n_empty} of the {n_total} {what}s in this group are empty while the "
                        f"rest have content, so a value did not come through. The page still "
                        f"looks complete, which is why this kind of gap survives review."),
            details={"class": "empty_row", "tag": tag, "empty": n_empty,
                     "total": n_total, "example": example[:200]}))
    return findings
