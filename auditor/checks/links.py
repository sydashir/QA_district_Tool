"""Broken-links check (v1 deterministic) — cross-page, dedup, tuned.

Collects every ``<a href>`` across the parsed sample, dedups to unique targets, and
probes each once (HEAD, then GET fallback). Ships WITH the tuning the spike proved
necessary so it doesn't misfire:

- ``/cdn-cgi/`` targets are excluded (Cloudflare email-protection artifacts, not links).
- Bot-hostile hosts (LinkedIn 999, Facebook 400, LegitScript / Joint Commission 403 …)
  are treated as a distinct **non-finding** ("reachable but bot-blocks crawlers"),
  never reported as broken.
- Genuine 4xx/5xx, timeouts/transport errors, and redirects landing on >=400 ARE flagged.
  The spike's one real finding (an internal ``/authors/…`` 404) must still fire.

Returns (findings, stats). One finding per unique broken TARGET (with its source pages),
not one per occurrence.
"""
from __future__ import annotations

import asyncio
from urllib.parse import urlparse

import httpx

from ..report import Finding, Severity

CHECK = "broken_links"
_CDN_CGI = "/cdn-cgi/"

# Hosts that answer bots with 4xx/999 despite being perfectly reachable. A hit here is
# NOT a broken link — it's a bot block. Kept as a tunable allowlist.
BOT_HOSTILE_HOSTS = {
    "linkedin.com", "www.linkedin.com",
    "facebook.com", "www.facebook.com", "m.facebook.com",
    "instagram.com", "www.instagram.com",
    "twitter.com", "x.com", "www.twitter.com",
    "tiktok.com", "www.tiktok.com",
    "youtube.com", "www.youtube.com",
    "legitscript.com", "www.legitscript.com",
    "jointcommission.org", "www.jointcommission.org", "quality.jointcommission.org",
    "bbb.org", "www.bbb.org",
    "yelp.com", "www.yelp.com",
}


def _host(url: str) -> str:
    return urlparse(url).netloc.lower()


async def _head_or_get(client, url):
    """Return (status, final_url, error). HEAD first; GET fallback on 403/405/501 or error."""
    try:
        r = await client.request("HEAD", url)
        if r.status_code in (403, 405, 501):
            r = await client.request("GET", url)
        return r.status_code, str(r.url), None
    except (httpx.TimeoutException, httpx.TransportError):
        try:
            r = await client.request("GET", url)
            return r.status_code, str(r.url), None
        except (httpx.TimeoutException, httpx.TransportError) as e:
            return None, url, f"{type(e).__name__}"


async def check_links(pages, client, config, max_links: int | None = None):
    # unique target -> source page urls
    targets: dict[str, list[str]] = {}
    excluded_cdn = 0
    for page in pages:
        for link in page.links:
            if _CDN_CGI in link.url:
                excluded_cdn += 1
                continue
            targets.setdefault(link.url, []).append(page.url)

    unique = list(targets)
    capped = unique[:max_links] if max_links else unique

    sem = asyncio.Semaphore(config.crawl.max_concurrency)
    status: dict[str, tuple] = {}

    async def probe(url):
        async with sem:
            await asyncio.sleep(config.crawl.delay_seconds)
            status[url] = await _head_or_get(client, url)

    await asyncio.gather(*(probe(u) for u in capped))

    findings: list[Finding] = []
    stats = {
        "unique_targets": len(unique),
        "probed": len(capped),
        "cdn_cgi_excluded": excluded_cdn,
        "bot_blocked_ignored": 0,
        "broken": 0,
        "redirects": 0,
    }

    for url in capped:
        code, final, err = status[url]
        host = _host(url)
        sources = targets[url][:5]

        if host in BOT_HOSTILE_HOSTS and (err or (code and code >= 400)):
            stats["bot_blocked_ignored"] += 1
            continue

        if final and final.split("#")[0].rstrip("/") != url.rstrip("/") and code and code < 400:
            stats["redirects"] += 1  # benign redirect; tagged in stats, not a finding in M1

        if err:
            stats["broken"] += 1
            findings.append(Finding(
                url=sources[0], check=CHECK, severity=Severity.WARNING,
                issue="unreachable (timeout/transport error)", location=url, snippet=err,
                details={"target": url, "sources": sources}))
        elif code is not None and code >= 400:
            stats["broken"] += 1
            findings.append(Finding(
                url=sources[0], check=CHECK, severity=Severity.ERROR,
                issue=f"HTTP {code}", location=url, snippet=f"{code} (landed on {final})",
                suggestion="Broken link — fix or remove.",
                details={"target": url, "status": code, "final_url": final, "sources": sources}))

    return findings, stats
