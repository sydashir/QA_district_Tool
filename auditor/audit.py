"""Audit orchestrator: enumerate -> reconcile -> fetch -> (project + intrinsic checks) ->
cross-page barriers -> run-diff -> write reports.

Memory shape (the point of the projection): a full ``ParsedPage`` carries the whole DOM +
visible text; holding 2.7k of them at GL cost multiple GB. So each page is parsed, its
intrinsic checks run, and then it is COLLAPSED to a compact ``PageProjection`` and the
ParsedPage is dropped. The cross-page barriers (link dedup, duplicate title/desc/H1) run off
projections, never raw pages. Findings themselves are small and are held.

M2 wiring lives in ``write_run``: annotate every finding in-stream via ``RunDiff`` (first_seen/
last_seen/status), emit the resolved tail, roll up counters, write JSONL+CSV+summary to a
timestamped ``reports/<brand>/<stamp>/`` dir, and persist the run history for the next diff.
"""
from __future__ import annotations

import os
import random
import shutil
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from . import checks_version, crawl as C, diff, writers
from .checks import blank, enumeration, links, meta, phone, placeholder, structure
from .config import BrandConfig
from .parse import ParsedPage, parse_html
from .report import AuditReport, Finding, PageAudit, Severity, dedupe_findings, make_fingerprint

# Per-page checks that operate purely on a ParsedPage.
_PAGE_CHECKS = (structure, placeholder, phone, blank, meta)

REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports"


_SAMPLE_SEED = 0xD15  # fixed -> the sampled subset is reproducible across runs (for diffing)


def select_sample(urls: list[str], limit: int | None, head: bool = False) -> list[str]:
    """Pick ``limit`` URLs to audit. DEFAULT is seeded-random across the WHOLE sitemap:
    representative AND reproducible. First-N (``head=True``) is skewed — sitemaps are ordered by
    page type, so the first N is one cohort (that skew misled heading 98-vs-73, n=25 alphabetical,
    and the 400 links — three times, same root cause). First-N stays only for debugging a prefix."""
    if not limit or limit >= len(urls):
        return urls
    if head:
        return urls[:limit]
    return sorted(random.Random(_SAMPLE_SEED).sample(urls, limit))


def canonical_url(url: str) -> str:
    """Page IDENTITY normalization: the requested URL with any trailing slash stripped. Single
    source of truth so fingerprints, the audited set, the cache, and the run-diff all key a page
    the same way — a redirect changes the landing URL but must NOT change identity (else the page
    churns new/resolved between runs for no real reason)."""
    return url.rstrip("/")


@dataclass
class PageProjection:
    """Compact per-page residue kept AFTER the ParsedPage is dropped. Carries exactly what the
    cross-page barriers and the reports need — never the DOM or full text."""
    url: str
    final_url: str | None = None
    status: int | None = None
    content_hash: str = ""
    link_urls: list[str] = field(default_factory=list)
    title: str | None = None
    meta_description: str | None = None
    h1_text: str | None = None
    visible_chars: int = 0
    intrinsic_findings: list[Finding] = field(default_factory=list)


def _project(parsed: ParsedPage, r, config: BrandConfig) -> PageProjection:
    """Run intrinsic checks and collapse a ParsedPage to a projection. The ParsedPage is
    expected to be released by the caller right after."""
    page_findings: list[Finding] = []
    for mod in _PAGE_CHECKS:
        page_findings.extend(mod.run(parsed, config))
    page_findings = dedupe_findings(page_findings)  # collapse identical repeats
    return PageProjection(
        url=parsed.url, final_url=r.final_url, status=r.status,
        content_hash=C.page_hash(parsed.raw_html),  # single-source stable hash (P5)
        link_urls=[link.url for link in parsed.links],
        title=parsed.title, meta_description=parsed.meta_description,
        h1_text=next((h.text for h in parsed.headings if h.level == 1), None),
        visible_chars=len(parsed.visible_text), intrinsic_findings=page_findings)


def _cross_page_duplicates(projections: list[PageProjection]) -> list[Finding]:
    findings: list[Finding] = []

    def dup_by(getter, label, check, severity):
        buckets: dict[str, list[str]] = defaultdict(list)
        for p in projections:
            val = getter(p)
            if val:
                buckets[val.strip().lower()].append(p.url)
        for val, urls in buckets.items():
            if len(urls) > 1:
                findings.append(Finding(
                    url=urls[0], check=check, severity=severity,
                    fingerprint=make_fingerprint(check, "dup", label, val),
                    issue=f"duplicate {label} across pages",
                    location="head" if check == "meta" else "page",
                    snippet=val[:80], details={"count": len(urls), "pages": urls[:8]}))

    dup_by(lambda p: p.title, "title", "meta", Severity.WARNING)
    dup_by(lambda p: p.meta_description, "meta description", "meta", Severity.WARNING)
    dup_by(lambda p: p.h1_text, "H1", "heading_structure", Severity.WARNING)
    return findings


# Phone classification findings whose identity is the NUMBER, not the page — collapsed
# site-wide like broken_links (identity=target). mismatch/malformed stay per-page (they're
# element-specific and low-volume).
_PHONE_COLLAPSE = {"retired", "unknown", "non_canonical"}


def _collapse_phone(findings: list[Finding]) -> list[Finding]:
    """A wrong number on 3,591 pages is ONE fix, not 3,591 rows. Collapse each
    (classification, number) to a single finding carrying every source page — same shape as
    broken_links, so the CSV writer flattens it (page_url + source_count) and the diff tracks
    the number site-wide (fix the global element -> it resolves once, not 3,591 times)."""
    keep: list[Finding] = []
    groups: dict[tuple[str, str], list[Finding]] = {}
    for f in findings:
        parts = f.fingerprint.split(":")
        if f.check == "phone" and len(parts) >= 2 and parts[1] in _PHONE_COLLAPSE:
            e164 = (f.details or {}).get("number") or parts[-1]
            groups.setdefault((parts[1], e164), []).append(f)
        else:
            keep.append(f)
    for (cls, e164), fs in groups.items():
        rep = fs[0]
        sources = sorted({f.url for f in fs})
        keep.append(Finding(
            url=sources[0], check="phone", severity=rep.severity,
            fingerprint=make_fingerprint("phone", cls, e164),  # identity = the number, site-wide
            issue=rep.issue, location="page", snippet=e164, suggestion=rep.suggestion,
            details={**(rep.details or {}), "sources": sources, "page_count": len(sources)}))
    return keep


def _stamp(now: str) -> str:
    """ISO ``2026-07-16T01:15:00`` -> filesystem-safe ``20260716-011500`` (to the second, so
    back-to-back runs never clobber)."""
    return now.replace("-", "").replace(":", "").replace("T", "-")[:15]


def write_run(findings: list[Finding], projections: list[PageProjection], *, brand: str,
              base_url: str, now: str, config: BrandConfig, live, out_dir: Path,
              history_path: Path, extra_meta: dict | None = None) -> dict:
    """Annotate in-stream, emit the resolved tail, write JSONL+CSV+summary, persist history.
    Pure w.r.t. the network — unit-tested directly. ``live`` is the WP-REST live-page set (or
    None); ``audited`` is every URL we actually evaluated this run."""
    components = checks_version.components(config)
    prior = diff.load_history(history_path)
    rd = diff.RunDiff(prior, now, components,
                      audited={p.url for p in projections}, live=live)
    for f in findings:
        rd.annotate(f)
    resolved = rd.resolved_findings()
    rows = findings + resolved  # findings small; the DOM was the memory cost, not these

    rollup = writers.Rollup()
    for f in rows:
        rollup.add(f)
    # First run has no baseline -> nothing "changed" (an empty prior isn't a ruleset edit).
    changed = diff.changed_components(rd.prior_components, components) if rd.prior_components else []
    meta_blob = {
        "brand": brand, "base_url": base_url, "run_at": now,
        "pages_audited": len(projections), "changed_components": changed,
        **(extra_meta or {}),
    }

    # Write the whole report into a .partial dir, persist history atomically, THEN atomically
    # promote the dir. A crash mid-write leaves a visibly-incomplete "<stamp>.partial" dir (never
    # a real-looking authoritative report) and no history — so a re-run starts clean, not poisoned.
    titles = {p.url: (p.title or "") for p in projections}
    out_dir = Path(out_dir)
    staging = out_dir.with_name(out_dir.name + ".partial")
    if staging.exists():
        shutil.rmtree(staging)
    writers.write_jsonl(rows, staging / "findings.jsonl")
    writers.write_csv(rows, staging / "findings.csv", brand, titles)
    writers.write_summary(rollup, meta_blob, staging / "summary.json")
    rd.persist(history_path)               # atomic (temp + os.replace)
    os.replace(staging, out_dir)           # atomic promote: partial -> authoritative
    return {"out_dir": out_dir, "rollup": rollup, "resolved": resolved,
            "components": components, "changed": changed}


async def run_audit(config: BrandConfig, limit: int | None = None, do_reconcile: bool = True,
                    max_link_probes: int | None = 400, enum_probes: int | None = 0,
                    head_sample: bool = False, now: str | None = None, write: bool = True) -> dict:
    now = now or time.strftime("%Y-%m-%dT%H:%M:%S")
    async with C.make_client(config.crawl) as client:
        sitemap_urls, blocked, child_sitemaps = await C.enumerate_sitemap(
            client, config.sitemap_url, max_retries=config.crawl.max_retries)
        sitemap_urls = C._apply_exclude(sitemap_urls, config.crawl.exclude)

        recon = None
        if do_reconcile and config.wp_rest and config.wp_rest.enabled:
            recon = await enumeration.reconcile(client, config, sitemap_urls)

        sample = select_sample(sitemap_urls, limit, head=head_sample)
        fetched = await C.fetch_pages(client, sample, config.crawl)
        ok = [r for r in fetched if r.ok]

        # Project-and-discard: parse -> intrinsic checks -> compact projection; the ParsedPage
        # is unreferenced after each iteration and collected, so peak memory is projections
        # (small) not the DOM of every page at once.
        projections: list[PageProjection] = []
        for r in ok:
            # identity = requested URL (canonical); links resolve against the final/landing URL
            parsed = parse_html(r.text, page_url=canonical_url(r.url), base_url=r.final_url or r.url)
            projections.append(_project(parsed, r, config))

        findings: list[Finding] = [f for p in projections for f in p.intrinsic_findings]
        link_findings, link_stats = await links.check_links(
            projections, client, config, max_links=max_link_probes)
        findings.extend(link_findings)
        findings.extend(_cross_page_duplicates(projections))

        # The 845: per-page enumeration findings for live-but-unsitemapped pages. Robots-only
        # fetch (NO audit checks — out of scope); goes into the MAIN stream so the diff tracks
        # each page. enum_probes: 0 = skip (smokes), None = all (baseline), N = cap.
        enum_stats = None
        if recon and enum_probes != 0:
            enum_findings, enum_stats = await enumeration.run(
                client, config, recon["pages_missing_from_sitemap"], probe_cap=enum_probes)
            findings.extend(enum_findings)

        findings = dedupe_findings(findings)  # one finding per fingerprint across the run
        findings = _collapse_phone(findings)  # site-wide numbers -> one finding each

        run = None
        if write:
            live = set(recon["rest_urls"]) if recon else None
            out_dir = REPORTS_DIR / config.brand.lower() / _stamp(now)
            history = C.CACHE_DIR / config.brand.lower() / "history.json"
            extra = {"pages_enumerated": len(sitemap_urls), "link_stats": link_stats,
                     # completeness signal: a run where many pages failed to fetch (e.g. Cloudflare
                     # started blocking mid-crawl) would persist a thin history. No hard threshold
                     # (the baseline calibrates the healthy rate) — but surface it for eyeballing.
                     "crawl": {"fetched": len(fetched), "fetched_ok": len(ok),
                               "sitemap_blocked": blocked}}
            if enum_stats is not None:  # the 845 bisection (INFO/WARNING/ERROR) for the human view
                extra["enumeration"] = enum_stats
            if config.canon is not None:  # name the phone-scope limitation IN the report
                extra["phone_scope_caveat"] = (
                    "Per-location numbers are validated brand-wide, not per-page; a valid number "
                    "rendered on the wrong location's page is NOT flagged. Canonical from the NAP "
                    "2026-07-02 snapshot; NAP_SHEET_ID unverified.")
            run = write_run(
                findings, projections, brand=config.brand, base_url=config.base_url,
                now=now, config=config, live=live, out_dir=out_dir, history_path=history,
                extra_meta=extra)
            C.write_cache(config.brand, ok)

        report = AuditReport(
            brand=config.brand, base_url=config.base_url, enumeration_method="sitemap",
            pages_enumerated=len(sitemap_urls), pages_fetched=len(projections),
            pages=[PageAudit(url=p.url, final_url=p.final_url, status=p.status, fetched_ok=True,
                             content_hash=p.content_hash, findings=p.intrinsic_findings)
                   for p in projections])

        return {
            "report": report,
            "findings": findings,
            "recon": recon,
            "link_stats": link_stats,
            "child_sitemaps": child_sitemaps,
            "sitemap_blocked": blocked,
            "fetched": len(fetched),
            "fetched_ok": len(ok),
            "page_visible_chars": [p.visible_chars for p in projections],
            "enum_stats": enum_stats,
            "run": run,
        }
