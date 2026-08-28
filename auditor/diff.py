"""In-stream run-diff.

first_seen/last_seen/status can't be a post-pass over the JSONL we just streamed — that
would rewrite the output and defeat streaming. So the diff runs IN the stream: load the
prior run's history (per-fingerprint snapshot + dates, plus the check-version components as
of that run), and as each finding streams by, annotate it (carry first_seen / set last_seen /
status new|persisting).

RESOLVED findings — prior fingerprints not seen this run — are only knowable at the end and
are emitted as a reconstructed tail. But a finding can vanish for reasons that are NOT a fix:
- the CHECK (or a global source) changed (threshold/logic edit) -> it was un-flagged by a moved
  ruler, not fixed -> status ``rule_changed`` (never claim a fix to a client that didn't happen).
- the PAGE isn't live anymore -> status ``page_removed`` (a bigger event than "bug fixed").
- the page LEFT OUR SCOPE (still live, dropped from the sitemap so we stopped auditing it) ->
  status ``page_unsitemapped`` — NOT a resolution; the finding is still out there, unseen.
- otherwise -> genuine ``resolved``.
Scope is judged by the AUDITED set (pages we actually evaluated) and liveness by the WP-REST
LIVE set — never by sitemap membership, which conflates "in the sitemap" with "live".
History is small (fingerprints + dates + a few fields), not pages.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .checks import (actions, blank, brands, duplication, empty_row, empty_slot,
                     enumeration, links, meta, misspelling, phone, placeholder, schema, scope,
                     spelling, structure)
from .checks_version import changed_components
from .report import Finding, Severity

# Global sources that shape ALL checks' output/fingerprints — a change here makes every
# resolved finding suspect (stale ruleset), not just one check's.
_GLOBAL_SRC = {"src:parse.py", "src:report.py"}

# check name (Finding.check) -> the check-version component(s) that drive it.
_CHECK_COMPONENT: dict[str, set[str]] = {
    m.CHECK: {f"src:{Path(m.__file__).name}"}
    for m in (actions, blank, brands, duplication, empty_row, empty_slot, enumeration,
              links, meta, misspelling, phone, placeholder, schema, scope, spelling, structure)
}
# enumeration output depends on crawl.py's enumerate logic (what's in the sitemap/REST sets) —
# enumeration-scoped, so a crawl enumerate change rule-changes ONLY the 845 findings.
_CHECK_COMPONENT["enumeration"] = _CHECK_COMPONENT["enumeration"] | {"src:crawl.py"}
# `redirects_off_brand` is an enumeration finding MINTED IN audit.py (_off_brand_projection), so an
# edit to the off-domain rule must rule-change it. Without this the findings vanish from the diff
# and read as `resolved` — "someone fixed the sitemap" — when nothing changed. Exactly the trap the
# collapse list below documents, in a new place.
_CHECK_COMPONENT["enumeration"] = _CHECK_COMPONENT["enumeration"] | {"src:audit.py"}
# broken_links imports CRUFT_RE from enumeration for the cruft_link class (B10), so an edit to
# that shared pattern must rule-change link findings too — links-scoped, not global.
_CHECK_COMPONENT["broken_links"] = _CHECK_COMPONENT["broken_links"] | {"src:enumeration.py"}
# The collapses in audit.py mint these checks' fingerprints, so an edit there rule-changes
# exactly those checks — never the ones it does not touch.
# empty_slot / misspelling / scope were added to the collapse LATER and this list was not updated
# with them. The consequence was client-visible and wrong: AH's 169 collapsed `county_for_country`
# rows vanished from the diff and were reported to the QA team as "171 fixed" when nothing had been
# fixed at all. GL (4,002 empty_slot) and MHD (1,056 misspelling) would have claimed thousands.
# Any check the collapses touch MUST be listed here, or its collapsed rows read as resolved.
for _c in ("phone", "brands", "duplication", "empty_row", "actions", "heading_structure",
           "empty_slot", "misspelling", "scope"):
    if _c in _CHECK_COMPONENT:
        _CHECK_COMPONENT[_c] = _CHECK_COMPONENT[_c] | {"src:audit.py"}
# phone output also depends on the canonical VALUES and on nap.py's parse (P1) — both
# phone-scoped (a nap.py edit rule-changes ONLY phone, never other checks).
_CHECK_COMPONENT["phone"] = _CHECK_COMPONENT["phone"] | {"canonical_phones", "src:nap.py",
                                                         "third_party", "brand_numbers"}
# meta output depends on the per-brand title bounds (P4) — config-scoped to meta, so a
# per-brand threshold tune rule-changes only that brand's meta findings.
_CHECK_COMPONENT["meta"] = _CHECK_COMPONENT["meta"] | {"title_bounds"}
# placeholder output depends on the ACF-token ruleset imported from the GeoData Fetcher's
# geo_field_validator (out-of-repo, path via $GEODATA_SERVICES_DIR) — placeholder-scoped, so
# updating/repointing it rule-changes ONLY the [acf field] findings, never another check's.
_CHECK_COMPONENT["placeholder"] = _CHECK_COMPONENT["placeholder"] | {
    "src:geo_field_validator.py", "geodata_gfv_path"}
# spelling output depends on the word lists and on the English dictionary itself — spelling-scoped,
# so adding an approved term rule-changes ONLY spelling findings, never another check's.
_CHECK_COMPONENT["spelling"] = _CHECK_COMPONENT["spelling"] | {
    "vocab:allowlist.json", "vocab:domain_vocab.json", "dict:pyspellchecker"}


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
    components; ``audited`` is the set of URLs actually evaluated this run (the honest "did we
    look?" signal); ``live`` is the WP-REST live-page set used to split gone vs unsitemapped
    (optional — absent it, we never claim removal)."""

    def __init__(self, prior: dict, now: str, components: dict, audited=None, live=None) -> None:
        prior = prior or {}
        self.prior_findings: dict = prior.get("findings", {})
        self.prior_components: dict = prior.get("components", {})
        self.now = now
        self.components = components
        self.audited = {u.rstrip("/") for u in (audited or ())}  # pages EVALUATED this run
        self.live = None if live is None else {u.rstrip("/") for u in live}  # WP-REST live set
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
        url = rec.get("url", "").rstrip("/")
        if url not in self.audited:
            # We did NOT evaluate this page this run -> its finding vanished because we stopped
            # LOOKING, not because it was fixed. "Audited" (evaluated) is the honest signal, not
            # sitemap membership: a page that goes noindex drops from the sitemap while still
            # serving its broken link (the ~845 live-but-unsitemapped GL pages). So confirm
            # removal against the WP-REST LIVE set, never against the sitemap.
            if self.live is not None and url not in self.live:
                return "page_removed"       # confirmed gone: not in the live site
            return "page_unsitemapped"      # still live (or liveness unknown) — left our scope
        if (changed & _GLOBAL_SRC) or (_CHECK_COMPONENT.get(rec.get("check"), set()) & changed):
            return "rule_changed"           # re-evaluated, but a moved ruler un-flagged it
        return "resolved"                   # evaluated, ruler unchanged -> a genuine fix

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
        (resolved drop out; if one reappears later it's 'new' again). Written ATOMICALLY
        (temp + os.replace) so a mid-write crash can never leave a truncated history that a
        later run would silently diff against — a poisoned baseline is worse than none."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(
            json.dumps({"components": self.components, "findings": self.history},
                       indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, p)  # atomic on POSIX
