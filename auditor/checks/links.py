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
import re
from urllib.parse import urlparse

import httpx

from ..report import Finding, Severity, make_fingerprint

CHECK = "broken_links"
_CDN_CGI = "/cdn-cgi/"

# "The host refused/blocked OUR request" — verification impossible, NOT proof the page is gone.
# On an EXTERNAL host this is `unverified` (a browser probe found CDC=200, pubmed=reCAPTCHA,
# but samhsa=403-in-browser — so we can't auto-tell bot-block from dead; surface, don't suppress).
# On an INTERNAL host it's a real finding — our own site shouldn't refuse. This host-scope+status
# rule replaces the old BOT_HOSTILE_HOSTS allowlist, which was permanent per-brand maintenance debt
# and would have silently hidden the dead SAMHSA link.
_REFUSED = {400, 401, 402, 403, 406, 429, 999}
# WP staging hosts leaked into production content — a real copy-paste-from-staging defect.
_STAGING_RE = re.compile(r"(cloudwaysapps\.com|wpengine\.com|\bstaging\b)", re.I)


def _host(url: str) -> str:
    return urlparse(url).netloc.lower()


def _registrable(host: str) -> str:
    return host.removeprefix("www.")


def _is_malformed(url: str) -> bool:
    """Truncated/broken URL in page content (e.g. ``http://gabapen`` — a pasted URL cut off
    mid-string). A netloc with no dot has no TLD -> it can never resolve; it's a content bug,
    not a network failure."""
    p = urlparse(url)
    return not p.netloc or "." not in p.netloc


async def _head_or_get(client, url, timeout):
    """Return (status, final_url, error). HEAD first; GET fallback on 403/405/501 or error.
    ``timeout`` is explicit per call so the caller can give internal links the full budget and
    external hosts a short one (both HEAD and the GET retry honor it)."""
    try:
        r = await client.request("HEAD", url, timeout=timeout)
        if r.status_code in (403, 405, 501):
            r = await client.request("GET", url, timeout=timeout)
        return r.status_code, str(r.url), None
    except (httpx.TimeoutException, httpx.TransportError):
        try:
            r = await client.request("GET", url, timeout=timeout)
            return r.status_code, str(r.url), None
        except (httpx.TimeoutException, httpx.TransportError) as e:
            return None, url, f"{type(e).__name__}"


def _finding(target, sources, subtype, severity, issue, suggestion, cls, **details):
    return Finding(
        url=sources[0], check=CHECK, severity=severity,
        fingerprint=make_fingerprint(CHECK, subtype, target) if subtype
        else make_fingerprint(CHECK, target),  # broken keeps identity = bare target (stable)
        issue=issue, location=target, snippet=str(details.get("status") or target),
        suggestion=suggestion,
        details={"target": target, "class": cls, "sources": sources, **details})


async def check_links(pages, client, config, max_links: int | None = None, on_done=None):
    # ``pages`` are compact projections: each has ``.url`` and ``.link_urls`` (the DOM is long
    # gone by now). unique target -> source page urls
    targets: dict[str, list[str]] = {}
    excluded_cdn = 0
    for page in pages:
        for url in page.link_urls:
            if _CDN_CGI in url:
                excluded_cdn += 1
                continue
            targets.setdefault(url, []).append(page.url)

    internal = _registrable(_host(config.base_url))
    findings: list[Finding] = []
    stats = {"unique_targets": len(targets), "cdn_cgi_excluded": excluded_cdn, "probed": 0,
             "malformed": 0, "staging": 0, "broken": 0, "unverified": 0, "redirects": 0}

    # Content-bug classes are decidable WITHOUT a probe — flag them and don't waste a request.
    to_probe: list[str] = []
    for url in targets:
        sources = targets[url][:5]
        if _is_malformed(url):
            stats["malformed"] += 1
            findings.append(_finding(
                url, sources, "malformed", Severity.ERROR,
                "malformed / truncated URL in page content", "Not a valid URL (truncated or "
                "broken) — fix the link.", "malformed_link"))
        elif _STAGING_RE.search(_host(url)):
            stats["staging"] += 1
            findings.append(_finding(
                url, sources, "staging", Severity.ERROR,
                "links to a staging host (content copied from staging)", "A staging URL is live "
                "in production content — repoint it to production.", "staging_link"))
        else:
            to_probe.append(url)

    capped = to_probe[:max_links] if max_links else to_probe
    stats["probed"] = len(capped)

    full_timeout = config.crawl.timeout_seconds
    ext_timeout = getattr(config.crawl, "external_link_timeout_seconds", 5.0)
    sem = asyncio.Semaphore(config.crawl.max_concurrency)
    status: dict[str, tuple] = {}
    total = len(capped)
    done = 0

    async def probe(url):
        nonlocal done
        # host-scope budget: internal links get the full timeout (a real 404 is a real finding);
        # external hosts get the short one (they classify `unverified` regardless of exact status).
        timeout = full_timeout if _registrable(_host(url)) == internal else ext_timeout
        async with sem:
            await asyncio.sleep(config.crawl.delay_seconds)
            status[url] = await _head_or_get(client, url, timeout)
        done += 1
        if on_done:
            on_done(done, total)

    await asyncio.gather(*(probe(u) for u in capped))

    for url in capped:
        code, final, err = status[url]
        sources = targets[url][:5]
        is_internal = _registrable(_host(url)) == internal

        if final and final.split("#")[0].rstrip("/") != url.rstrip("/") and code and code < 400:
            stats["redirects"] += 1  # benign redirect; tagged in stats, not a finding in M1

        if err:
            stats["broken"] += 1
            findings.append(_finding(
                url, sources, None, Severity.WARNING, "unreachable (timeout/transport error)",
                "Link did not respond — verify it's reachable.", "unreachable", error=err))
        elif code in _REFUSED and not is_internal:
            # external host blocked the crawler -> we COULDN'T verify (bot-block or dead). Surface,
            # don't call it broken (CDC/pubmed are fine) and don't suppress (samhsa may be dead).
            stats["unverified"] += 1
            findings.append(_finding(
                url, sources, "unverified", Severity.INFO,
                f"external link unverified — host returned HTTP {code} (bot-block or forbidden)",
                "Crawler was blocked; check manually whether the link is dead.",
                "unverified_external", status=code))
        elif code is not None and code >= 400:  # gone (404/410), 5xx, or an INTERNAL refusal
            stats["broken"] += 1
            findings.append(_finding(
                url, sources, None, Severity.ERROR, f"HTTP {code}",
                "Broken link — fix or remove.", "broken",
                status=code, final_url=final))

    return findings, stats
