"""M1 audit orchestrator: enumerate -> reconcile -> fetch -> parse -> run checks.

Ties the M0 crawl layer to the M1 checks. Emits ``Finding`` objects in memory and
returns them with run stats. Report *writers* (CSV/JSON) are M2 — not here.
"""
from __future__ import annotations

from collections import defaultdict

from . import crawl as C
from .checks import blank, links, meta, phone, placeholder, structure
from .config import BrandConfig
from .parse import ParsedPage, parse_html
from .report import AuditReport, Finding, PageAudit, Severity, dedupe_findings, make_fingerprint

# Per-page checks that operate purely on a ParsedPage.
_PAGE_CHECKS = (structure, placeholder, phone, blank, meta)


async def reconcile_enumeration(client, config: BrandConfig, sitemap_urls: list[str]) -> dict:
    """Compare the sitemap URL set against WP-REST /wp/v2/pages. The delta
    ``pages_missing_from_sitemap`` = live WP pages absent from the sitemap (open
    question #8). ``sitemap_only_count`` is mostly CPTs/posts (expected, not an issue)."""
    wp_pages_only = config.wp_rest.model_copy(update={"post_types": ["pages"]})
    rest_urls, authed = await C.enumerate_wp_rest(
        client, wp_pages_only, max_retries=config.crawl.max_retries)
    rest_urls = C._apply_exclude(rest_urls, config.crawl.exclude)

    sitemap_set = {u.rstrip("/") for u in sitemap_urls}
    rest_set = {u.rstrip("/") for u in rest_urls}
    missing = sorted(rest_set - sitemap_set)
    return {
        "sitemap_total": len(sitemap_set),
        "wp_rest_pages_total": len(rest_set),
        "authed": authed,
        "pages_missing_from_sitemap": missing,
        "sitemap_only_count": len(sitemap_set - rest_set),
    }


def _cross_page_duplicates(parsed: list[ParsedPage]) -> list[Finding]:
    findings: list[Finding] = []

    def dup_by(getter, label, check, severity):
        buckets: dict[str, list[str]] = defaultdict(list)
        for p in parsed:
            val = getter(p)
            if val:
                buckets[val.strip().lower()].append(p.url)
        for val, urls in buckets.items():
            if len(urls) > 1:
                findings.append(Finding(
                    url=urls[0], check=check, severity=severity,
                    fingerprint=make_fingerprint(check, "dup", label, val),
                    issue=f"duplicate {label} across pages", location="head" if check == "meta" else "page",
                    snippet=val[:80], details={"count": len(urls), "pages": urls[:8]}))

    dup_by(lambda p: p.title, "title", "meta", Severity.WARNING)
    dup_by(lambda p: p.meta_description, "meta description", "meta", Severity.WARNING)
    dup_by(
        lambda p: next((h.text for h in p.headings if h.level == 1), None),
        "H1", "heading_structure", Severity.WARNING)
    return findings


async def run_audit(config: BrandConfig, limit: int | None = None,
                    do_reconcile: bool = True, max_link_probes: int | None = 400) -> dict:
    async with C.make_client(config.crawl) as client:
        sitemap_urls, blocked, child_sitemaps = await C.enumerate_sitemap(
            client, config.sitemap_url, max_retries=config.crawl.max_retries)
        sitemap_urls = C._apply_exclude(sitemap_urls, config.crawl.exclude)

        recon = None
        if do_reconcile and config.wp_rest and config.wp_rest.enabled:
            # The delta is DATA here. The dedicated 845 report (later step) fetches robots
            # and emits per-page enumeration:missing_from_sitemap:{brand}:{url} with D1
            # severity; the count summary lives in the rollup — not a single stream finding.
            recon = await reconcile_enumeration(client, config, sitemap_urls)

        sample = sitemap_urls[:limit] if limit else sitemap_urls
        fetched = await C.fetch_pages(client, sample, config.crawl)
        ok = [r for r in fetched if r.ok]
        parsed = [parse_html(r.text, r.final_url or r.url) for r in ok]

        findings: list[Finding] = []
        page_audits: list[PageAudit] = []
        for p, r in zip(parsed, ok):
            page_findings: list[Finding] = []
            for mod in _PAGE_CHECKS:
                page_findings.extend(mod.run(p, config))
            page_findings = dedupe_findings(page_findings)  # collapse identical repeats
            findings.extend(page_findings)
            page_audits.append(PageAudit(
                url=p.url, final_url=r.final_url, status=r.status, fetched_ok=True,
                content_hash=C.page_hash(p.raw_html),  # single-source stable hash (P5)
                findings=page_findings))

        link_findings, link_stats = await links.check_links(
            parsed, client, config, max_links=max_link_probes)
        findings.extend(link_findings)
        findings.extend(_cross_page_duplicates(parsed))
        findings = dedupe_findings(findings)  # one finding per fingerprint across the run

        report = AuditReport(
            brand=config.brand, base_url=config.base_url, enumeration_method="sitemap",
            pages_enumerated=len(sitemap_urls), pages_fetched=len(parsed), pages=page_audits)

        return {
            "report": report,
            "findings": findings,
            "recon": recon,
            "link_stats": link_stats,
            "child_sitemaps": child_sitemaps,
            "sitemap_blocked": blocked,
            "fetched": len(fetched),
            "fetched_ok": len(ok),
            "page_visible_chars": [len(p.visible_text) for p in parsed],
        }
