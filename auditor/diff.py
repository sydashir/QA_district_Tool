"""In-stream run-diff.

first_seen/last_seen/status can't be a post-pass over the JSONL we just streamed — that
would rewrite the output and defeat streaming. So the diff runs IN the stream: load the
prior run's history (per-fingerprint snapshot + dates, plus the check-version components as
of that run), and as each finding streams by, annotate it (carry first_seen / set last_seen /
status new|persisting).

RESOLVED findings — prior fingerprints not seen this run — are only knowable at the end and
are emitted as a reconstructed tail. But a finding can vanish for reasons that are NOT a fix:
- the CHECK changed (threshold/logic edit) -> it was un-flagged by a moved ruler, not fixed
  -> status ``stale_ruleset`` (never claim a fix to a client that didn't happen).
- the PAGE was removed -> status ``page_removed`` (a bigger event than "bug fixed", not a win).
- otherwise -> genuine ``resolved``.
History is small (fingerprints + dates + a few fields), not pages.
"""
from __future__ import annotations

import json
from pathlib import Path

from .checks import blank, links, meta, phone, placeholder, structure
from .checks_version import changed_components
from .report import Finding, Severity

# Global sources that shape ALL checks' output/fingerprints — a change here makes every
# resolved finding suspect (stale ruleset), not just one check's.
_GLOBAL_SRC = {"src:parse.py", "src:report.py"}

# check name (Finding.check) -> the check-version component(s) that drive it.
_CHECK_COMPONENT: dict[str, set[str]] = {
    m.CHECK: {f"src:{Path(m.__file__).name}"}
    for m in (blank, links, meta, phone, placeholder, structure)
}
_CHECK_COMPONENT["phone"] = _CHECK_COMPONENT["phone"] | {"canonical_phones"}


def load_history(path) -> dict:
    p = Path(path)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return {}
    return {}


class RunDiff:
    """Annotate findings in-stream and classify resolved. ``now`` is injected (no wall-clock
    in the logic) so runs are reproducible. ``components`` is this run's check-version
    components; ``enumerated`` is the set of live page URLs this run (limit-independent)."""

    def __init__(self, prior: dict, now: str, components: dict, enumerated=None) -> None:
        prior = prior or {}
        self.prior_findings: dict = prior.get("findings", {})
        self.prior_components: dict = prior.get("components", {})
        self.now = now
        self.components = components
        self.enumerated = {u.rstrip("/") for u in (enumerated or ())}
        self.seen: set[str] = set()
        self.history: dict[str, dict] = {}

    def annotate(self, f: Finding) -> Finding:
        self.seen.add(f.fingerprint)
        prev = self.prior_findings.get(f.fingerprint)
        f.first_seen = prev["first_seen"] if prev else self.now
        f.last_seen = self.now
        f.status = "persisting" if prev else "new"
        self.history[f.fingerprint] = {
            "first_seen": f.first_seen, "last_seen": f.last_seen,
            "url": f.url, "check": f.check, "issue": f.issue,
            "severity": f.severity.value, "location": f.location,
        }
        return f

    def _resolved_status(self, rec: dict, changed: set[str]) -> str:
        if changed & _GLOBAL_SRC:
            return "stale_ruleset"
        if _CHECK_COMPONENT.get(rec.get("check"), set()) & changed:
            return "stale_ruleset"
        if rec.get("url", "").rstrip("/") not in self.enumerated:
            return "page_removed"
        return "resolved"

    def resolved_findings(self) -> list[Finding]:
        changed = set(changed_components(self.prior_components, self.components))
        out: list[Finding] = []
        for fp, rec in self.prior_findings.items():
            if fp in self.seen:
                continue
            out.append(Finding(
                url=rec.get("url", ""), check=rec.get("check", "?"), fingerprint=fp,
                severity=Severity(rec.get("severity", "info")),
                issue=rec.get("issue", "(resolved)"), location=rec.get("location"),
                first_seen=rec.get("first_seen"), last_seen=rec.get("last_seen"),
                status=self._resolved_status(rec, changed)))
        return out

    def persist(self, path) -> None:
        """History = this run's check-version components + only fingerprints seen this run
        (resolved drop out; if one reappears later it's 'new' again)."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps({"components": self.components, "findings": self.history},
                       indent=2, sort_keys=True), encoding="utf-8")
