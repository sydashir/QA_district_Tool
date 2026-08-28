"""NAP-sheet canonical-phone parser (P1 / D2).

Turns the client NAP sheet into a per-brand canonical-number model the phone check
uses to classify each found number as clean / stale-retired / unknown.

SOURCE SPLIT (deliberate, per D2): ``parse_nap_grid`` is pure and source-agnostic —
it takes a raw grid (rows of cells). ``grid_from_xlsx`` reads the 2026-07-02 .tmp_dd
SNAPSHOT; the live-sheet read swaps in a ``grid_from_sheet`` source (SheetsService,
readonly) and NOTHING in the parse logic changes.

Grid layout (verified against the real "NAP (Current)" cells, 0-based columns):
- col E (4)  : brand token on a header row (RR/GL/CAD/...); facility name on a location row
- col H (7)  : "SEO Target"/"PPC Target"/"National"/blank on headers; nickname on locations
- col I (8)  : THE number (national on header rows, local on location rows)
- col BD (55): "OLD Hardcoded 800 #s" — per-row retired number
RR and GL uniquely carry the PPC national on the row AFTER the header (blank E, H="PPC Target").
"""
from __future__ import annotations

import logging
import re
from functools import lru_cache
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field

from .checks.phone import normalize

_log = logging.getLogger(__name__)

# 0-based column indices into a full NAP row.
COL_E, COL_H, COL_I, COL_BD = 4, 7, 8, 55

BRAND_TOKENS = {"RR", "GL", "CAD", "COC", "AR", "DBH", "TDRC", "AH", "MHD", "SLN"}
# Not live yet (CLAUDE.md §6) — legitimately has no NAP numbers; excluded from the
# zero-canonical loud-failure so it doesn't false-alarm every run.
NOT_LIVE_BRANDS = frozenset({"SLN"})

# NAP snapshot — repo-local, gitignored copy of Syed's authoritative export (2026-07-20; the
# older .tmp_dd/jake_sites.xlsx was 2026-07-02, missing the AH/CAD/MHD call-center numbers).
# Refresh = drop a new export at this path. Durable fix is the live gspread read (D2 backlog):
# grid_from_xlsx -> grid_from_sheet, parse unchanged.
NAP_SNAPSHOT = Path(__file__).resolve().parent.parent / "data" / "nap_snapshot.xlsx"

# The SECOND NAP source, and a deliberately SEPARATE one. Syed supplied a newer tab that is
# TRANSPOSED relative to the one above — brands run across the COLUMNS, fields down the rows — and
# it carries two things "NAP (Current)" does not: the per-location BUSINESS NAME and STREET ADDRESS.
#
# UNION, NOT REPLACEMENT. Ruled by Syed after checking both, and the reason must survive anyone
# later "simplifying" the loader to one source: **on phones the new tab is a strict SUBSET.** It is
# missing 10 per-location call-centre numbers (AH/CAD/MHD: San Jose, Sacramento, Santa Cruz, Palm
# Desert) that the old tab has — searched the whole file, absent everywhere. Drop the old tab and
# those ten numbers leave the canonical set, so every page that carries one starts reporting as an
# unknown number: a fresh wave of false findings, caused by a tidy-up. There are ZERO conflicts
# between the two on the numbers they share.
#
#   phones          -> "NAP (Current)" snapshot   (load_canonical_from_snapshot, above)
#   name + address  -> this tab                   (load_nap_locations, below)
#
# `tests/test_nap_locations.py` pins the subset relationship, so if it ever stops being true the
# suite says so and the decision gets revisited deliberately.
NAP_LOCATIONS_CSV = Path(__file__).resolve().parent.parent / "data" / "nap_locations.csv"

# A cell in the NAP-Name row holding exactly one of these OPENS that brand's block of columns.
_BRAND_CODES = {"RR", "GL", "CAD", "COC", "AR", "DBH", "TDRC", "AH", "MHD"}

# Row labels, matched on a normalised prefix of column 0. Rows are located BY LABEL, never by
# index: these sheets are re-exported by hand and positions drift — the Fetcher's own newer modules
# resolve every column by header name for exactly this reason, after one brand renamed a column.
_ROW_LABELS = {
    "name": "nap name",
    "address": "nap address",
    "nickname": "nickname",
    "phone": "nap phone",
}


@dataclass(frozen=True)
class NapLocation:
    """One physical place: what the business is CALLED and WHERE it is, per the client's own NAP."""
    brand: str
    name: str
    address: str
    nickname: str | None = None
    phone: str | None = None      # E.164, or None when unparseable


def _find_row(rows: list[list[str]], key: str) -> list[str] | None:
    want = _ROW_LABELS[key]
    for r in rows:
        if r and r[0] and r[0].strip().lower().replace("\n", " ").startswith(want):
            return r
    return None


def locations_from_grid(rows: list[list[str]]) -> dict[str, list[NapLocation]]:
    """Parse the transposed tab into {brand_code: [NapLocation]}. Empty dict if it isn't that tab.

    A column becomes a location only when it has a real ADDRESS. The two columns that open each
    brand block are the SEO and PPC routing numbers — a phone with no place attached — and treating
    them as locations would invent addressless businesses to match pages against.
    """
    name_row = _find_row(rows, "name")
    addr_row = _find_row(rows, "address")
    if not name_row or not addr_row:
        return {}
    nick_row = _find_row(rows, "nickname") or []
    phone_row = _find_row(rows, "phone") or []

    def cell(row: list[str], i: int) -> str:
        return row[i].strip() if i < len(row) and row[i] else ""

    out: dict[str, list[NapLocation]] = {}
    current: str | None = None
    for i in range(1, max(len(name_row), len(addr_row))):
        nm = cell(name_row, i)
        if nm.upper() in _BRAND_CODES:      # a bare brand code opens that brand's block
            current = nm.lower()
            out.setdefault(current, [])
            continue
        if current is None:
            continue
        address = cell(addr_row, i)
        if not address or not nm:
            continue                        # routing-number column, or a spacer
        out[current].append(NapLocation(
            brand=current, name=nm, address=address,
            nickname=cell(nick_row, i) or None,
            phone=normalize(cell(phone_row, i)) or None))
    return out


def grid_from_csv(path: Path = None) -> list[list[str]]:
    import csv
    with open(path or NAP_LOCATIONS_CSV, newline="", encoding="utf-8-sig") as fh:
        return [row for row in csv.reader(fh)]


def load_nap_locations(path: Path = None) -> dict[str, list[NapLocation]]:
    """Per-brand physical locations from the transposed tab. Empty on any failure — the checks that
    use it must DEGRADE (skip), never crash a nine-brand run over a missing local file."""
    try:
        return locations_from_grid(grid_from_csv(path))
    except (FileNotFoundError, OSError, ValueError) as e:
        _log.warning("NAP locations tab unavailable (%s); name/address checks will not run", e)
        return {}
NAP_TAB = "NAP (Current)"
# VERIFIED 2026-08-03 as the live canonical sheet: reading it with the service account returns
# title "NAP Phone numbers / UTM Codes / DBAs" with a "NAP (Current)" tab — exactly the tab this
# module parses from the snapshot. Safe to use as the live source (D2).
NAP_SHEET_ID = "1AU_wNukifVPc6yH7pvXW051llwOnf7RTDx9hZDVgF-c"


class NationalNumber(BaseModel):
    number: str  # E.164
    channel: str | None = None  # "SEO" | "PPC" | None


class CanonicalNumbers(BaseModel):
    brand: str
    national: list[NationalNumber] = Field(default_factory=list)
    per_location: dict[str, str] = Field(default_factory=dict)  # nickname -> E.164
    stale_retired: list[str] = Field(default_factory=list)  # E.164

    def current_set(self) -> set[str]:
        return {n.number for n in self.national} | set(self.per_location.values())


@dataclass
class NapProblem:
    brand: str
    reason: str


_ZERO_WIDTH = "\u200b\u200c\u200d\ufeff"  # zero-width space/joiner/BOM — drop entirely


def _norm_ws(s: str) -> str:
    """Collapse unicode whitespace (incl NBSP U+00A0) to single ASCII spaces and strip
    zero-width chars. The Fetcher's geo_field_validator went NBSP-tolerant because a
    Google-Docs-paste artifact silently broke parsing in production once; the live NAP
    read (D2) may carry the same, so normalize before matching brand tokens / numbers.
    (The 2026-07-02 snapshot was clean; this is forward-looking insurance.)"""
    for z in _ZERO_WIDTH:
        s = s.replace(z, "")
    return " ".join(s.split())  # str.split() breaks on NBSP too -> collapses + strips


def _cell(row, idx: int) -> str:
    if idx < len(row) and row[idx] is not None:
        return _norm_ws(str(row[idx]))
    return ""


def _channel(h: str) -> str | None:
    low = h.lower()
    if "seo" in low:
        return "SEO"
    if "ppc" in low:
        return "PPC"
    return None


def parse_nap_grid(rows) -> dict[str, CanonicalNumbers]:
    """Parse raw NAP rows into per-brand canonical numbers. Source-agnostic."""
    brands: dict[str, CanonicalNumbers] = {}
    current: CanonicalNumbers | None = None

    def add_stale(cn: CanonicalNumbers, raw: str) -> None:
        num = normalize(raw)
        if num:
            cn.stale_retired.append(num)

    for row in rows:
        e, h, i, bd = _cell(row, COL_E), _cell(row, COL_H), _cell(row, COL_I), _cell(row, COL_BD)
        token = e.upper()

        if token in BRAND_TOKENS:
            current = CanonicalNumbers(brand=token)
            brands[token] = current
            num = normalize(i)
            if num:
                current.national.append(NationalNumber(number=num, channel=_channel(h)))
            add_stale(current, bd)
            continue

        if current is None:
            continue  # pre-first-brand header rows

        if not e and h and _channel(h):  # PPC/SEO continuation row (blank E)
            num = normalize(i)
            if num:
                current.national.append(NationalNumber(number=num, channel=_channel(h)))
            add_stale(current, bd)
        elif e:  # location row
            num = normalize(i)
            if num:
                current.per_location[(h or e).strip()] = num
            add_stale(current, bd)
        else:
            add_stale(current, bd)

    # current wins: a number that is a live canonical is not "retired" for that brand.
    for cn in brands.values():
        live = cn.current_set()
        cn.stale_retired = sorted(set(cn.stale_retired) - live)
    return brands


def validate_canonical(
    brands: dict[str, CanonicalNumbers], ignore: frozenset[str] | set[str] = frozenset()
) -> list[NapProblem]:
    """The GeoData Check #2 trap: a brand that parses to ZERO canonical numbers is a
    LOUD failure, never a silent clean pass (a check whose input pattern didn't match
    is worse than no check). Returns one NapProblem per offending brand. ``ignore``
    lists known-not-live brands (e.g. NOT_LIVE_BRANDS) that are expected to be empty."""
    return [
        NapProblem(brand=b, reason="no canonical number parsed")
        for b, cn in brands.items()
        if not cn.national and b not in ignore
    ]


@lru_cache(maxsize=4)
def grid_from_xlsx(path: str | Path = NAP_SNAPSHOT, tab: str = NAP_TAB) -> list[list]:
    """Read the NAP tab from the .tmp_dd snapshot xlsx into raw rows (padded).

    Cached because a single ``load_brand()`` parses this workbook TWICE — once via
    ``load_canonical_from_snapshot`` and once via ``load_third_party`` — and openpyxl spends ~25s
    per parse, which made ``load_brand()`` a 51s call. The snapshot is a static file that cannot
    change mid-run, and both consumers (``parse_nap_grid``, ``third_party_hotlines``) only READ the
    grid, so handing back the same list is safe. A process that rewrites the xlsx in place must
    call ``grid_from_xlsx.cache_clear()``.
    """
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[tab]
    rows: list[list] = []
    width = COL_BD + 1
    for r in ws.iter_rows(values_only=True):
        row = list(r)
        if len(row) < width:
            row += [None] * (width - len(row))
        rows.append(row)
    wb.close()
    return rows


def grid_from_sheet(values) -> list[list]:
    """Shape live-sheet values (e.g. a gspread ``worksheet.get_all_values()``) into the
    SAME padded rows as ``grid_from_xlsx``. Written now, before the live read exists, on
    purpose: the gspread FETCH lives outside this module (needs creds, D2) and passes its
    values in here, so the snapshot->live swap is a loader CALL — never an edit to this
    parse module. An edit here would move the phone check-version and churn the baseline
    (the whole reason P1 lands before the baseline run)."""
    rows: list[list] = []
    width = COL_BD + 1
    for r in values:
        row = list(r)
        if len(row) < width:
            row += [None] * (width - len(row))
        rows.append(row)
    return rows


def load_canonical_from_snapshot() -> dict[str, CanonicalNumbers]:
    """Convenience: parse the 2026-07-02 snapshot. Live read is a separate source fn."""
    return parse_nap_grid(grid_from_xlsx())


# Global crisis-hotline columns in the NAP header row (Poison Control, SAMHSA, Lifeline, RAINN).
THIRD_PARTY_COLS = (100, 101, 102, 103)


def _strip_paren_decode(raw: str) -> str:
    """'1-800-662-HELP (4357)' -> '1-800-662-HELP' — drop the parenthetical keypad decode so the
    vanity part normalizes (normalize() converts HELP->4357 itself)."""
    return re.sub(r"\s*\([\d\s\-]+\)\s*$", "", raw).strip()


def third_party_hotlines(grid) -> set[str]:
    """Global known third-party hotlines from the NAP header row (cols 100-103). A rehab site
    listing Poison Control / SAMHSA / Lifeline / RAINN is EXPECTED behaviour the client has
    documented — NOT an unknown-number defect. Source-agnostic like ``parse_nap_grid``."""
    out: set[str] = set()
    row = grid[0] if grid else []
    for c in THIRD_PARTY_COLS:
        if c < len(row):
            n = normalize(_strip_paren_decode(_norm_ws(str(row[c] or ""))))
            if n:
                out.add(n)
    return out


def load_third_party(loader=grid_from_xlsx) -> set[str]:
    """Best-effort global third-party hotline set from the snapshot. Empty on failure (the
    phone check then just classifies those numbers 'unknown' — visible, not silently wrong)."""
    try:
        return third_party_hotlines(loader())
    except (FileNotFoundError, OSError, ValueError, KeyError) as e:
        _log.warning("NAP third-party hotlines unavailable (%s)", e)
        return set()


def brand_number_owners(loader=load_canonical_from_snapshot) -> dict[str, list[str]]:
    """Map every LIVE canonical number (national + per_location) -> the brand(s) that own it,
    across ALL brands. Lets the phone check tell a benign call-tracking mismatch (dials one of
    THIS brand's own numbers) from a cross-brand leak (dials ANOTHER brand's number, e.g. a COC
    page dialing GL's line). Verified 2026-07-20: no number is shared across brands, but the map
    carries the full owner set so a future shared number is flagged ambiguous, not miscalled."""
    try:
        brands = loader()
    except (FileNotFoundError, OSError, ValueError, KeyError) as e:
        _log.warning("NAP brand-number map unavailable (%s)", e)
        return {}
    owners: dict[str, set[str]] = {}
    for b, c in brands.items():
        for n in c.current_set():
            owners.setdefault(n, set()).add(b)
    return {n: sorted(bs) for n, bs in owners.items()}


def canon_for(brand: str, loader=load_canonical_from_snapshot) -> CanonicalNumbers | None:
    """Best-effort per-brand canonical from the NAP snapshot. Returns None (with a loud
    log) if the snapshot is unavailable/unreadable — the phone check then DEGRADES to the
    flat ``config.canonical_phones`` (still audits; just loses the stale/unknown split).
    That degradation is logged, never silent (a silent fallback is the Check #2 trap)."""
    try:
        return loader().get(brand.upper())
    except (FileNotFoundError, OSError, ValueError, KeyError) as e:  # snapshot missing/broken
        _log.warning("NAP snapshot unavailable for %s (%s); phone check falls back to "
                     "flat canonical_phones — no stale-retired classification this run", brand, e)
        return None
