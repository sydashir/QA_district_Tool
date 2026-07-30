"""Given a brand's union report dir, isolate findings that land on the REST-ONLY pages (live pages
missing from the sitemap — the set the sitemap-only audit never content-checked). The rest-only URL
set is read straight from the report's own enumeration:missing_from_sitemap findings (classes
indexable/noindex/cruft), so no re-crawl. Per-page checks (heading/meta/blank/placeholder/phone
mismatch+cross-brand) attribute cleanly to a page; site-wide collapsed findings (retired/unknown
phone, broken_links) are reported separately as 'not page-attributable'."""
import json, sys
from collections import Counter

REPORT = sys.argv[1]
rows = [json.loads(l) for l in open(f"{REPORT}/findings.jsonl")]

MISSING_LIVE = {"indexable_unsitemapped", "noindex_unsitemapped", "cruft_noindex", "cruft_indexable"}
rest_only = {r["url"] for r in rows
             if r.get("check") == "enumeration" and (r.get("details") or {}).get("class") in MISSING_LIVE}

PER_PAGE = {"heading_structure", "meta", "blank", "placeholder"}
def is_page_phone(r):  # per-page phone findings (mismatch/cross-brand/malformed), not collapsed site-wide
    return r.get("check") == "phone" and (r.get("details") or {}).get("class") not in {"retired", "unknown", "non_canonical"}

on_added = [r for r in rows
            if r["url"] in rest_only and (r.get("check") in PER_PAGE or is_page_phone(r))]

print(f"rest-only live pages (missing from sitemap): {len(rest_only)}")
print(f"per-page findings landing ON those added pages: {len(on_added)}")
by_check = Counter(r["check"] for r in on_added)
by_sev = Counter(r["severity"] for r in on_added)
print(f"  by check: {dict(by_check)}")
print(f"  by severity: {dict(by_sev)}")
# show the actionable (error/warning) issue mix
issues = Counter(r.get("issue","") for r in on_added if r["severity"] in ("error","warning"))
print("  actionable issues on added pages:")
for iss, n in issues.most_common(8):
    print(f"     {n:>4}  {iss}")
