"""Client triage state that survives the nightly rewrite.

The `<BRAND> — open` tab is rewritten every run, and it also carries two HUMAN columns — `status`
and `note`. So the run must READ what the client wrote before it writes, and re-attach it by
fingerprint. Anything less silently wipes their triage, which is the same trust failure as a partial
sitemap emitting confident coverage findings.

Three rules this module exists to enforce:

1. **Never invent or drop a human value.** An unrecognised status ("wontfx") is preserved verbatim
   and merely treated as untriaged for sorting. Correcting it would invent intent; dropping it would
   destroy intent.
2. **Never let one finding inherit another's triage.** A row with a blank or corrupt fingerprint is
   skipped entirely rather than matched loosely.
3. **A reset must explain itself.** Fingerprints are scoped to check-version, so changing a rule
   re-keys a finding and the old judgement legitimately no longer applies. But a "wontfix" that
   vanishes with no explanation reads as the tool eating data, so the note says what happened.

Pure functions only — no network, no Sheets types. The transport lives in ``sheets.py``.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .report import Finding, Severity

# The statuses that mean something operationally. Anything else is kept verbatim and sorts as
# untriaged — see rule 1 above.
ACKNOWLEDGED = "acknowledged"
WONTFIX = "wontfix"
KNOWN_STATUSES = frozenset({"", ACKNOWLEDGED, WONTFIX})


@dataclass(frozen=True)
class TriageRow:
    fingerprint: str
    status: str
    note: str


@dataclass
class OpenRow:
    """A finding plus the client's own annotation, ready to write."""
    finding: Finding
    status: str = ""
    note: str = ""
    first_seen: str = ""
    age_days: int = 0


def parse_triage_columns(header: list[str], rows: list[list[str]]) -> dict[str, TriageRow]:
    """Read `status`/`note` out of a previously written tab, keyed by fingerprint.

    Tolerant on purpose: the client may reorder or delete columns, and Sheets omits trailing empty
    cells so a row can be shorter than the header. Neither may raise, and neither may cause a
    finding to inherit somebody else's triage.
    """
    def idx(name: str) -> int:
        try:
            return header.index(name)
        except ValueError:
            return -1

    i_fp, i_status, i_note = idx("fingerprint"), idx("status"), idx("note")
    if i_fp < 0:
        return {}   # without the join key we know nothing — say nothing

    def cell(row: list[str], i: int) -> str:
        return row[i].strip() if 0 <= i < len(row) and row[i] is not None else ""

    out: dict[str, TriageRow] = {}
    for row in rows:
        fp = cell(row, i_fp)
        if not fp or fp in out:
            # blank/corrupt fingerprint: skip, never match loosely (rule 2).
            # duplicate: first wins, deterministically, rather than last-write-wins.
            continue
        out[fp] = TriageRow(fp, cell(row, i_status), cell(row, i_note))
    return out


def merge_triage(findings: list[Finding], existing: dict[str, TriageRow],
                 changed_checks: set[str], today: str,
                 orphan_urls: dict[str, tuple[str, str]] | None = None) -> list[OpenRow]:
    """Re-attach client triage to this run's findings.

    ``changed_checks`` is the set of check names whose check-version moved since the last run.
    ``orphan_urls`` optionally maps a now-unmatched fingerprint to the (url, check) it came from, so
    a reset note is only attached to a finding on the SAME page and check — never borrowed from an
    unrelated orphan.
    """
    matched = {f.fingerprint for f in findings} & existing.keys()
    orphans = {fp: t for fp, t in existing.items() if fp not in matched}
    orphan_urls = orphan_urls or {}

    out: list[OpenRow] = []
    used_orphans: set[str] = set()
    for f in findings:
        prior = existing.get(f.fingerprint)
        if prior is not None:
            out.append(OpenRow(f, status=prior.status, note=prior.note))
            continue

        note = ""
        if f.check in changed_checks:
            # This finding has no prior triage AND its rule moved. If an orphaned judgement came
            # from the same page + check, say what happened to it rather than letting it vanish.
            for fp, t in orphans.items():
                if fp in used_orphans or not (t.status or t.note):
                    continue
                src_url, src_check = orphan_urls.get(fp, (f.url, f.check))
                if src_url != f.url or src_check != f.check:
                    continue
                used_orphans.add(fp)
                was = f"{t.status or 'untriaged'}" + (f" — {t.note}" if t.note else "")
                note = (f"triage reset: rule changed {today} (was {was}). "
                        f"The rule moved, so the earlier judgement was about a different question — "
                        f"please re-check.")
                break
        out.append(OpenRow(f, status="", note=note))
    return out


_SEVERITY_RANK = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}


def sort_key(status: str, severity: Severity, age_days: int):
    """Untriaged first, then by severity, then OLDEST first.

    A tab sorted the same way every day is a tab nobody opens. Ordering by age means the board
    visibly changes as work is not done, and the thing most overdue is always the top row.
    """
    triage_rank = {ACKNOWLEDGED: 1, WONTFIX: 2}.get(status, 0)  # unknown -> untriaged (rule 1)
    return (triage_rank, _SEVERITY_RANK.get(severity, 3), -age_days)


def untriaged_error_summary(rows: list[OpenRow]) -> tuple[int, int]:
    """(how many ERRORs nobody has looked at, age in days of the oldest).

    This is the line that goes in the digest email. The sheet should not have to create urgency —
    if nothing moves for six weeks, nobody opens a tab, but they do read a subject line.
    """
    open_errors = [r for r in rows
                   if r.finding.severity is Severity.ERROR
                   and r.status not in (ACKNOWLEDGED, WONTFIX)]
    if not open_errors:
        return 0, 0
    return len(open_errors), max(r.age_days for r in open_errors)
