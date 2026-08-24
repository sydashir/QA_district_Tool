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
import logging
import os
import random
import re
import shutil
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from . import checks_version, crawl as C, diff, writers
from .checks import (actions, blank, brands, duplication, empty_row, empty_slot,
                     enumeration, links, meta, misspelling, phone, placeholder, scope,
                     spelling, structure)
from .config import BrandConfig
from .css_cache import BrandCSS
from .parse import ParsedPage, parse_html
from .report import (AuditReport, Finding, PageAudit, Severity, canonical_url,
                     dedupe_findings, make_fingerprint)

# Per-page checks that operate purely on a ParsedPage.
_PAGE_CHECKS = (structure, placeholder, empty_slot, misspelling, scope, spelling, phone,
                blank, meta, actions, brands, duplication, empty_row)
# Checks whose findings are derived from ``visible_text`` and are therefore only as trustworthy as
# our knowledge of what the page HIDES (see ParsedPage.css_status).
_VISIBLE_TEXT_CHECKS = frozenset({"blank", "placeholder", "empty_slot", "misspelling",
                                 "scope", "phone", "spelling"})

REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports"
_log = logging.getLogger(__name__)


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


def _project(parsed: ParsedPage, r, config: BrandConfig, ledger=None) -> PageProjection:
    """Run intrinsic checks and collapse a ParsedPage to a projection. The ParsedPage is
    expected to be released by the caller right after."""
    # The site-level typo ledger is filled HERE because this is the only moment the page's text
    # exists — the ParsedPage is released as soon as this returns. It stores counts and one short
    # context per rare token, never text. See misspelling.TokenLedger.
    if ledger is not None:
        ledger.add_page(parsed)
    page_findings: list[Finding] = []
    for mod in _PAGE_CHECKS:
        page_findings.extend(mod.run(parsed, config))
    page_findings = dedupe_findings(page_findings)  # collapse identical repeats
    # A partial CSS read must NOT produce a confident finding. Without every stylesheet we cannot
    # know what the page hides, so visible_text may still carry text no reader sees — exactly the
    # class of defect that produced fabricated "missing space" findings. Same discipline as
    # withholding coverage findings on a partial sitemap read: say so, do not quietly proceed.
    if parsed.css_status not in ("ok", "none"):
        for f in page_findings:
            if f.check in _VISIBLE_TEXT_CHECKS:
                f.details = dict(f.details or {})
                f.details["css_status"] = parsed.css_status
                f.details["confidence"] = "low"
                f.suggestion = (f"{f.suggestion} NOTE: this brand's stylesheets could not be fully "
                                f"read ({parsed.css_status}), so hidden text may not have been "
                                f"excluded — verify against the rendered page before acting.")
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
        "check_version": check_version, "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "url": p.url, "final_url": p.final_url,
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


RESUME_MAX_AGE_HOURS = 48  # a resumed page older than this is re-fetched (the site has moved on)


def load_resume(brand: str, check_version: str,
                max_age_hours: float = RESUME_MAX_AGE_HOURS) -> dict[str, PageProjection]:
    """{canonical_url: PageProjection} for pages already completed AT THE CURRENT check-version.
    Entries stamped with a different version are stale (a check/config/parse edit changed output)
    and ignored -> those pages re-fetch + re-check. Last line per url wins (crash-retry safe).

    Rows older than ``max_age_hours`` are ALSO ignored: a resume is for continuing an interrupted
    crawl, not for re-emitting last week's crawl as today's report. Without this, `--resume` after a
    completed run reuses everything, fetches nothing, and dates a stale snapshot as current."""
    path = resume_cache_path(brand)
    if not path.exists():
        return {}
    cutoff = time.time() - max_age_hours * 3600
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
        ts = row.get("fetched_at")
        if ts:
            try:
                if time.mktime(time.strptime(ts, "%Y-%m-%dT%H:%M:%S")) < cutoff:
                    continue  # too old to pass off as "this run"
            except ValueError:
                pass
        done[row["url"]] = _row_projection(row)
    return done


async def _stream_fetch_project(client, urls, config, check_version, resume_path, on_done=None,
                                brand_css=None, ledger=None):
    """Fetch -> parse -> project -> APPEND to the resume cache, per page, concurrency-capped.
    Persisting INSIDE the loop (not after a batch gather) is what makes a crawl resumable: a
    crash at page N keeps the N-1 already flushed to disk. Returns (projections, failed).

    Two hard requirements learned the hard way:
    - ONE BAD PAGE MUST NOT KILL THE CRAWL. Every page body is wrapped, and the gather uses
      return_exceptions=True. Without this, a single unexpected raise (an httpx error outside the
      narrow retry catch, a check blowing up on odd markup) aborts a 29h census before write_run
      ever runs — and because only successes are cached, every --resume dies on the same page.
    - ORDER MUST BE DETERMINISTIC. Results are placed BY INDEX, not appended on completion:
      completion order varies per run, and the capped link probe (`to_probe[:max_links]`) would
      then pick a different subset each run, so an unprobed broken link reads as `resolved` — a
      fix claim that never happened.
    """
    resume_path.parent.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(config.crawl.max_concurrency)
    lock = asyncio.Lock()
    css_lock = asyncio.Lock()
    if brand_css is None:
        brand_css = BrandCSS()
    fh = resume_path.open("a", encoding="utf-8")
    total = len(urls)
    done_n = 0
    slots: list[PageProjection | None] = [None] * len(urls)
    failed: list = []

    async def one(i, u):
        nonlocal done_n
        try:
            async with sem:
                await asyncio.sleep(config.crawl.delay_seconds)
                status, final, text, err, h = await C._request(
                    client, u, max_retries=config.crawl.max_retries)
                r = C.FetchResult(u, status, final, text or "", err,
                                  last_modified=(h.get("last-modified") if h else None),
                                  robots_header=(h.get("x-robots-tag") if h else None))
            if r.ok:
                # ONE stylesheet fetch per brand, on the first page that succeeds. The theme's CSS
                # is identical across URLs, and it is where the display:none rules that decide what
                # is actually visible live. Guarded so concurrent pages do not all fetch it.
                if not brand_css._loaded:
                    async with css_lock:
                        await brand_css.load(client, r.text, r.final_url or r.url,
                                             max_retries=config.crawl.max_retries)
                parsed = parse_html(r.text, page_url=canonical_url(r.url),
                                    base_url=r.final_url or r.url,
                                    extra_css=brand_css.css, css_status=brand_css.status)
                proj = _project(parsed, r, config, ledger=ledger)
                async with lock:
                    fh.write(json.dumps(_projection_row(proj, check_version)) + "\n")
                    fh.flush()
                slots[i] = proj
            else:
                failed.append(r)
        except Exception as e:  # one page must never sink the run — record it and carry on
            _log.warning("page failed to audit (%s): %s: %s", u, type(e).__name__, e)
            failed.append(C.FetchResult(u, None, None, "", f"{type(e).__name__}: {e}"))
        finally:
            done_n += 1
            if on_done:
                on_done(done_n, total)

    try:
        # return_exceptions=True so a raise inside one task can't cancel/abort its siblings
        await asyncio.gather(*(one(i, u) for i, u in enumerate(urls)), return_exceptions=True)
    finally:
        fh.close()  # after gather returns -> no in-flight task can write to a closed handle
    return [p for p in slots if p is not None], failed


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


# A sister brand named on a LARGE SHARE of a brand's pages is a template, a footer-adjacent
# disclosure, or a real network relationship — all deliberate. One named on a handful of pages is
# the anomaly the client reported. Measured on 95 live pages: legitimate mentions ran 67-92% of
# pages (AH's disclosure copy 10/11, DBH's directory 11/12), never a thin tail.
_BRAND_SHARE_MAX = 0.02      # keep only a sister appearing on <=2% of the brand's pages
_BRAND_PAGES_MAX = 25        # ...and never more than this many pages outright
_BRAND_MIN_CORPUS = 50       # below this, there is no baseline -> claim nothing


def _collapse_brands(findings: list[Finding], audited_pages: int) -> list[Finding]:
    """Keep only ANOMALOUSLY RARE sister-brand mentions; drop the site-wide ones.

    The per-page check cannot tell a stray copy-paste from deliberate cross-brand copy, because
    the two are identical on the page. Across the site they are not: the deliberate ones are
    everywhere. This is the discriminator, and it is why B6 could ship at all — see the
    measurement in `checks/brands.py`.

    On a small run there is no baseline to judge rarity against, so nothing is reported rather
    than reported wrongly — the same discipline as withholding coverage findings on a partial
    sitemap read.
    """
    keep: list[Finding] = []
    groups: dict[str, list[Finding]] = {}
    for f in findings:
        if f.check == "brands":
            groups.setdefault(str(f.details.get("other_brand", "")), []).append(f)
        else:
            keep.append(f)
    if not groups:
        return keep
    if audited_pages < _BRAND_MIN_CORPUS:
        _log.info("brands: %d pages audited (<%d) — no baseline for rarity, withholding %d finding(s)",
                  audited_pages, _BRAND_MIN_CORPUS, sum(len(v) for v in groups.values()))
        return keep
    for other, fs in groups.items():
        pages = {f.url for f in fs}
        share = len(pages) / max(1, audited_pages)
        if share <= _BRAND_SHARE_MAX and len(pages) <= _BRAND_PAGES_MAX:
            keep.extend(fs)
        else:
            _log.info("brands: %r named on %d/%d pages (%.1f%%) — deliberate, dropping %d finding(s)",
                      other, len(pages), audited_pages, share * 100, len(fs))
    return keep


# `actions` belongs here for the same reason: GL's Instagram-icon-to-LinkedIn fault is in the
# header and footer template, so it produced 119 rows across 60 pages — one template field, one
# fix. Keyed on the snippet, which is the button label for a dead CTA and the destination for a
# misrouted icon, so two different dead buttons never merge into one row.
_TEMPLATE_COLLAPSE_CHECKS = {"duplication", "empty_row", "actions",
                             # Added after the first full nine-brand run, which showed every long
                             # tab is ONE template repeated, not many problems:
                             #   GL   4,002 missing_unit rows ->    1 shape over 1,149 pages
                             #   COC  1,873 missing_unit rows ->    2 shapes over  811 pages
                             #   MHD  1,056 misspellings     ->    1 shape over  352 pages
                             #   AH     171 county_for_country ->  1 shape over  169 pages
                             # A row per page told the QA team there were 4,002 things to fix when
                             # there was one missing word ("within 25 of Long Beach" wants "miles").
                             "empty_slot", "misspelling", "scope"}
_DIGITS = re.compile(r"\d+")
_TEMPLATE_MIN_PAGES = 3     # two pages is a coincidence; three is a template


def _collapse_repeats(findings: list[Finding]) -> list[Finding]:
    """One template fault on 1,500 pages is ONE fix, not 1,500 rows.

    Same principle as `_collapse_phone` (a wrong number site-wide) and `_collapse_headings`. The
    two defects this collapses are template-driven by nature: GL's "Addictions Gratitude Lodge
    Treats" grid has a hole in the same row on every geo page, and the Hydromorphone paragraph
    renders three times on the same template across two brands. Identity is the CONTENT, not the
    page, so fixing the template resolves one finding instead of leaving 1,500 half-resolved.
    """
    keep: list[Finding] = []
    groups: dict[tuple[str, str, str], list[Finding]] = {}
    for f in findings:
        if f.check in _TEMPLATE_COLLAPSE_CHECKS:
            d = f.details or {}
            # Key on the defect's SHAPE, not the instance. The snippet carries the surrounding
            # sentence, so "within 25 of Long Beach" and "within 30 of Newport" looked like two
            # defects; `matched`/`word` with digits normalised makes them the one they are.
            shape = str(d.get("matched") or d.get("word") or d.get("text")
                        or d.get("example") or f.snippet or "")
            key = _DIGITS.sub("N", shape)[:120].lower()
            # The content string alone is not an identity: two social icons can share one wrong
            # destination while being labelled for DIFFERENT networks, and two dead buttons can
            # share a snippet. Fold in the per-class discriminator so distinct defects stay
            # distinct rows instead of silently merging into one.
            disc = "|".join(str(d.get(k, "")) for k in
                            ("intended", "actual", "label", "href", "tag", "matched"))
            groups.setdefault((f.check, str(d.get("class", "")), key, disc), []).append(f)
        else:
            keep.append(f)
    for (check, cls, key, _disc), fs in groups.items():
        sources = sorted({f.url for f in fs})
        if len(sources) < _TEMPLATE_MIN_PAGES:
            keep.extend(fs)
            continue
        rep = fs[0]
        keep.append(Finding(
            url=sources[0], check=check, severity=rep.severity,
            fingerprint=make_fingerprint(check, cls, "template", key),
            issue=f"{rep.issue} — on {len(sources)} pages",
            location=rep.location, snippet=rep.snippet,
            suggestion=(f"{rep.suggestion} This appears on {len(sources)} pages, so it comes from "
                        f"a shared template — one fix corrects all of them."),
            details={**(rep.details or {}), "page_count": len(sources),
                     "sources": sources[:8], "template_wide": True}))
    return keep


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
              history_path: Path, extra_meta: dict | None = None,
              persist_history: bool = True) -> dict:
    """Annotate in-stream, emit the resolved tail, write JSONL+CSV+summary, persist history.
    Pure w.r.t. the network — unit-tested directly. ``live`` is the WP-REST live-page set (or
    None); ``audited`` is every URL we actually evaluated this run.

    ``persist_history=False`` writes the report but leaves the diff baseline ALONE. A ``-n``
    sample is a tuning tool, not a baseline: a 40-page CAD sample became the baseline for the next
    FULL run, which then reported **3,362 "new"** findings that were not new at all. The open count
    stayed correct; the delta in front of the QA team was meaningless. Brands never sampled showed
    1/36/42 new in the same run, which is what a real delta looks like."""
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
        "history_written": bool(persist_history),
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
    if persist_history:
        rd.persist(history_path)           # atomic (temp + os.replace)
    else:
        print(f"[diff] sampled run — diff baseline for {brand.upper()} left untouched "
              f"(this report is complete; the next run still diffs against the last FULL run)",
              flush=True)
    os.replace(staging, out_dir)           # atomic promote: partial -> authoritative
    return {"out_dir": out_dir, "rollup": rollup, "resolved": resolved,
            "components": components, "changed": changed}


async def run_audit(config: BrandConfig, limit: int | None = None, do_reconcile: bool = True,
                    max_link_probes: int | None = 400, head_sample: bool = False,
                    now: str | None = None, write: bool = True, resume: bool = False,
                    cached_only: bool = False) -> dict:
    now = now or time.strftime("%Y-%m-%dT%H:%M:%S")
    brand_css = BrandCSS()          # one stylesheet fetch per brand, shared by every page
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

        # LAST-RESORT ENUMERATION: a static page list, when the site has no index at all.
        # This has to live HERE, not only in crawl.enumerate_pages: run_audit does its own
        # sitemap+REST union and never calls that helper, so a fallback added there passed its
        # unit tests while DBH still audited 0 pages in production. Same shape as the WP-REST
        # fallback above — used only when both real sources yielded nothing.
        urls_file_used = None
        if not audit_urls and getattr(config, "urls_file", None):
            listed, method, meta = await C.enumerate_pages(client, config)
            if method == "urls-file" and listed:
                audit_urls = listed
                sitemap_set = set()          # there is no sitemap to be missing from
                urls_file_used = meta
                print(f"[enumerate] {config.brand.upper()}: no sitemap and no WP-REST — using the "
                      f"static page list ({len(listed)} URLs). This list CANNOT discover pages "
                      f"added since it was written.", flush=True)

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
        # SCOPE INTERSECTION (required): the version stamp guards check LOGIC, never SCOPE. A page
        # cached by an earlier leg may no longer be in scope — deleted from the site, newly matched
        # by crawl.exclude, or outside a --limit sample. Merging it anyway would audit a page that
        # isn't there: its cached findings re-emit as "persisting", from_audit MINTS a fresh
        # missing-from-sitemap WARNING for it, and because it lands in the diff's audited set the
        # correct page_removed classification becomes unreachable. So drop out-of-scope rows, loudly.
        scope = {canonical_url(u) for u in sample}
        dropped = [u for u in done if u not in scope]
        for u in dropped:
            del done[u]
        to_fetch = [u for u in sample if canonical_url(u) not in done]
        # PUBLISH WHAT IS BANKED. MHD's host throttles below even our gentle config (<1 page/min
        # measured), so a full pass is not reachable. --cached-only audits exactly the pages already
        # in the resume cache and fetches nothing, so the brand can be published as an explicitly
        # PARTIAL sample rather than not at all. The partial flag travels to the Summary tab; a
        # partial audit must never be presented as a complete one.
        # PARTIAL means "this did not cover the brand", whichever way it happened: a --limit that
        # truncated the URL set, or --cached-only skipping the un-fetched tail. Either way the
        # Summary row must say so — a 400-page sample of a 15,635-page site presented without
        # qualification reads as a complete audit.
        partial_sample = len(sample) < len(audit_urls)
        # Narrower than partial_sample on purpose — see the write_run call below.
        limit_truncated = bool(limit) and len(sample) < len(audit_urls)
        if cached_only:
            partial_sample = partial_sample or len(to_fetch) > 0
            print(f"[cached-only] publishing {len(done)} banked pages; "
                  f"NOT fetching the remaining {len(to_fetch)} of {len(sample)}")
            to_fetch = []
        if done or dropped:
            print(f"[resume] reusing {len(done)} cached pages (version match); "
                  f"fetching {len(to_fetch)} of {len(sample)}"
                  + (f"; DROPPED {len(dropped)} cached pages no longer in scope" if dropped else ""),
                  flush=True)

        # Project-and-discard, streamed: parse -> intrinsic checks -> compact projection, persisted
        # per page. Peak memory is projections (small), not the DOM of every page at once.
        token_ledger = misspelling.TokenLedger()
        fresh_projections, failed = await _stream_fetch_project(
            client, to_fetch, config, check_version, resume_path, on_done=_progress("fetch pages"),
            brand_css=brand_css, ledger=token_ledger)
        # cross-page barriers (dup title/desc/H1, link dedup) run over ALL projections, resumed +
        # fresh — never just the fresh ones, or dup-detection silently breaks across a resume.
        # Ordered by SAMPLE position (not resumed-then-fresh, not completion order) so the capped
        # link probe sees the same target window every run; otherwise an unprobed broken link
        # silently reads as `resolved` in the diff.
        _pos = {canonical_url(u): i for i, u in enumerate(sample)}
        projections: list[PageProjection] = sorted(
            list(done.values()) + fresh_projections,
            key=lambda p: _pos.get(canonical_url(p.url), len(_pos)))

        findings: list[Finding] = [f for p in projections for f in p.intrinsic_findings]
        # sitemap_unreachable is for SITEMAPPED pages that failed (a sitemap advertising a dead
        # page); REST-only failures are the enumeration rest_404 bucket (from_audit), not this.
        sitemap_failed = [r for r in failed if canonical_url(r.url) in sitemap_set]
        findings.extend(enumeration.sitemap_unreachable(sitemap_failed, config.brand))
        link_findings, link_stats = await links.check_links(
            projections, client, config, max_links=max_link_probes, on_done=_progress("link probe"))
        findings.extend(link_findings)
        findings.extend(_cross_page_duplicates(projections))
        # Site-level typo mining. "Appears 1-3 times" is a statement about the SITE, so it
        # cannot be computed per page. Emits NOTHING when the run was a truncated sample or
        # when too much of it came from the resume cache (resumed pages carry no text, so the
        # counts would be of a subset and a common word could read as rare). Both gates live
        # in misspelling.from_audit.
        findings.extend(misspelling.from_audit(
            token_ledger, audited_pages=len(projections), partial_sample=bool(limit_truncated)))

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
        # sister-brand mentions: keep only the anomalously rare ones (deliberate ones are site-wide)
        findings = _collapse_brands(findings, len(projections))
        # template-driven duplicate/empty-slot faults -> one finding per template
        findings = _collapse_repeats(findings)
        findings = _collapse_headings(findings)  # template-driven multi-H1 -> one per template

        run = None
        # A run that audited NOTHING must not produce an authoritative-looking report. It happens for
        # real: if the sitemap index itself fails (throttled host) and WP-REST is also unreachable,
        # the union is empty — and a written report would claim "0 findings" and persist an empty
        # history that makes the NEXT run's diff nonsense. Refuse loudly instead; same principle as
        # withholding coverage findings on a partial sitemap read.
        if write and not projections:
            print("ERROR: 0 pages audited (enumeration returned nothing — check sitemap_partial / "
                  "host availability). NO report written, history untouched.", flush=True)
        elif write:
            live = set(recon["rest_urls"]) if recon else None
            out_dir = REPORTS_DIR / config.brand.lower() / _stamp(now)
            history = C.CACHE_DIR / config.brand.lower() / "history.json"
            extra = {"pages_enumerated": len(sitemap_urls), "link_stats": link_stats,
                     # union scope: how many live pages the sitemap missed (rest_only_added) and
                     # the true audit denominator (union) vs the sitemap's own advertised total.
                     "audit_scope": {"sitemap": len(sitemap_urls),
                                     "rest_only_added": rest_only_added,
                                     "union": len(audit_urls)},
                     # provenance: set when the brand had NO index and was audited from a static
                     # page list, so a report can never be mistaken for full coverage.
                     "urls_file": urls_file_used,
                     # completeness signal: a run where many pages failed to fetch (e.g. Cloudflare
                     # started blocking mid-crawl) would persist a thin history. No hard threshold
                     # (the baseline calibrates the healthy rate) — but surface it for eyeballing.
                     # fetched = what THIS run actually requested (resumed pages are reported
                     # separately) — a resumed run must never imply it re-fetched the cached set.
                     "crawl": {"fetched": len(to_fetch), "fetched_ok": len(fresh_projections),
                               "resumed_from_cache": len(done), "pages_audited": len(projections),
                               "sitemap_blocked": blocked, "sitemap_partial": sitemap_partial,
                               "sitemap_failed_children": failed_sitemaps}}
            if enum_stats is not None:  # the 845 bisection (INFO/WARNING/ERROR) for the human view
                extra["enumeration"] = enum_stats
            if config.canon is not None:  # name the phone-scope limitation IN the report
                extra["phone_scope_caveat"] = (
                    "Per-location numbers are validated brand-wide, not per-page; a valid "
                    "number rendered on the wrong location's page is NOT flagged. Canonical "
                    "from the 2026-07-20 snapshot of the verified live NAP sheet.")
            # A --limit sample must NOT become the diff baseline. --cached-only is deliberately
            # EXCLUDED from this rule: it is MHD's standing publishing mode (its host throttles
            # below a full pass), so refusing it a baseline would make every MHD run report its
            # whole finding set as "new" forever.
            run = write_run(
                findings, projections, brand=config.brand, base_url=config.base_url,
                now=now, config=config, live=live, out_dir=out_dir, history_path=history,
                extra_meta=extra, persist_history=not limit_truncated)
            write_projection_cache(config.brand, projections)  # B5 light projection (no findings)
            # The run COMPLETED and its report is written — retire the resume cache so a later
            # `--resume` can't silently replay a finished crawl as a fresh report. Kept (renamed)
            # rather than deleted, so a post-mortem can still inspect it.
            if resume_path.exists():
                os.replace(resume_path, resume_path.with_suffix(".done.jsonl"))

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
            "partial_sample": partial_sample,
            "sample_target": len(sample),
            "scope_total": len(audit_urls),
            # so the Summary tab can show when hidden-content detection was degraded
            "css_status": getattr(brand_css, "status", "none"),
            "css_missing_sheets": list(getattr(brand_css, "missing_sheets", []) or []),
            "sitemap_failed_children": failed_sitemaps,
            "fetched": len(to_fetch),
            "fetched_ok": len(fresh_projections),
            "resumed_from_cache": len(done),
            "pages_audited": len(projections),
            "audit_scope": {"sitemap": len(sitemap_urls),
                            "rest_only_added": rest_only_added, "union": len(audit_urls)},
            "page_visible_chars": [p.visible_chars for p in projections],
            "enum_stats": enum_stats,
            "run": run,
        }
