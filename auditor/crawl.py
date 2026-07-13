"""Crawl layer: sitemap + WP-REST enumeration, polite async fetch, content-hash cache.

Ported from the session-1 spike (``spike/gl_spike.py``) — same httpx + asyncio approach
and the crawl settings the spike proved work against GL's Cloudflare. Scope boundary:
the checks are M1 and the cache *diff* is M2. M0 stores content hashes only.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from .config import BrandConfig, WPRestConfig

_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.IGNORECASE)
CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"


@dataclass
class FetchResult:
    url: str
    status: int | None
    final_url: str | None
    text: str
    error: str | None

    @property
    def ok(self) -> bool:
        return self.error is None and self.status == 200 and bool(self.text)


# --------------------------------------------------------------------------- #
# HTTP primitive (ported from the spike's fetch(), with retries)              #
# --------------------------------------------------------------------------- #
async def _request(client, url, method="GET", max_retries=1, req_headers=None):
    """Return (status, final_url, text, error, response_headers). Retries transient errors."""
    for attempt in range(max_retries + 1):
        try:
            r = await client.request(method, url, headers=req_headers)
            text = r.text if method == "GET" else ""
            return r.status_code, str(r.url), text, None, r.headers
        except (httpx.TimeoutException, httpx.TransportError) as e:
            if attempt == max_retries:
                return None, url, "", f"{type(e).__name__}: {e}", {}
            await asyncio.sleep(0.5)


def _cloudflare_blocked(status, text) -> bool:
    if status in (403, 429, 503):
        low = (text or "").lower()
        markers = (
            "cloudflare", "cf-ray", "attention required", "just a moment",
            "checking your browser", "cf-chl", "enable javascript and cookies",
        )
        return status in (429, 503) or any(m in low for m in markers)
    return False


def make_client(crawl) -> httpx.AsyncClient:
    limits = httpx.Limits(
        max_connections=crawl.max_concurrency,
        max_keepalive_connections=crawl.max_concurrency,
    )
    headers = {
        "User-Agent": crawl.user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml",
    }
    return httpx.AsyncClient(
        headers=headers, timeout=crawl.timeout_seconds, limits=limits, follow_redirects=True
    )


# --------------------------------------------------------------------------- #
# Enumeration                                                                 #
# --------------------------------------------------------------------------- #
async def enumerate_sitemap(client, sitemap_url, max_retries=1, max_depth=3):
    """Index-aware sitemap enumeration: expand nested <sitemapindex> entries into
    page <urlset> URLs. Returns (page_urls, blocked, child_sitemap_count)."""
    seen_sitemaps: set[str] = set()
    page_urls: list[str] = []
    blocked = False

    async def walk(sm_url, depth):
        nonlocal blocked
        if depth > max_depth or sm_url in seen_sitemaps:
            return
        seen_sitemaps.add(sm_url)
        status, _final, text, err, _h = await _request(client, sm_url, max_retries=max_retries)
        if err or status != 200:
            if status and _cloudflare_blocked(status, text):
                blocked = True
            return
        locs = _LOC_RE.findall(text)
        children = [u for u in locs if u.lower().endswith(".xml")]
        page_urls.extend(u for u in locs if not u.lower().endswith(".xml"))
        for child in children:
            await walk(child, depth + 1)

    await walk(sitemap_url, 0)

    out, s = [], set()
    for u in page_urls:
        if u not in s:
            s.add(u)
            out.append(u)
    child_count = max(0, len(seen_sitemaps) - 1)  # visited sitemaps minus the root index
    return out, blocked, child_count


def _wp_auth_header(wp: WPRestConfig) -> str | None:
    """Basic Application-Password header from env vars, or None (unauth). Creds are
    read from the environment only — never from this repo."""
    if not (wp.username_env and wp.password_env):
        return None
    user, pw = os.getenv(wp.username_env), os.getenv(wp.password_env)
    if not (user and pw):
        return None
    tok = base64.b64encode(f"{user}:{pw}".encode()).decode()
    return f"Basic {tok}"


async def enumerate_wp_rest(client, wp: WPRestConfig, max_retries=1, per_page=100):
    """Fallback enumeration via WP REST /wp/v2/<post_type>. Paginates on
    X-WP-TotalPages. Returns (page_urls, authed:bool)."""
    auth = _wp_auth_header(wp)
    req_headers = {"Authorization": auth} if auth else None
    urls: list[str] = []
    for pt in wp.post_types:
        page = 1
        while True:
            ep = (f"{wp.base_url.rstrip('/')}/wp-json/wp/v2/{pt}"
                  f"?per_page={per_page}&page={page}&_fields=link")
            status, _final, text, err, headers = await _request(
                client, ep, max_retries=max_retries, req_headers=req_headers)
            if err or status != 200:
                break
            try:
                items = json.loads(text)
            except (ValueError, TypeError):
                break
            if not items:
                break
            urls.extend(i.get("link") for i in items if i.get("link"))
            total_pages = int(headers.get("X-WP-TotalPages", "0") or 0)
            if page >= total_pages or page > 500:
                break
            page += 1

    out, s = [], set()
    for u in urls:
        if u and u not in s:
            s.add(u)
            out.append(u)
    return out, bool(auth)


def _apply_exclude(urls, exclude) -> list[str]:
    return [u for u in urls if not any(x in u for x in exclude)]


async def enumerate_pages(client, config: BrandConfig):
    """Sitemap first (index-aware); WP-REST fallback if the sitemap is blocked/empty.
    Returns (urls, method, meta)."""
    urls, blocked, child_count = await enumerate_sitemap(
        client, config.sitemap_url, max_retries=config.crawl.max_retries)
    urls = _apply_exclude(urls, config.crawl.exclude)
    if urls and not blocked:
        return urls, "sitemap", {"child_sitemaps": child_count}

    if config.wp_rest and config.wp_rest.enabled:
        rest_urls, authed = await enumerate_wp_rest(
            client, config.wp_rest, max_retries=config.crawl.max_retries)
        rest_urls = _apply_exclude(rest_urls, config.crawl.exclude)
        method = "wp-rest+auth" if authed else "wp-rest"
        reason = "sitemap blocked" if blocked else "sitemap empty"
        return rest_urls, method, {"reason": reason, "post_types": config.wp_rest.post_types}

    return urls, "sitemap", {"child_sitemaps": child_count, "blocked": blocked}


# --------------------------------------------------------------------------- #
# Fetch + content-hash cache                                                  #
# --------------------------------------------------------------------------- #
async def fetch_pages(client, urls, crawl) -> list[FetchResult]:
    sem = asyncio.Semaphore(crawl.max_concurrency)

    async def one(u):
        async with sem:
            await asyncio.sleep(crawl.delay_seconds)
            status, final, text, err, _h = await _request(client, u, max_retries=crawl.max_retries)
            return FetchResult(u, status, final, text or "", err)

    return await asyncio.gather(*(one(u) for u in urls))


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def cache_path(brand: str) -> Path:
    return CACHE_DIR / brand.lower() / "pages.json"


def load_cache(brand: str) -> dict:
    p = cache_path(brand)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except (ValueError, OSError):
            return {}
    return {}


def write_cache(brand: str, results: list[FetchResult]) -> tuple[Path, int, int]:
    """Store a content hash per successfully-fetched page. Returns
    (path, total_entries, updated_this_run). The diff itself is M2."""
    p = cache_path(brand)
    p.parent.mkdir(parents=True, exist_ok=True)
    cache = load_cache(brand)
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    updated = 0
    for r in results:
        if r.ok:
            cache[r.url] = {
                "content_hash": content_hash(r.text),
                "status": r.status,
                "final_url": r.final_url,
                "content_length": len(r.text),
                "fetched_at": now,
            }
            updated += 1
    p.write_text(json.dumps(cache, indent=2, sort_keys=True))
    return p, len(cache), updated


# --------------------------------------------------------------------------- #
# M0 pipeline: enumerate -> fetch -> cache                                     #
# --------------------------------------------------------------------------- #
async def run_m0(config: BrandConfig, limit: int | None = None, enumerate_only: bool = False):
    """Run the M0 crawl pipeline for one brand. Returns (summary, urls, fetched)."""
    async with make_client(config.crawl) as client:
        urls, method, meta = await enumerate_pages(client, config)
        summary = {"method": method, "meta": meta, "enumerated": len(urls)}
        if enumerate_only:
            return summary, urls, []

        sample = urls[:limit] if limit else urls
        fetched = await fetch_pages(client, sample, config.crawl)
        cache_file, total, updated = write_cache(config.brand, fetched)
        summary.update({
            "fetched": len(sample),
            "fetched_ok": sum(1 for r in fetched if r.ok),
            "cache_file": str(cache_file),
            "cache_entries_total": total,
            "cache_entries_updated": updated,
        })
        return summary, urls, fetched
