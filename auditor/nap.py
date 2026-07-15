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

from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field

from .checks.phone import normalize

# 0-based column indices into a full NAP row.
COL_E, COL_H, COL_I, COL_BD = 4, 7, 8, 55

BRAND_TOKENS = {"RR", "GL", "CAD", "COC", "AR", "DBH", "TDRC", "AH", "MHD", "SLN"}
# Not live yet (CLAUDE.md §6) — legitimately has no NAP numbers; excluded from the
# zero-canonical loud-failure so it doesn't false-alarm every run.
NOT_LIVE_BRANDS = frozenset({"SLN"})

# 2026-07-02 SNAPSHOT. Live read (D2) swaps grid_from_xlsx -> grid_from_sheet.
NAP_SNAPSHOT = Path.home() / "Documents/workk/district/.tmp_dd/jake_sites.xlsx"
NAP_TAB = "NAP (Current)"
# Plan [c] ID — UNVERIFIED as the live canonical sheet; Syed confirms before live use.
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


def grid_from_xlsx(path: str | Path = NAP_SNAPSHOT, tab: str = NAP_TAB) -> list[list]:
    """Read the NAP tab from the .tmp_dd snapshot xlsx into raw rows (padded)."""
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


def load_canonical_from_snapshot() -> dict[str, CanonicalNumbers]:
    """Convenience: parse the 2026-07-02 snapshot. Live read is a separate source fn."""
    return parse_nap_grid(grid_from_xlsx())
