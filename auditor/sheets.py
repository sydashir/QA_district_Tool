"""Google Sheets output for the scheduled auditor.

Raw Sheets API v4 over ``httpx`` + ``google-auth``. Deliberately not ``gspread``: it is not
installed, it is sync-only and not thread-safe, and this needs three REST calls.

**The rule this module exists to enforce:** the `<BRAND> — open` tab carries the client's own
`status`/`note` columns, and every run rewrites that tab. So the run reads the existing triage
first and re-attaches it. If that read FAILS, the write is refused — losing a night of updates is
recoverable, silently wiping a month of somebody's triage is not. Same discipline as withholding
coverage findings on a partial sitemap read.

The service account CANNOT create a spreadsheet (verified 2026-08-03: `storageQuota.limit` = 0 and
no Shared Drives, so it can never own a file). A human creates the sheet and shares it as Editor
with ``app-service-account@lexical-sol-454719-s2.iam.gserviceaccount.com``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .report import Severity
from .triage import OpenRow, merge_triage, parse_triage_columns, sort_key

_log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"

# Official Sheets API quota (developers.google.com/workspace/sheets/api/limits, checked 2026-08-04):
# 60 read AND 60 write requests per minute PER USER per project (300 per project). No daily cap.
# We pace well under 60 because this service account is SHARED with the GeoData Fetcher — if that
# runs at the same time it draws from the same per-user bucket, and a nightly job that starts
# backing off on brand seven runs into the morning.
QUOTA_PER_MIN = 60
PACE_PER_MIN = 40          # ~1.5s between calls, a third of the quota left as headroom

OPEN_HEADER = ["url", "check", "severity", "issue", "location", "snippet", "suggestion",
               "fingerprint", "first_seen", "age_days", "status", "note"]


class TriageReadFailed(RuntimeError):
    """Raised when existing triage could not be read, so the tab must be left alone."""


@dataclass
class SheetsClient:
    """Thin Sheets v4 wrapper. ``read_tab`` returns None when the tab does not exist yet."""
    spreadsheet_id: str
    credentials_path: str
    _creds: object = None
    _last_call: float = 0.0

    def _headers(self) -> dict:
        """Cached credentials. The first version re-read the key file and minted a NEW token on
        EVERY call, which is what pushed a live round-trip into HTTP 429 — the token is valid for
        an hour, so a nightly run needs one, not hundreds."""
        from google.oauth2 import service_account
        import google.auth.transport.requests as gtr
        if self._creds is None:
            self._creds = service_account.Credentials.from_service_account_file(
                self.credentials_path, scopes=SCOPES)
        if not self._creds.valid:
            self._creds.refresh(gtr.Request())
        return {"Authorization": f"Bearer {self._creds.token}"}

    def _pace(self) -> None:
        """Space calls out so a nine-brand run never approaches the per-minute quota."""
        import time
        min_gap = 60.0 / PACE_PER_MIN
        now = time.monotonic()
        wait = self._last_call + min_gap - now
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def _send(self, method: str, url: str, **kw):
        """One request with backoff on 429/5xx.

        Sheets enforces a per-minute write quota. A nightly run pushing nine brands will hit it, and
        a run that dies on a transient 429 after writing four brands leaves the sheet half-updated —
        so retry rather than fail, and only give up when it is genuinely not transient.
        """
        import time
        import httpx
        delay = 2.0
        last = None
        for attempt in range(5):
            self._pace()
            with httpx.Client(timeout=120) as c:
                r = c.request(method, url, headers=self._headers(), **kw)
            if r.status_code < 400:
                return r
            last = r
            if r.status_code == 429 or 500 <= r.status_code < 600:
                _log.warning("sheets %s %s -> %s, retrying in %.0fs (attempt %d/5)",
                             method, url.rsplit("/", 1)[-1][:40], r.status_code, delay, attempt + 1)
                time.sleep(delay)
                delay *= 2
                continue
            return r                      # a real 4xx (404/400) is the caller's to interpret
        return last

    def read_tab(self, tab: str):
        """(header, rows) — or None if the tab does not exist. Raises on any transport failure."""
        rng = f"{tab}!A1:Z100000"
        r = self._send("GET", f"{SHEETS_API}/{self.spreadsheet_id}/values/{rng}")
        if r.status_code == 400 and "Unable to parse range" in r.text:
            return None                       # tab absent — a first run, not a failure
        r.raise_for_status()
        values = r.json().get("values", [])
        if not values:
            return None
        return values[0], values[1:]

    def replace_tab(self, tab: str, header: list[str], rows: list[list[str]]) -> None:
        # WRITE FIRST, THEN TRIM. The obvious order (clear, then write) leaves the tab EMPTY if the
        # process dies between the two calls — the client opens the sheet to nothing. Writing first
        # means the worst case is correct new data followed by a few stale trailing rows, which the
        # next run cleans up.
        values = [header] + rows
        self._send("PUT", f"{SHEETS_API}/{self.spreadsheet_id}/values/{tab}!A1",
                   params={"valueInputOption": "RAW"},
                   json={"values": values}).raise_for_status()
        # TRIM ONLY WHAT EXISTS. The write auto-expands the grid to fit, so asking to clear from
        # row N+1 when the grid is exactly N rows tall starts the range PAST the last row and Sheets
        # answers 400. AR hit this live at 1,184 rows. Ask the grid how tall it is and skip the trim
        # when there is nothing below the new content.
        rows_in_grid = self._row_count(tab)
        if rows_in_grid is None or rows_in_grid > len(values):
            end = rows_in_grid or 100000
            self._send("POST",
                       f"{SHEETS_API}/{self.spreadsheet_id}/values/"
                       f"{tab}!A{len(values) + 1}:Z{end}:clear").raise_for_status()

    def _row_count(self, tab: str) -> int | None:
        """How many rows the tab's grid actually has, or None if it cannot be determined."""
        r = self._send("GET", f"{SHEETS_API}/{self.spreadsheet_id}"
                              "?fields=sheets(properties(title,gridProperties(rowCount)))")
        if r.status_code >= 400:
            return None
        for s_ in r.json().get("sheets", []):
            props = s_.get("properties", {})
            if props.get("title") == tab:
                return props.get("gridProperties", {}).get("rowCount")
        return None

    def update_row(self, tab: str, row_index: int, row: list[str]) -> None:
        """Overwrite one data row (0-based, excluding the header)."""
        self._send("PUT", f"{SHEETS_API}/{self.spreadsheet_id}/values/{tab}!A{row_index + 2}",
                   params={"valueInputOption": "RAW"},
                   json={"values": [row]}).raise_for_status()

    def ensure_tab(self, tab: str, header: list[str] | None = None) -> None:
        """Create the tab if absent, and seed its header row if it has none.

        The header is not cosmetic. Without it the first appended row lands in A1 and is read back
        AS the header, so an append-only tab can never find its own (run_id, brand) row — and every
        re-run appends a duplicate instead of updating. Found on the first live publish.
        """
        meta = self._send("GET", f"{SHEETS_API}/{self.spreadsheet_id}?fields=sheets.properties.title")
        meta.raise_for_status()
        titles = {s_["properties"]["title"] for s_ in meta.json().get("sheets", [])}
        if tab not in titles:
            self._send("POST", f"{SHEETS_API}/{self.spreadsheet_id}:batchUpdate",
                       json={"requests": [{"addSheet": {"properties": {"title": tab}}}]}
                       ).raise_for_status()
        if header and not self.read_tab(tab):
            self._send("PUT", f"{SHEETS_API}/{self.spreadsheet_id}/values/{tab}!A1",
                       params={"valueInputOption": "RAW"},
                       json={"values": [header]}).raise_for_status()

    def delete_tab(self, tab: str) -> None:
        """Remove a tab. Used to clean up after a live round-trip proof."""
        meta = self._send("GET", f"{SHEETS_API}/{self.spreadsheet_id}?fields=sheets.properties")
        meta.raise_for_status()
        for s_ in meta.json().get("sheets", []):
            if s_["properties"]["title"] == tab:
                self._send("POST", f"{SHEETS_API}/{self.spreadsheet_id}:batchUpdate",
                           json={"requests": [{"deleteSheet":
                                              {"sheetId": s_["properties"]["sheetId"]}}]}
                           ).raise_for_status()
                return

    def append_row(self, tab: str, row: list[str]) -> None:
        self._send("POST", f"{SHEETS_API}/{self.spreadsheet_id}/values/{tab}!A1:append",
                   params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"},
                   json={"values": [row]}).raise_for_status()


def _age_days(first_seen: str, run_date: str) -> int:
    from datetime import date
    try:
        a = date.fromisoformat(first_seen[:10])
        b = date.fromisoformat(run_date[:10])
        return max(0, (b - a).days)
    except Exception:
        return 0


def publish_open_tab(client, spreadsheet_id: str, tab: str, findings, changed_checks: set[str],
                     today: str, first_seen: dict[str, str], run_date: str) -> list[OpenRow]:
    """Read existing triage, re-attach it, and rewrite the tab. Refuses to write on a failed read."""
    try:
        existing_tab = client.read_tab(tab)
    except Exception as e:
        # DO NOT fall through to a write. A blank rewrite here destroys the client's triage.
        raise TriageReadFailed(
            f"could not read existing triage from {tab!r} ({type(e).__name__}: {e}); "
            f"refusing to rewrite the tab so the client's status/note columns are not lost") from e

    if existing_tab is None:
        existing = {}                          # first run — nothing to preserve
    else:
        header, rows = existing_tab
        existing = parse_triage_columns(header, rows)

    merged = merge_triage(findings, existing, changed_checks, today)
    for row in merged:
        row.first_seen = first_seen.get(row.finding.fingerprint, run_date)
        row.age_days = _age_days(row.first_seen, run_date)
    merged.sort(key=lambda r: sort_key(r.status, r.finding.severity, r.age_days))

    out_rows = [[
        r.finding.url, r.finding.check,
        r.finding.severity.name if isinstance(r.finding.severity, Severity) else str(r.finding.severity),
        r.finding.issue, r.finding.location, (r.finding.snippet or "")[:500],
        (r.finding.suggestion or "")[:500], r.finding.fingerprint,
        r.first_seen, str(r.age_days), r.status, r.note,
    ] for r in merged]
    client.replace_tab(tab, OPEN_HEADER, out_rows)
    return merged


SUMMARY_HEADER = ["run_started", "run_finished", "brand", "status", "pages_audited",
                  "errors", "warnings", "info", "new", "resolved", "rule_changed",
                  "untriaged_errors", "oldest_untriaged_days", "css_status", "sitemap_partial",
                  "duration_s", "detail"]


def summary_row(*, brand: str, status: str, started: str, finished: str = "", pages: int = 0,
                counts: dict | None = None, delta: dict | None = None,
                untriaged: tuple[int, int] = (0, 0), css_status: str = "", sitemap_partial: bool = False,
                duration_s: int = 0, detail: str = "") -> list[str]:
    """One Summary row. Written with status='running' at the START of a brand and rewritten at the
    end, so a run that dies leaves a visible 'running' row rather than silence."""
    c, d = counts or {}, delta or {}
    return [started, finished, brand, status, str(pages),
            str(c.get("ERROR", 0)), str(c.get("WARNING", 0)), str(c.get("INFO", 0)),
            str(d.get("new", 0)), str(d.get("resolved", 0)), str(d.get("rule_changed", 0)),
            str(untriaged[0]), str(untriaged[1]), css_status, str(bool(sitemap_partial)),
            str(duration_s), detail]


def digest_line(brand: str, delta: dict, untriaged: tuple[int, int]) -> str:
    """The line that makes NON-MOVEMENT visible.

    A sheet cannot create urgency — if nothing is fixed for six weeks the tab looks the same and
    nobody opens it. A subject/body line that says how much is untriaged and how old the oldest is
    reaches someone who never opens the sheet at all.
    """
    n, oldest = untriaged
    stale = f"{n} ERROR{'s' if n != 1 else ''} untriaged, oldest is {oldest} days" if n else \
        "nothing untriaged"
    return (f"{brand}: {delta.get('new', 0)} new, {delta.get('resolved', 0)} fixed, "
            f"{delta.get('open', 0)} open — {stale}")


class DryRunSheets:
    """A SheetsClient that touches nothing and renders exactly what WOULD be written.

    This is what makes the first real deploy safe, and what can be shown to the client before
    anything is live. It implements the same surface as ``SheetsClient``; reads come from whatever
    the sheet currently holds ONLY if a real client is supplied, otherwise from nothing — so a dry
    run against a live sheet still shows the true triage merge without writing it back.
    """

    def __init__(self, out, reader=None):
        self.out = out                 # a writable file object
        self.reader = reader           # optional real client, used for reads only
        self.writes: list[tuple] = []

    def read_tab(self, tab):
        return self.reader.read_tab(tab) if self.reader else None

    def ensure_tab(self, tab, header=None):
        self._w(f"\n=== would ENSURE tab exists: {tab!r}"
                + (f" (with header row)" if header else ""))

    def replace_tab(self, tab, header, rows):
        self.writes.append(("replace", tab, len(rows)))
        self._w(f"\n=== would REPLACE {tab!r} — {len(rows)} rows")
        self._w("    " + " | ".join(header))
        for r in rows[:15]:
            self._w("    " + " | ".join((c or "")[:28] for c in r))
        if len(rows) > 15:
            self._w(f"    … {len(rows) - 15} more rows")

    def append_row(self, tab, row):
        self.writes.append(("append", tab, 1))
        self._w(f"\n=== would APPEND to {tab!r}:\n    " + " | ".join(str(c)[:28] for c in row))

    def update_row(self, tab, row_index, row):
        self.writes.append(("update", tab, row_index))
        self._w(f"\n=== would UPDATE {tab!r} row {row_index}:\n    "
                + " | ".join(str(c)[:28] for c in row))

    def _w(self, line: str) -> None:
        print(line, file=self.out)
