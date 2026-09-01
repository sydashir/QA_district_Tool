"""Refuse to start a crawl against a stale container image.

THE INCIDENT THIS EXISTS FOR. The api/worker image was built at 2026-08-31T13:48Z. The batch adding
`redirected_internal`, `-2` slug detection and element selectors committed at 17:26Z. The nine-brand
run was queued at 17:27Z — one minute after the commit, three and a half hours after the image.
Every one of those runs executed the OLD code. It was only found days later, from the data:
0 `redirected_internal`, 0 `collision_slug`, 0 selectors across every run.

Nothing warned, because nothing could: `docker compose` mounts `cache`, `reports`, `data` and the
geodata directory, but never the source. The container runs what was COPYed into it.

Compares the repo's check-source fingerprint against what the running API reports. Exits non-zero
when they differ, so it can gate a run sequence instead of relying on someone remembering.

Usage:  python3 scripts/check_image_current.py
        python3 scripts/check_image_current.py --api http://127.0.0.1:8099
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from auditor.checks_version import code_fingerprint          # noqa: E402


def main(api: str) -> int:
    repo = code_fingerprint()
    try:
        with urllib.request.urlopen(f"{api.rstrip('/')}/api/health", timeout=15) as r:
            health = json.load(r)
    except (urllib.error.URLError, OSError) as e:
        print(f"  could not reach the API at {api}: {type(e).__name__}: {e}")
        print("  is the stack up?  docker compose up -d")
        return 2

    running = health.get("code_fingerprint")
    print(f"  repo    : {repo}")
    print(f"  running : {running or '(this image predates the check — definitely stale)'}")

    if running == repo:
        print("  OK — the container is running the repo's check code.")
        return 0

    print()
    print("  STALE IMAGE. The container is NOT running the code in this repo, so any crawl started")
    print("  now would silently use the old checks — exactly what happened on 2026-08-31, when a")
    print("  nine-brand run produced zero findings from checks that had been committed one minute")
    print("  earlier.")
    print()
    print("      docker compose build && docker compose up -d")
    print()
    print("  then re-run this check before queueing anything.")
    return 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8099")
    raise SystemExit(main(ap.parse_args().api))
