"""In-stream run-diff.

first_seen/last_seen/status can't be a post-pass over the JSONL we just streamed — that
would rewrite the output and defeat streaming. So the diff runs IN the stream: load the
prior run's history (fingerprint -> compact snapshot + dates), and as each finding streams by,
annotate it (carry first_seen / set last_seen / status new|persisting). RESOLVED findings —
prior fingerprints not seen this run — can only be known at the end, so they're emitted as a
tail, reconstructed from the snapshot (a fixed 404 is worth reporting). History is small
(fingerprints + dates + a few fields), not pages, so loading it at run start is cheap.
"""
from __future__ import annotations

import json
from pathlib import Path

from .report import Finding, Severity


def load_history(path) -> dict:
    p = Path(path)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return {}
    return {}


class RunDiff:
    """Annotate findings in-stream and track resolved. ``now`` is passed in (no wall-clock
    in the logic) so runs are reproducible/testable."""

    def __init__(self, prior: dict, now: str) -> None:
        self.prior = prior
        self.now = now
        self.seen: set[str] = set()
        self.history: dict[str, dict] = {}

    def annotate(self, f: Finding) -> Finding:
        self.seen.add(f.fingerprint)
        prev = self.prior.get(f.fingerprint)
        f.first_seen = prev["first_seen"] if prev else self.now
        f.last_seen = self.now
        f.status = "persisting" if prev else "new"
        self.history[f.fingerprint] = {
            "first_seen": f.first_seen, "last_seen": f.last_seen,
            "url": f.url, "check": f.check, "issue": f.issue,
            "severity": f.severity.value, "location": f.location,
        }
        return f

    def resolved_findings(self) -> list[Finding]:
        """Prior fingerprints not seen this run — reconstructed from their snapshot."""
        out: list[Finding] = []
        for fp, rec in self.prior.items():
            if fp in self.seen:
                continue
            out.append(Finding(
                url=rec.get("url", ""), check=rec.get("check", "?"), fingerprint=fp,
                severity=Severity(rec.get("severity", "info")),
                issue=rec.get("issue", "(resolved)"), location=rec.get("location"),
                first_seen=rec.get("first_seen"), last_seen=rec.get("last_seen"),
                status="resolved"))
        return out

    def persist(self, path) -> None:
        """Write history = only fingerprints seen THIS run (resolved drop out; if one
        reappears later it's 'new' again)."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.history, indent=2, sort_keys=True), encoding="utf-8")
