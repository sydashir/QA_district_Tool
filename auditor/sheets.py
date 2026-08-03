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
        self._send("POST",
                   f"{SHEETS_API}/{self.spreadsheet_id}/values/{tab}!A1:Z100000:clear"
                   ).raise_for_status()
        self._send("PUT", f"{SHEETS_API}/{self.spreadsheet_id}/values/{tab}!A1",
                   params={"valueInputOption": "RAW"},
                   json={"values": [header] + rows}).raise_for_status()

    def ensure_tab(self, tab: str) -> None:
        """Create the tab if it is absent. Idempotent."""
        meta = self._send("GET", f"{SHEETS_API}/{self.spreadsheet_id}?fields=sheets.properties.title")
        meta.raise_for_status()
        titles = {s_["properties"]["title"] for s_ in meta.json().get("sheets", [])}
        if tab in titles:
            return
        self._send("POST", f"{SHEETS_API}/{self.spreadsheet_id}:batchUpdate",
                   json={"requests": [{"addSheet": {"properties": {"title": tab}}}]}
                   ).raise_for_status()

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
