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

import asyncio
import json
import os
import random
import shutil
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from . import checks_version, crawl as C, diff, writers
from .checks import blank, enumeration, links, meta, phone, placeholder, structure
from .config import BrandConfig
from .parse import ParsedPage, parse_html
from .report import (AuditReport, Finding, PageAudit, Severity, canonical_url,
                     dedupe_findings, make_fingerprint)

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


@dataclass
class PageProjection:
    """Compact per-page residue kept AFTER the ParsedPage is dropped. Carries exactly what the
    cross-page barriers and the reports need — never the DOM or full text."""
    url: str
    final_url: str | None = None
    status: int | None = None
    content_hash: str = ""
    last_modified: str | None = None
    link_urls: list[str] = field(default_factory=list)
    title: str | None = None
    meta_description: str | None = None
    h1_text: str | None = None
    is_noindex: bool = False
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
        last_modified=r.last_modified,
        link_urls=[link.url for link in parsed.links],
        title=parsed.title, meta_description=parsed.meta_description,
        h1_text=next((h.text for h in parsed.headings if h.level == 1), None),
        is_noindex=parsed.is_noindex or bool(r.robots_header and "noindex" in r.robots_header.lower()),
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


def _progress(label: str, every: int = 200):
    """Live progress for long phases (fetch / enum / links). Prints every ``every`` completions
    and at the end, flushed so a background run's output file shows it in real time — a silent
    throttle-out (completions stalling) becomes visible instead of a black box."""
    def cb(done: int, total: int) -> None:
        if done == total or done % every == 0:
            print(f"[{time.strftime('%H:%M:%S')}] {label}: {done}/{total}", flush=True)
    return cb


def write_projection_cache(brand: str, projections: list[PageProjection]) -> tuple[Path, int]:
    """Persist the LIGHT projection per page (B5 approved schema): change-detection keys
    (content_hash, last_modified, status, final_url) + barrier inputs (link_urls, title,
    meta_description, h1_text). NOT intrinsic_findings — caching those with no read-side version
    check is the stale-findings trap. Unused until Jake's cadence is known; captured now so a
    future incremental/link-only run needs no extra full crawl. Written atomically."""
    path = C.cache_path(brand)
    cache = C.load_cache(brand)
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    for p in projections:
        cache[p.url] = {
            "content_hash": p.content_hash, "last_modified": p.last_modified,
            "status": p.status, "final_url": p.final_url, "link_urls": p.link_urls,
            "title": p.title, "meta_description": p.meta_description, "h1_text": p.h1_text,
            "fetched_at": now,
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)
    return path, len(cache)


# --------------------------------------------------------------------------- #
# Resume cache — full projections (incl. intrinsic_findings) stamped with the  #
# check-version, persisted incrementally so an interrupted large crawl (MHD    #
# 15,635 died twice at 2,600) resumes from where it stopped instead of         #
# re-fetching from scratch. DISTINCT from write_projection_cache (the light    #
# diff cache, which deliberately omits findings — the B5 stale-findings trap): #
# resume DELIBERATELY persists findings, made safe by the version stamp. Reuse #
# only when checks_version.version matches (the SAME components the diff's      #
# rule_changed keys on: src + config + parse.py + report.py + nap.py + crawl);  #
# a mismatch means cached findings are stale -> that page re-fetches + re-runs. #
# --------------------------------------------------------------------------- #
def resume_cache_path(brand: str) -> Path:
    return C.CACHE_DIR / brand.lower() / "resume.jsonl"


def _projection_row(p: PageProjection, check_version: str) -> dict:
    return {
        "check_version": check_version, "url": p.url, "final_url": p.final_url,
        "status": p.status, "content_hash": p.content_hash, "last_modified": p.last_modified,
        "link_urls": p.link_urls, "title": p.title, "meta_description": p.meta_description,
        "h1_text": p.h1_text, "is_noindex": p.is_noindex, "visible_chars": p.visible_chars,
        "intrinsic_findings": [f.model_dump(mode="json") for f in p.intrinsic_findings],
    }


def _row_projection(row: dict) -> PageProjection:
    return PageProjection(
        url=row["url"], final_url=row.get("final_url"), status=row.get("status"),
        content_hash=row.get("content_hash", ""), last_modified=row.get("last_modified"),
        link_urls=row.get("link_urls") or [], title=row.get("title"),
        meta_description=row.get("meta_description"), h1_text=row.get("h1_text"),
        is_noindex=row.get("is_noindex", False), visible_chars=row.get("visible_chars", 0),
        intrinsic_findings=[Finding.model_validate(d) for d in row.get("intrinsic_findings", [])])


def load_resume(brand: str, check_version: str) -> dict[str, PageProjection]:
    """{canonical_url: PageProjection} for pages already completed AT THE CURRENT check-version.
    Entries stamped with a different version are stale (a check/config/parse edit changed output)
    and ignored -> those pages re-fetch + re-check. Last line per url wins (crash-retry safe)."""
    path = resume_cache_path(brand)
    if not path.exists():
        return {}
    done: dict[str, PageProjection] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue  # a torn last line from a crash mid-write -> skip, don't abort the resume
        if row.get("check_version") != check_version:
            continue
        done[row["url"]] = _row_projection(row)
    return done


async def _stream_fetch_project(client, urls, config, check_version, resume_path, on_done=None):
    """Fetch -> parse -> project -> APPEND to the resume cache, per page, concurrency-capped.
    Persisting INSIDE the loop (not after a batch gather) is what makes a crawl resumable: a
    crash at page N keeps the N-1 already flushed to disk. Returns (projections, failed)."""
    resume_path.parent.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(config.crawl.max_concurrency)
    lock = asyncio.Lock()
    fh = resume_path.open("a", encoding="utf-8")
    total = len(urls)
    done_n = 0
    projections: list[PageProjection] = []
    failed: list = []

    async def one(u):
        nonlocal done_n
        async with sem:
            await asyncio.sleep(config.crawl.delay_seconds)
            status, final, text, err, h = await C._request(
                client, u, max_retries=config.crawl.max_retries)
            r = C.FetchResult(u, status, final, text or "", err,
                              last_modified=(h.get("last-modified") if h else None),
                              robots_header=(h.get("x-robots-tag") if h else None))
        if r.ok:
            parsed = parse_html(r.text, page_url=canonical_url(r.url), base_url=r.final_url or r.url)
            proj = _project(parsed, r, config)
            async with lock:
                fh.write(json.dumps(_projection_row(proj, check_version)) + "\n")
                fh.flush()
            projections.append(proj)
        else:
            failed.append(r)
        done_n += 1
        if on_done:
            on_done(done_n, total)

    try:
        await asyncio.gather(*(one(u) for u in urls))
    finally:
        fh.close()
    return projections, failed


# Heading defects that are TEMPLATE-driven walls — collapse by URL-template so 1,095 pages of
# one Elementor geo-template read as one "fix the template" finding, not 1,095 identical rows.
# Only multi_h1 (validated: one template per url-shape, spot-checked on the 1,095-page group).
_HEADING_COLLAPSE = {"multi_h1"}


def _url_template(url: str) -> str:
    """Template signature = first path segment + depth. Pages of the same section+depth share a
    page-builder template (measured: 2,010 multi-H1 pages -> 49 templates; the biggest, 1,095
    pages under /drug-rehab depth-6, confirmed one Elementor template by spot-check)."""
    segs = [s for s in urlparse(url).path.split("/") if s]
    section = segs[0] if segs else "(root)"
    return f"/{section}/*  (depth {len(segs)})"


def _collapse_headings(findings: list[Finding]) -> list[Finding]:
    """Collapse template-driven heading walls by (subtype, URL-template): identity = the template,
    sources = the pages, and a REPRESENTATIVE H1 text carried so the finding says WHAT to look at
    ('… render "FMLA Rehab near Westminster" ×N') not just a count. Lone pages stay per-page."""
    keep: list[Finding] = []
    groups: dict[tuple[str, str], list[Finding]] = {}
    for f in findings:
        parts = f.fingerprint.split(":")
        if f.check == "heading_structure" and len(parts) >= 2 and parts[1] in _HEADING_COLLAPSE:
            groups.setdefault((parts[1], _url_template(f.url)), []).append(f)
        else:
            keep.append(f)
    for (subtype, template), fs in groups.items():
        if len(fs) == 1:  # a single page under this template -> nothing to collapse
            keep.append(fs[0])
            continue
        rep = fs[0]
        sources = sorted({f.url for f in fs})
        example = rep.snippet or ""
        keep.append(Finding(
            url=sources[0], check="heading_structure", severity=rep.severity,
            fingerprint=make_fingerprint("heading_structure", subtype, "template", template),
            issue=f"{rep.issue}: {len(sources)} pages of one template", location=template,
            snippet=example,
            suggestion=f"{len(sources)} pages under {template} render duplicate H1s in the HTML "
                       f"(e.g. {example[:50]!r}; text varies by page) — ONE template fix. Low "
                       f"priority: violates the one-H1 standard + DOM bloat, not an SEO penalty.",
            details={"class": subtype, "template": template, "h1_example": example,
                     "page_count": len(sources), "sources": sources}))
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
                    max_link_probes: int | None = 400, head_sample: bool = False,
                    now: str | None = None, write: bool = True, resume: bool = False) -> dict:
    now = now or time.strftime("%Y-%m-%dT%H:%M:%S")
    async with C.make_client(config.crawl) as client:
        sitemap_urls, blocked, child_sitemaps, failed_sitemaps = await C.enumerate_sitemap(
            client, config.sitemap_url, max_retries=config.crawl.max_retries)
        sitemap_urls = C._apply_exclude(sitemap_urls, config.crawl.exclude)
        # A PARTIAL sitemap read (a throttled/5xx child dropped) silently undercounts and must never
        # produce a coverage finding — that is exactly how MHD's 15,635-URL sitemap read as 96 and
        # yielded a bogus "~1% coverage". reconcile still runs (the REST union keeps the content audit
        # best-effort), but the missing-from-sitemap FINDING (from_audit) is withheld when partial.
        sitemap_partial = bool(failed_sitemaps)

        recon = None
        if do_reconcile and config.wp_rest and config.wp_rest.enabled:
            recon = await enumeration.reconcile(client, config, sitemap_urls)

        # UNION SCOPE (default for every brand): the audit target is every LIVE page =
        # sitemap ∪ WP-REST published, deduped by canonical identity, sitemap first so the tier
        # ordering is preserved. Auditing only the sitemap silently under-covers a brand whose
        # sitemap is broken (DBH 15/483, MHD 96/8761 live) — the live set is the right denominator.
        # rest-only pages get the full content audit AND the missing-from-sitemap flag (from_audit).
        sitemap_set = {canonical_url(u) for u in sitemap_urls}
        audit_urls = list(sitemap_urls)
        rest_only_added = 0
        if recon:
            for u in recon["rest_urls"]:
                if canonical_url(u) not in sitemap_set:
                    audit_urls.append(u)
                    rest_only_added += 1

        sample = select_sample(audit_urls, limit, head=head_sample)

        # Resume: skip pages already completed at THIS check-version; a fresh run starts the
        # resume cache clean so it reflects this run's version+scope. Streaming fetch-project-persist
        # (below) writes each page as it lands, so an interrupted crawl resumes from the tail.
        check_version = checks_version.version(config)
        resume_path = resume_cache_path(config.brand)
        if resume:
            done = load_resume(config.brand, check_version)
        else:
            if resume_path.exists():
                resume_path.unlink()
            done = {}
        to_fetch = [u for u in sample if canonical_url(u) not in done]
        if done:
            print(f"[resume] reusing {len(done)} cached pages (version match); "
                  f"fetching {len(to_fetch)} of {len(sample)}", flush=True)

        # Project-and-discard, streamed: parse -> intrinsic checks -> compact projection, persisted
        # per page. Peak memory is projections (small), not the DOM of every page at once.
        fresh_projections, failed = await _stream_fetch_project(
            client, to_fetch, config, check_version, resume_path, on_done=_progress("fetch pages"))
        # cross-page barriers (dup title/desc/H1, link dedup) run over ALL projections, resumed +
        # fresh — never just the fresh ones, or dup-detection silently breaks across a resume.
        projections: list[PageProjection] = list(done.values()) + fresh_projections

        findings: list[Finding] = [f for p in projections for f in p.intrinsic_findings]
        # sitemap_unreachable is for SITEMAPPED pages that failed (a sitemap advertising a dead
        # page); REST-only failures are the enumeration rest_404 bucket (from_audit), not this.
        sitemap_failed = [r for r in failed if canonical_url(r.url) in sitemap_set]
        findings.extend(enumeration.sitemap_unreachable(sitemap_failed, config.brand))
        link_findings, link_stats = await links.check_links(
            projections, client, config, max_links=max_link_probes, on_done=_progress("link probe"))
        findings.extend(link_findings)
        findings.extend(_cross_page_duplicates(projections))

        # The 845, now DERIVED from the same union fetch (no second crawl): every live page not in
        # the sitemap, classified by (cruft, noindex) or the rest_404 integrity bucket. Goes into
        # the MAIN stream so the diff tracks each page.
        enum_stats = None
        if recon and not sitemap_partial:  # withhold coverage findings on a partial sitemap read
            enum_findings, enum_stats = enumeration.from_audit(
                projections, failed, sitemap_set, config.brand)
            findings.extend(enum_findings)

        findings = dedupe_findings(findings)  # one finding per fingerprint across the run
        findings = _collapse_phone(findings)  # site-wide numbers -> one finding each
        findings = _collapse_headings(findings)  # template-driven multi-H1 -> one per template

        run = None
        if write:
            live = set(recon["rest_urls"]) if recon else None
            out_dir = REPORTS_DIR / config.brand.lower() / _stamp(now)
            history = C.CACHE_DIR / config.brand.lower() / "history.json"
            extra = {"pages_enumerated": len(sitemap_urls), "link_stats": link_stats,
                     # union scope: how many live pages the sitemap missed (rest_only_added) and
                     # the true audit denominator (union) vs the sitemap's own advertised total.
                     "audit_scope": {"sitemap": len(sitemap_urls),
                                     "rest_only_added": rest_only_added, "union": len(audit_urls)},
                     # completeness signal: a run where many pages failed to fetch (e.g. Cloudflare
                     # started blocking mid-crawl) would persist a thin history. No hard threshold
                     # (the baseline calibrates the healthy rate) — but surface it for eyeballing.
                     "crawl": {"fetched": len(sample), "fetched_ok": len(projections),
                               "resumed_from_cache": len(done),
                               "sitemap_blocked": blocked, "sitemap_partial": sitemap_partial,
                               "sitemap_failed_children": failed_sitemaps}}
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
            write_projection_cache(config.brand, projections)  # B5 light projection (no findings)

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
            "sitemap_partial": sitemap_partial,
            "sitemap_failed_children": failed_sitemaps,
            "fetched": len(sample),
            "fetched_ok": len(projections),
            "resumed_from_cache": len(done),
            "audit_scope": {"sitemap": len(sitemap_urls),
                            "rest_only_added": rest_only_added, "union": len(audit_urls)},
            "page_visible_chars": [p.visible_chars for p in projections],
            "enum_stats": enum_stats,
            "run": run,
        }
