"""One command a non-developer can run: audit every brand and publish each to the sheet.

There is no scheduler. A person triggers this, so it is built for a person:

- **One command, no flags to remember.** Not nine invocations of ``audit --brand X --publish``.
- **Smallest brand first.** TDRC (21 pages) before RR (7,721). If something is wrong — credentials,
  network, sheet access — it surfaces in the first minute on a 21-page site rather than four hours
  into the largest one.
- **Resume is automatic.** Every brand runs with resume on, so closing the laptop mid-run and
  running the same command again picks up where it stopped instead of starting over.
- **One brand failing does not stop the rest.** A failed brand is reported at the end and its
  Summary row stays ``running``, which is the visible marker that it needs another go.
- **Plain progress and a plain ending**, because whoever runs it may not read Python tracebacks.
"""
from __future__ import annotations

import pathlib
import time
import traceback

from .config import load_brand

# A run that was interrupted keeps its identity. "Run the same command again" is a CONTINUATION of
# the same run, not a new one — so brands that already published do not get a second history row
# every time someone closes the laptop. Cleared once every brand finishes.
_STATE = pathlib.Path(".run_in_progress")


def _run_id() -> tuple[str, bool]:
    if _STATE.exists():
        prior = _STATE.read_text().strip()
        if prior:
            return prior, True
    rid = time.strftime("%Y%m%dT%H%M%S")
    _STATE.write_text(rid)
    return rid, False

# Smallest first, deliberately — see the module docstring. Page counts are the last measured
# audited totals, kept here only to justify the ordering.
BRAND_ORDER = ["tdrc", "ah", "ar", "dbh", "cad", "coc", "gl", "rr", "mhd"]


def _fmt(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m" if h else (f"{m}m{s:02d}s" if m else f"{s}s")


def run_all(echo, *, dry_run: bool = False, brands: list[str] | None = None,
            limit: int | None = None, cached_only: bool = False) -> int:
    """Audit every brand and publish. Returns a process exit code (0 = every brand succeeded)."""
    import asyncio

    from . import audit as auditmod
    from .publish import publish_brand_from_result

    order = brands or BRAND_ORDER
    run_id, resumed = _run_id()
    started = time.time()
    ok: list[str] = []
    failed: list[tuple[str, str]] = []
    digests: list[str] = []

    echo(f"District Site Auditor — all brands ({len(order)})")
    echo(f"run id: {run_id}{'   [DRY RUN — nothing will be written]' if dry_run else ''}")
    if resumed:
        echo("Continuing an earlier run that did not finish — already-audited pages are cached,")
        echo("so completed brands will go quickly.")
    echo("Safe to stop at any time: run the same command again and it resumes.\n")

    for i, brand in enumerate(order, 1):
        t0 = time.time()
        echo(f"[{i}/{len(order)}] {brand.upper()} — starting")
        try:
            cfg = load_brand(brand)
            result = asyncio.run(auditmod.run_audit(cfg, resume=True, limit=limit,
                                                    cached_only=cached_only))
            pages = result.get("pages_audited", 0)
            n = len(result.get("findings", []))
            echo(f"[{i}/{len(order)}] {brand.upper()} — audited {pages} pages, {n} findings "
                 f"({_fmt(time.time() - t0)})")
            line = publish_brand_from_result(
                brand, result, run_id=run_id, dry_run=dry_run,
                started=time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t0)),
                duration_s=int(time.time() - t0))
            digests.append(line)
            echo(f"[{i}/{len(order)}] {brand.upper()} — published. {line}")
            ok.append(brand)
        except KeyboardInterrupt:
            echo("\nStopped by you. Nothing is broken — run the same command again to resume.")
            return 130
        except Exception as e:
            failed.append((brand, f"{type(e).__name__}: {e}"))
            echo(f"[{i}/{len(order)}] {brand.upper()} — FAILED: {type(e).__name__}: {e}")
            echo("    (the other brands will still run; this one can be retried on its own)")
            traceback.print_exc()

    echo("\n" + "=" * 68)
    echo(f"Finished in {_fmt(time.time() - started)}.  {len(ok)} of {len(order)} brands published.")
    for line in digests:
        echo(f"  {line}")
    if failed:
        echo(f"\n{len(failed)} brand(s) did NOT finish:")
        for brand, why in failed:
            echo(f"  {brand.upper()}: {why}")
        echo("\nWhat to do: run the same command again. Finished brands are cached and will be")
        echo("skipped quickly; the failed ones will be retried. If a brand fails twice with the")
        echo("same error, send that error to Syed.")
        echo("In the sheet, a failed brand's Summary row still says 'running' — that is the marker.")
    else:
        echo("\nAll brands published. The sheet is up to date.")
        if not dry_run:
            _STATE.unlink(missing_ok=True)      # a clean finish closes the run
    return 0 if not failed else 1
