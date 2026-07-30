"""Re-probe a brand's link graph with the STRATIFIED window, without re-crawling its pages.

GL/RR/COC/CAD were audited while the probe window was diluted (a flat/alphabetical pick spent the
400-probe budget on whichever URL cohort was largest), so their link signal is understated — AR went
from 0 to 7 ERROR link findings once the window was stratified. This reads each brand's cached
link graph (cache/<brand>/pages.json holds link_urls per page) and runs ONLY the link check, so it
costs 400 probes per brand instead of a full re-crawl.

Usage: python3 -m spike.reprobe_links gl rr coc cad
"""
from __future__ import annotations

import asyncio
import json
import sys
from types import SimpleNamespace

from auditor import crawl as C
from auditor.checks import links
from auditor.config import load_brand


def projections_from_cache(brand: str):
    path = C.cache_path(brand)
    if not path.exists():
        return []
    cache = json.loads(path.read_text())
    return [SimpleNamespace(url=u, link_urls=m.get("link_urls") or [])
            for u, m in cache.items() if m.get("link_urls")]


async def one(brand: str) -> None:
    cfg = load_brand(brand)
    projs = projections_from_cache(brand)
    if not projs:
        print(f"{brand.upper():5} no cached link graph — skip")
        return
    async with C.make_client(cfg.crawl) as client:
        findings, stats = await links.check_links(projs, client, cfg, max_links=400)
    actionable = [f for f in findings if f.severity.value in ("error", "warning")]
    print(f"{brand.upper():5} pages={len(projs)} unique_targets={stats['unique_targets']} "
          f"probed={stats['probed']} broken={stats['broken']} unverified={stats['unverified']}")
    print(f"      ACTIONABLE link findings: {len(actionable)}")
    for f in actionable:
        d = f.details or {}
        src = (d.get("sources") or ["?"])[0]
        print(f"        [{f.severity.value}] {f.issue[:44]:44} {(d.get('target') or '')[:58]}")
        print(f"              on {len(d.get('sources') or [])} page(s), e.g. {src[:66]}")


async def main(brands: list[str]) -> None:
    for b in brands:
        try:
            await one(b)
        except Exception as e:
            print(f"{b.upper():5} ERROR {type(e).__name__}: {e}")
        await asyncio.sleep(3)  # settle between hosts


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:] or ["gl", "rr", "coc", "cad"]))
