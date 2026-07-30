"""Measure link-probe wall-time WITH the external-timeout fix, against real GL link targets.
Both GL and RR cap at max_links=400, so the 400-probe wall-time is bounded by the slowest probes
regardless of brand -> GL's fixed time estimates RR's. Loads real link_urls from the GL projection
cache (no re-crawl of pages), probes them once through the patched check_links, reports elapsed."""
import asyncio, json, time
from types import SimpleNamespace
from auditor import crawl as C
from auditor.checks import links
from auditor.config import load_brand

cfg = load_brand("gl")
cache = json.load(open("cache/gl/pages.json"))
# build light projections carrying link_urls (that's all check_links reads)
projs = [SimpleNamespace(url=u, link_urls=m.get("link_urls", []))
         for u, m in cache.items() if m.get("link_urls")]
print(f"GL pages with links: {len(projs)}  (external timeout = {cfg.crawl.external_link_timeout_seconds}s, "
      f"full = {cfg.crawl.timeout_seconds}s, concurrency = {cfg.crawl.max_concurrency})")

async def main():
    async with C.make_client(cfg.crawl) as client:
        t0 = time.time()
        findings, stats = await links.check_links(projs, client, cfg, max_links=400,
                                                  on_done=None)
        dt = time.time() - t0
    print(f"probed={stats['probed']} unique_targets={stats['unique_targets']} "
          f"broken={stats['broken']} unverified={stats['unverified']}")
    print(f"LINK-PROBE WALL TIME (400 cap, fixed): {dt:.1f}s  ({dt/60:.1f} min)")

asyncio.run(main())
