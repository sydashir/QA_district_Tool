"""Typer CLI for the District Site Auditor.

M0: ``audit --brand gl`` enumerates -> fetches -> caches. Checks (M1) and report
writers (M2) are not wired yet. Planned full surface (CLAUDE.md §4):
``audit --brand AH | --all | --all --since last-run``.
"""
from __future__ import annotations

import asyncio

import typer

from . import crawl as crawlmod
from .config import load_brand

app = typer.Typer(add_completion=False, help="District Site Auditor")


@app.callback()
def _main() -> None:
    """District Site Auditor — keep ``audit`` as an explicit subcommand
    (M1/M2 will add ``--all`` / ``--since`` alongside it)."""


@app.command()
def audit(
    brand: str = typer.Option(..., "--brand", "-b", help="brand code, e.g. gl"),
    limit: int = typer.Option(None, "--limit", "-n", help="cap pages fetched (testing/politeness)"),
    enumerate_only: bool = typer.Option(
        False, "--enumerate-only", help="list URLs only; do not fetch"),
):
    """M0 crawl pipeline for one brand: enumerate -> fetch -> content-hash cache."""
    config = load_brand(brand)
    summary, urls, _fetched = asyncio.run(
        crawlmod.run_m0(config, limit=limit, enumerate_only=enumerate_only))

    typer.echo(f"brand={config.brand}  base={config.base_url}")
    typer.echo(
        f"enumeration: method={summary['method']}  urls={summary['enumerated']}  "
        f"meta={summary['meta']}")

    if enumerate_only:
        for u in urls[:20]:
            typer.echo(f"  {u}")
        if len(urls) > 20:
            typer.echo(f"  ... ({len(urls)} total)")
        raise typer.Exit()

    typer.echo(f"fetched: {summary['fetched']} (ok={summary['fetched_ok']})")
    typer.echo(
        f"cache: {summary['cache_file']}  "
        f"({summary['cache_entries_total']} entries, {summary['cache_entries_updated']} updated)")


if __name__ == "__main__":
    app()
