#!/usr/bin/env python3
"""Queue the nightly audits. Run by auditor-nightly.service, fired by auditor-nightly.timer.

It goes through the API (`POST /api/brands/<code>/runs`) rather than deferring to Procrastinate
directly, so the nightly path and the dashboard button are the SAME path: one in-flight guard, one
place that creates the `queued` run row. A second code path would be a second set of bugs, and the
guard is the thing that stops a nightly job stacking up behind a run that is still going.

Enqueueing is instant. The worker runs one job at a time, so all eight land in the queue at 02:00
and drain in order over the next ~7 hours — which is why the order below is smallest-first: if the
night runs out of time, the brands that got audited are the many small ones, not one big one.

Stdlib only, on purpose: this must work from a bare `python3` on the box even if the venv is being
rebuilt.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

API_URL = os.getenv("AUDITOR_API_URL", "http://127.0.0.1:8099").rstrip("/")
PING_URL = os.getenv("HEALTHCHECK_PING_URL", "").strip()
TIMEOUT = 30

# The API may not be listening yet. This is not hypothetical: the whole point of the timer's
# Persistent=true is that a missed run REPLAYS AT BOOT, and at boot systemd has started the API
# process but not waited for it to bind. Without this the replay — the one run that most needs to
# succeed — dies on connection-refused.
STARTUP_ATTEMPTS = 6
STARTUP_WAIT_S = 10

# Smallest first (measured page counts, README.md). Anything not listed is appended alphabetically,
# so a brand added later still gets queued — just not in a place this list can vouch for.
SIZE_ORDER = ["TDRC", "AH", "AR", "DBH", "CAD", "COC", "GL", "RR"]

# MHD is excluded here as well as by its NULL schedule_cron in the database. Belt and braces,
# because the cost of getting it wrong is uniquely bad: its origin throttles to ~10 pages/min and a
# full 11,439-page census is ~131 HOURS. It is meant to run as a labelled partial sample, and
# `POST /api/brands/{code}/runs` has no sample-size parameter to ask for one. Removing this guard
# requires giving the API that parameter first.
NEVER_SCHEDULE = {"MHD"}


def _get(path: str):
    with urllib.request.urlopen(f"{API_URL}{path}", timeout=TIMEOUT) as r:
        return json.loads(r.read())


def _post(path: str) -> tuple[int, dict | str]:
    req = urllib.request.Request(f"{API_URL}{path}", method="POST", data=b"{}",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(body)
        except ValueError:
            return e.code, body


def _ping(suffix: str = "") -> None:
    """Tell an external watchdog the night happened.

    Optional. Without it, the failure mode this whole timer exists to prevent — nobody noticing
    that nothing ran — is only visible to whoever thinks to check the dashboard.
    """
    if not PING_URL:
        return
    try:
        urllib.request.urlopen(PING_URL + suffix, timeout=10).read()
    except Exception as e:                                # noqa: BLE001 — never fail the run on this
        print(f"[nightly] healthcheck ping failed (ignored): {type(e).__name__}: {e}")


def _brands_when_ready():
    """First call, retried — see STARTUP_ATTEMPTS. Raises the last error if it never comes up."""
    last: Exception | None = None
    for attempt in range(1, STARTUP_ATTEMPTS + 1):
        try:
            return _get("/api/brands")
        except Exception as e:                            # noqa: BLE001 — any failure is "not up"
            last = e
            if attempt < STARTUP_ATTEMPTS:
                print(f"[nightly] API not answering ({type(e).__name__}), "
                      f"retry {attempt}/{STARTUP_ATTEMPTS - 1} in {STARTUP_WAIT_S}s")
                time.sleep(STARTUP_WAIT_S)
    raise last                                            # type: ignore[misc]


def main() -> int:
    try:
        brands = _brands_when_ready()
    except Exception as e:                                # noqa: BLE001
        print(f"[nightly] could not reach the API at {API_URL}: {type(e).__name__}: {e}",
              file=sys.stderr)
        _ping("/fail")
        return 1

    scheduled = [b["code"] for b in brands
                 if b.get("scheduled") and b["code"] not in NEVER_SCHEDULE]
    skipped = [b["code"] for b in brands if b["code"] not in scheduled]
    scheduled.sort(key=lambda c: (SIZE_ORDER.index(c) if c in SIZE_ORDER else len(SIZE_ORDER), c))

    if not scheduled:
        # Not an error to shrug at: it means every brand's schedule_cron is NULL, so the nightly
        # audit silently does nothing. Fail loudly so `systemctl status` shows it.
        print("[nightly] no brands are scheduled — nothing was queued", file=sys.stderr)
        _ping("/fail")
        return 1

    print(f"[nightly] queueing {len(scheduled)}: {', '.join(scheduled)}")
    if skipped:
        print(f"[nightly] not scheduled, skipped: {', '.join(sorted(skipped))} "
              f"(MHD is unscheduled by design — partial sample only)")

    failed: list[str] = []
    for code in scheduled:
        status, body = _post(f"/api/brands/{code}/runs")
        if status == 202:
            print(f"[nightly]   {code}: queued as run {body.get('run_id')}")
        elif status == 409:
            # Already running. Normal after a night that overran, and explicitly not a failure —
            # the guard did its job and stopped a duplicate crawl of the same origin.
            print(f"[nightly]   {code}: already in flight, left alone")
        else:
            print(f"[nightly]   {code}: FAILED to queue ({status}): {body}", file=sys.stderr)
            failed.append(code)

    if failed:
        _ping("/fail")
        return 1
    _ping()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
