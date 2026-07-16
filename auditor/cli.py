"""Typer CLI for the District Site Auditor.

M1: ``audit --brand gl`` runs enumerate -> reconcile -> fetch -> parse -> checks and
prints a per-check summary. ``--no-checks`` runs the M0 crawl+cache path only;
``--enumerate-only`` lists URLs. Report *writers* (CSV/JSON) are M2. Planned full
surface (CLAUDE.md §4): ``audit --brand AH | --all | --all --since last-run``.
"""
from __future__ import annotations

import asyncio
from collections import Counter

import typer

from . import audit as auditmod
from . import crawl as crawlmod
from .config import load_brand

app = typer.Typer(add_completion=False, help="District Site Auditor")


@app.callback()
def _main() -> None:
    """District Site Auditor — keep ``audit`` as an explicit subcommand
    (M1/M2 will add ``--all`` / ``--since`` alongside it)."""


def _print_summary(cfg, result) -> None:
    findings = result["findings"]
    typer.echo(f"brand={cfg.brand}  base={cfg.base_url}")
    typer.echo(
        f"enumeration: sitemap urls={result['report'].pages_enumerated}  "
        f"child_sitemaps={result['child_sitemaps']}  blocked={result['sitemap_blocked']}")
    if result["recon"]:
        r = result["recon"]
        typer.echo(
            f"reconciliation: sitemap={r['sitemap_total']}  wp_rest_pages={r['wp_rest_pages_total']}  "
            f"missing_from_sitemap={len(r['pages_missing_from_sitemap'])}  (OQ#8)")
    typer.echo(f"fetched: {result['fetched_ok']}/{result['fetched']} ok")

    vc = sorted(result["page_visible_chars"])
    if vc:
        p = lambda q: vc[min(len(vc) - 1, int(q * len(vc)))]
        typer.echo(f"visible_chars: min={vc[0]} p10={p(0.10)} median={p(0.50)} max={vc[-1]}")

    by_check = Counter(f.check for f in findings)
    by_sev = Counter(f.severity.value for f in findings)
    typer.echo(f"\nfindings: {len(findings)} total  by_severity={dict(by_sev)}")
    for check in sorted(by_check):
        sevs = Counter(f.severity.value for f in findings if f.check == check)
        issues = Counter(f.issue for f in findings if f.check == check)
        typer.echo(f"  {check}: {by_check[check]}  {dict(sevs)}")
        for issue, n in issues.most_common(6):
            typer.echo(f"      {n:>4}  {issue}")

    ls = result["link_stats"]
    typer.echo(
        f"\nlink stats: unique={ls['unique_targets']} probed={ls['probed']} "
        f"broken={ls['broken']} bot_blocked_ignored={ls['bot_blocked_ignored']} "
        f"cdn_cgi_excluded={ls['cdn_cgi_excluded']} redirects={ls['redirects']}")

    run = result.get("run")
    if run:
        by_status = dict(run["rollup"].by_status)
        typer.echo(f"\nrun-diff: by_status={by_status}  resolved={len(run['resolved'])}")
        if run["changed"]:
            typer.echo(f"  check-version changed: {run['changed']} -> vanished findings tagged rule_changed")
        typer.echo(f"reports: {run['out_dir']}")


@app.command()
def audit(
    brand: str = typer.Option(..., "--brand", "-b", help="brand code, e.g. gl"),
    limit: int = typer.Option(None, "--limit", "-n", help="cap pages fetched (sample size)"),
    max_link_probes: int = typer.Option(400, "--max-link-probes", help="cap unique links probed"),
    enumerate_only: bool = typer.Option(False, "--enumerate-only", help="list URLs; no fetch"),
    no_checks: bool = typer.Option(False, "--no-checks", help="M0 path: crawl + cache only"),
):
    """Run the M1 audit (enumerate -> reconcile -> fetch -> checks) for one brand."""
    cfg = load_brand(brand)

    if enumerate_only or no_checks:
        summary, urls, _ = asyncio.run(
            crawlmod.run_m0(cfg, limit=limit, enumerate_only=enumerate_only))
        typer.echo(f"brand={cfg.brand}  base={cfg.base_url}")
        typer.echo(f"enumeration: method={summary['method']} urls={summary['enumerated']} meta={summary['meta']}")
        if enumerate_only:
            for u in urls[:20]:
                typer.echo(f"  {u}")
            if len(urls) > 20:
                typer.echo(f"  ... ({len(urls)} total)")
        else:
            typer.echo(
                f"fetched: {summary['fetched']} (ok={summary['fetched_ok']})  "
                f"cache: {summary['cache_entries_total']} entries")
        raise typer.Exit()

    result = asyncio.run(
        auditmod.run_audit(cfg, limit=limit, max_link_probes=max_link_probes))
    _print_summary(cfg, result)


if __name__ == "__main__":
    app()
