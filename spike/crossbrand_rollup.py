"""Re-derive the cross-brand dial rollup from each brand's LATEST report.

Cross-brand dial = a page displays one number but its click-to-call `tel:` dials a number that
belongs to a DIFFERENT District brand (phone.py's dial-split). That's revenue misrouting: the
caller reaches another facility's intake line. Also reports display_dial_unknown (dials a number
in nobody's NAP) because a number recurring across several brands' facility pages is more likely a
real shared line missing from the canon than N independent bugs.

Reads reports/<brand>/<latest>/findings.jsonl only — no crawling, no writes.
Usage: python3 -m spike.crossbrand_rollup
"""
from __future__ import annotations

import glob
import json
import os
from collections import defaultdict

from auditor.nap import brand_number_owners

BRANDS = ["gl", "rr", "coc", "dbh", "ah", "ar", "tdrc", "cad", "mhd"]
CROSS = "cross_brand_dial"
UNKNOWN = {"display_dial_unknown", "display_dial_ambiguous"}


def latest_report(brand: str) -> str | None:
    dirs = sorted(d for d in glob.glob(f"reports/{brand}/2026*") if os.path.isdir(d))
    for d in reversed(dirs):
        if os.path.exists(f"{d}/findings.jsonl"):
            return d
    return None


def fmt(e164: str | None) -> str:
    if not e164 or not e164.startswith("+1") or len(e164) != 12:
        return str(e164)
    return f"{e164[2:5]}-{e164[5:8]}-{e164[8:]}"


def main() -> None:
    owners = brand_number_owners()
    cross: list[dict] = []
    unknown: dict[str, list[tuple[str, str]]] = defaultdict(list)

    print("brand  report")
    for b in BRANDS:
        d = latest_report(b)
        print(f"  {b.upper():5} {d or '(none)'}")
        if not d:
            continue
        for line in open(f"{d}/findings.jsonl", encoding="utf-8"):
            r = json.loads(line)
            if r.get("check") != "phone":
                continue
            det = r.get("details") or {}
            cls = det.get("class")
            if cls == CROSS:
                cross.append({
                    "brand": b.upper(), "page": r["url"], "shown": det.get("displayed"),
                    "dials": det.get("tel"), "owner": det.get("owner"),
                    "sev": r.get("severity"), "status": r.get("status"),
                })
            elif cls in UNKNOWN:
                unknown[det.get("tel") or "?"].append((b.upper(), r["url"]))

    print(f"\n{'='*100}\nCROSS-BRAND DIAL (ERROR) — a page dials ANOTHER brand's number\n{'='*100}")
    print(f"{'brand':6} {'shows':14} {'dials':14} {'owner':6} page")
    for c in sorted(cross, key=lambda x: (x["owner"] or "", x["brand"])):
        print(f"{c['brand']:6} {fmt(c['shown']):14} {fmt(c['dials']):14} {c['owner'] or '?':6} "
              f"{c['page']}")
    by_pair: dict[tuple[str, str], int] = defaultdict(int)
    for c in cross:
        by_pair[(c["brand"], c["owner"] or "?")] += 1
    print(f"\nTOTAL cross-brand pages: {len(cross)}")
    print("by direction: " + ", ".join(
        f"{a}->{b} x{n}" for (a, b), n in sorted(by_pair.items(), key=lambda kv: -kv[1])))
    leaked_to: dict[str, int] = defaultdict(int)
    for c in cross:
        leaked_to[c["owner"] or "?"] += 1
    print("calls leaking TO: " + ", ".join(f"{k} x{v}" for k, v in sorted(
        leaked_to.items(), key=lambda kv: -kv[1])))

    print(f"\n{'='*100}\nUNKNOWN DIAL (WARNING) — dials a number in NO brand's NAP canon\n{'='*100}")
    for num, hits in sorted(unknown.items(), key=lambda kv: -len(kv[1])):
        brands = sorted({b for b, _ in hits})
        tag = ("  <-- SAME number across %d brands: likely a REAL shared line MISSING from the NAP "
               "canon, not %d separate bugs" % (len(brands), len(hits))) if len(brands) > 1 else ""
        print(f"{fmt(num):14} x{len(hits):<3} brands={','.join(brands)}{tag}")
        for b, u in hits[:3]:
            print(f"                   {b}  {u}")
    print(f"\nnumbers in canon (sanity): {len(owners)} live canonical numbers mapped to owners")


if __name__ == "__main__":
    main()
