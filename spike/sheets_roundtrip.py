"""Live round-trip proof for the sheet output, against the real Sheets API.

Uses a scratch tab in an explicitly-named test sheet and DELETES it at the end, leaving the
spreadsheet exactly as found. Proves the four things the unit tests can only assert against a fake:

  1. first run — tab absent, gets created and written
  2. the client edits `status`/`note` by hand
  3. next run rewrites the tab and their edits SURVIVE
  4. a failed read REFUSES to write, leaving the tab untouched

Usage: python3 -m spike.sheets_roundtrip <spreadsheet_id>
"""
from __future__ import annotations

import sys

from auditor.report import Finding, Severity
from auditor.sheets import OPEN_HEADER, SheetsClient, TriageReadFailed, publish_open_tab

CRED = "/Users/ashir/Documents/workk/district/credentials/service-account.json"
TAB = "ZZ_auditor_roundtrip_probe"


def _f(fp, sev=Severity.ERROR, check="phone", url="https://www.gratitudelodge.com/a/"):
    return Finding(url=url, check=check, severity=sev, fingerprint=fp,
                   issue="retired number printed on the page", location="footer",
                   snippet="800-692-9850", suggestion="replace with 844-576-0144", details={})


def main(sheet_id: str) -> None:
    c = SheetsClient(spreadsheet_id=sheet_id, credentials_path=CRED)
    findings = [_f("fp-a"), _f("fp-b", sev=Severity.WARNING), _f("fp-c")]
    first_seen = {"fp-a": "2026-06-01", "fp-b": "2026-07-20", "fp-c": "2026-08-01"}
    try:
        print("1. FIRST RUN (tab does not exist yet)")
        c.ensure_tab(TAB)
        rows = publish_open_tab(c, sheet_id, TAB, findings, changed_checks=set(),
                                today="2026-08-04", first_seen=first_seen, run_date="2026-08-04")
        print(f"   wrote {len(rows)} rows; order = {[r.finding.fingerprint for r in rows]}")
        print(f"   ages  = {[r.age_days for r in rows]}  (oldest untriaged ERROR must lead)")

        print("2. CLIENT EDITS THE TAB BY HAND (status + note on fp-a, a typo'd status on fp-c)")
        hdr, body = c.read_tab(TAB)
        i_fp, i_st, i_no = hdr.index("fingerprint"), hdr.index("status"), hdr.index("note")
        for r in body:
            while len(r) < len(hdr):
                r.append("")
            if r[i_fp] == "fp-a":
                r[i_st], r[i_no] = "wontfix", "PPC page, intentional"
            if r[i_fp] == "fp-c":
                r[i_st] = "wontfx"                      # human typo — must be preserved verbatim
        c.replace_tab(TAB, hdr, body)
        print("   set fp-a=wontfix('PPC page, intentional'), fp-c=wontfx (typo)")

        print("3. SECOND RUN — their edits must survive the rewrite")
        rows = publish_open_tab(c, sheet_id, TAB, findings, changed_checks=set(),
                                today="2026-08-04", first_seen=first_seen, run_date="2026-08-04")
        got = {r.finding.fingerprint: (r.status, r.note) for r in rows}
        print(f"   fp-a -> {got['fp-a']}")
        print(f"   fp-c -> {got['fp-c']}  (typo preserved verbatim, sorts as untriaged)")
        assert got["fp-a"] == ("wontfix", "PPC page, intentional"), got["fp-a"]
        assert got["fp-c"][0] == "wontfx", got["fp-c"]
        print(f"   order now = {[r.finding.fingerprint for r in rows]}  (wontfix sorts last)")

        print("4. RULE CHANGE — triage resets, and the note SAYS why")
        rekeyed = [_f("fp-a-v2"), _f("fp-b", sev=Severity.WARNING), _f("fp-c")]
        rows = publish_open_tab(c, sheet_id, TAB, rekeyed, changed_checks={"phone"},
                                today="2026-08-04", first_seen=first_seen, run_date="2026-08-04")
        note = next(r.note for r in rows if r.finding.fingerprint == "fp-a-v2")
        print(f"   fp-a-v2 note = {note[:110]}...")
        assert "triage reset" in note and "wontfix" in note

        print("5. FAILED READ — must refuse to write, tab left alone")
        before = c.read_tab(TAB)
        broken = SheetsClient(spreadsheet_id="does-not-exist-xxxx", credentials_path=CRED)
        try:
            publish_open_tab(broken, "x", TAB, findings, changed_checks=set(),
                             today="2026-08-04", first_seen=first_seen, run_date="2026-08-04")
            print("   FAIL: it wrote anyway")
        except TriageReadFailed as e:
            print(f"   refused, as required: {str(e)[:100]}...")
        after = c.read_tab(TAB)
        assert before == after, "the tab changed after a refused write"
        print("   tab byte-identical after the refusal")
    finally:
        c.delete_tab(TAB)
        print(f"\nCLEANUP: deleted {TAB!r} — spreadsheet left exactly as found")


if __name__ == "__main__":
    main(sys.argv[1])
