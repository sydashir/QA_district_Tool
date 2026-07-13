#!/usr/bin/env python3
"""
THROWAWAY validation spike (session 1) — NOT the M0-M3 build.

Single purpose: de-risk the biggest unknown in the plan — crawl access to the
live Cloudflare-fronted WordPress sites — and prove two deterministic checks
fire against real pages.

Target: Gratitude Lodge (https://www.gratitudelodge.com).
Steps: locate sitemap -> enumerate URLs -> fetch ~50 real pages (polite) ->
run broken-link + heading-structure checks -> write reports/gl_spike_report.json.

Stack matches the plan (httpx + asyncio + BeautifulSoup/lxml). If Cloudflare
blocks us, the script reports it loudly and stops — that IS the finding.
"""
import asyncio
import json
import re
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

BASE = "https://www.gratitudelodge.com"
SITEMAP_CANDIDATES = ["/sitemap_index.xml", "/sitemap.xml", "/wp-sitemap.xml"]
N_PAGES = 50
MAX_LINK_CHECKS = 250          # bound the broken-link probe; report if we hit it
CONCURRENCY = 5                # polite
PER_REQUEST_DELAY = 0.25       # seconds, added jitter-free floor
TIMEOUT = 20.0
LONG_HEADING_WORDS = 20        # tuning knob: heading wrapping body-length text
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 DistrictSiteAuditor-Spike/0.1")

LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.IGNORECASE)
ACF_TOKEN_RE = re.compile(r"\[acf[\s ]+field[\s ]*=", re.IGNORECASE)
CURLY_RE = re.compile(r"\{\{[^}]+\}\}")
# empty-slot artifacts from unpopulated template vars (the CAD-homepage case)
DANGLING_RE = re.compile(r"(\bIn\s*,|\bby\s*%|\s,\s|\bof\s+in\b|\s{3,})")


def log(m): print(m, flush=True)


async def fetch(client, url, method="GET"):
    """Return (status, final_url, text, error). Retries once on transient error."""
    for attempt in (1, 2):
        try:
            r = await client.request(method, url)
            text = r.text if method == "GET" else ""
            return r.status_code, str(r.url), text, None
        except (httpx.TimeoutException, httpx.TransportError) as e:
            if attempt == 2:
                return None, url, "", f"{type(e).__name__}: {e}"
            await asyncio.sleep(0.5)


def cloudflare_blocked(status, text):
    if status in (403, 429, 503):
        low = text.lower()
        markers = ("cloudflare", "cf-ray", "attention required", "just a moment",
                   "checking your browser", "cf-chl", "enable javascript and cookies")
        return status in (429, 503) or any(m in low for m in markers)
    return False


async def get_sitemap_urls(client):
    """Locate a working sitemap and return (sitemap_url_used, [page_urls])."""
    for path in SITEMAP_CANDIDATES:
        sm = BASE + path
        status, final, text, err = await fetch(client, sm)
        log(f"  sitemap probe {sm} -> status={status} err={err}")
        if err or status != 200:
            if status and cloudflare_blocked(status, text):
                return sm, "CLOUDFLARE_BLOCK"
            continue
        locs = LOC_RE.findall(text)
        # a sitemap index points at child sitemaps (.xml); expand one level
        child_sitemaps = [u for u in locs if u.lower().endswith(".xml")]
        page_urls = [u for u in locs if not u.lower().endswith(".xml")]
        if child_sitemaps and not page_urls:
            log(f"  sitemap index with {len(child_sitemaps)} child sitemaps; expanding...")
            for child in child_sitemaps:
                cs, cfinal, ctext, cerr = await fetch(client, child)
                if cerr or cs != 200:
                    if cs and cloudflare_blocked(cs, ctext):
                        return child, "CLOUDFLARE_BLOCK"
                    continue
                page_urls.extend(u for u in LOC_RE.findall(ctext)
                                 if not u.lower().endswith(".xml"))
                if len(page_urls) >= N_PAGES * 3:
                    break
        if page_urls:
            return sm, page_urls
    return None, []


async def check_pages(client, urls):
    sem = asyncio.Semaphore(CONCURRENCY)
    results = []

    async def one(url):
        async with sem:
            await asyncio.sleep(PER_REQUEST_DELAY)
            status, final, text, err = await fetch(client, url)
            return url, status, final, text, err

    fetched = await asyncio.gather(*(one(u) for u in urls))

    all_links = {}   # link_url -> set(source pages)
    for url, status, final, text, err in fetched:
        rec = {"url": url, "status": status, "final_url": final, "error": err,
               "heading_issues": [], "token_issues": []}
        if err or status != 200 or not text:
            rec["fetch_ok"] = False
            results.append(rec)
            continue
        rec["fetch_ok"] = True
        soup = BeautifulSoup(text, "lxml")

        # --- collect links for the broken-link check (deduped globally) ---
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if href.startswith(("mailto:", "tel:", "javascript:", "#")):
                continue
            absu = urljoin(final, href)
            if not absu.lower().startswith("http"):
                continue
            all_links.setdefault(absu.split("#")[0], set()).add(url)

        # --- heading-structure check ---
        headings = [(int(h.name[1]), h.get_text(" ", strip=True))
                    for h in soup.find_all(re.compile(r"^h[1-6]$"))]
        h1s = [t for lvl, t in headings if lvl == 1]
        if len(h1s) == 0:
            rec["heading_issues"].append("no <h1> on page")
        elif len(h1s) > 1:
            rec["heading_issues"].append(f"multiple <h1> ({len(h1s)}): {h1s[:3]}")
        prev = 0
        for lvl, txt in headings:
            wc = len(txt.split())
            if prev and lvl > prev + 1:
                rec["heading_issues"].append(f"skipped level: H{prev} -> H{lvl} ('{txt[:50]}')")
            if not txt:
                rec["heading_issues"].append(f"empty H{lvl}")
            if wc > LONG_HEADING_WORDS:
                rec["heading_issues"].append(
                    f"H{lvl} wraps body-length text ({wc} words): '{txt[:70]}...'")
            prev = lvl

        # --- leftover template tokens / empty-slot artifacts (structure adjunct) ---
        visible = soup.get_text(" ", strip=True)
        if ACF_TOKEN_RE.search(text):
            rec["token_issues"].append("leftover [acf field=...] token in HTML")
        if CURLY_RE.search(visible):
            rec["token_issues"].append("leftover {{...}} token in visible text")
        dangling = set(m.group(0).strip() for m in DANGLING_RE.finditer(visible))
        dangling.discard("")
        if dangling:
            rec["token_issues"].append(f"possible empty-slot artifacts: {sorted(dangling)[:5]}")

        results.append(rec)

    # --- broken-link probe (deduped, bounded) ---
    unique_links = list(all_links.keys())
    capped = unique_links[:MAX_LINK_CHECKS]
    log(f"  unique links found: {len(unique_links)}; probing {len(capped)} "
        f"(cap={MAX_LINK_CHECKS})")
    link_status = {}

    async def probe(link):
        async with sem:
            await asyncio.sleep(PER_REQUEST_DELAY)
            status, final, _, err = await fetch(client, link, method="HEAD")
            if status in (403, 405, 501) or (status is None and err):
                status, final, _, err = await fetch(client, link, method="GET")
            link_status[link] = {"status": status, "final_url": final, "error": err,
                                 "sources": sorted(all_links[link])[:3]}

    await asyncio.gather(*(probe(l) for l in capped))
    broken = {l: s for l, s in link_status.items()
              if s["error"] or (s["status"] is not None and s["status"] >= 400)}
    return results, link_status, broken, len(unique_links), len(capped)


async def main():
    limits = httpx.Limits(max_connections=CONCURRENCY, max_keepalive_connections=CONCURRENCY)
    headers = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,application/xml"}
    async with httpx.AsyncClient(headers=headers, timeout=TIMEOUT, limits=limits,
                                 follow_redirects=True) as client:
        log(f"[1] Locating sitemap for {BASE} ...")
        sm_used, page_urls = await get_sitemap_urls(client)
        if page_urls == "CLOUDFLARE_BLOCK":
            log(f"\n!!! CLOUDFLARE BLOCK on {sm_used} — crawl access is NOT open with plain httpx.")
            log("    STOP: this is the #1 risk from the plan. Report to Asif before scaffolding.")
            Path("spike/reports").mkdir(parents=True, exist_ok=True)
            Path("spike/reports/gl_spike_report.json").write_text(json.dumps(
                {"result": "CLOUDFLARE_BLOCK", "sitemap": sm_used, "base": BASE}, indent=2))
            sys.exit(2)
        if not page_urls:
            log("\n!!! No sitemap found and no page URLs enumerated. STOP and report.")
            sys.exit(3)
        log(f"    sitemap OK: {sm_used}; enumerated {len(page_urls)} page URLs")

        sample = page_urls[:N_PAGES]
        log(f"[2] Fetching {len(sample)} pages (concurrency={CONCURRENCY}, "
            f"delay={PER_REQUEST_DELAY}s, UA=browser-like) ...")
        results, link_status, broken, n_unique, n_probed = await check_pages(client, sample)

        ok = [r for r in results if r.get("fetch_ok")]
        page_fail = [r for r in results if not r.get("fetch_ok")]
        heading_hits = [r for r in ok if r["heading_issues"]]
        token_hits = [r for r in ok if r["token_issues"]]
        issue_counter = Counter()
        for r in ok:
            for i in r["heading_issues"]:
                issue_counter[i.split(":")[0].split("(")[0].strip()] += 1

        report = {
            "result": "OK",
            "base": BASE, "sitemap_used": sm_used,
            "pages_enumerated": len(page_urls),
            "pages_fetched": len(sample),
            "pages_ok": len(ok), "pages_failed": len(page_fail),
            "unique_links_found": n_unique, "links_probed": n_probed,
            "broken_links_count": len(broken),
            "pages_with_heading_issues": len(heading_hits),
            "pages_with_token_issues": len(token_hits),
            "heading_issue_kinds": dict(issue_counter),
            "broken_links_sample": {k: broken[k] for k in list(broken)[:15]},
            "heading_issue_examples": [
                {"url": r["url"], "issues": r["heading_issues"][:4]} for r in heading_hits[:10]],
            "token_issue_examples": [
                {"url": r["url"], "issues": r["token_issues"]} for r in token_hits[:10]],
            "page_fetch_failures": [
                {"url": r["url"], "status": r["status"], "error": r["error"]} for r in page_fail[:10]],
        }
        Path("spike/reports").mkdir(parents=True, exist_ok=True)
        Path("spike/reports/gl_spike_report.json").write_text(json.dumps(report, indent=2))

        log("\n========== SPIKE RESULT ==========")
        log(f"  Crawl access:        OPEN (plain httpx, browser-like UA) — sitemap + pages fetched")
        log(f"  Sitemap used:        {sm_used}")
        log(f"  Pages enumerated:    {len(page_urls)}")
        log(f"  Pages fetched OK:    {len(ok)}/{len(sample)}  (failed: {len(page_fail)})")
        log(f"  Unique links found:  {n_unique}; probed: {n_probed}; BROKEN: {len(broken)}")
        log(f"  Heading-check hits:  {len(heading_hits)} pages  {dict(issue_counter)}")
        log(f"  Token/empty-slot:    {len(token_hits)} pages")
        if broken:
            log("  Sample broken links:")
            for k in list(broken)[:8]:
                s = broken[k]
                log(f"    [{s['status'] or s['error']}] {k}")
        log("  Full report -> spike/reports/gl_spike_report.json")
        log("==================================")


if __name__ == "__main__":
    asyncio.run(main())
