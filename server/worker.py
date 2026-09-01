"""Worker entrypoint.

Run with:  python3 -m server.worker

Deployed, this is a systemd unit with Restart=always. The nightly trigger is a systemd OnCalendar
timer with Persistent=true rather than an in-process periodic task, because a persistent timer
REPLAYS a run missed while the machine was down — an in-process scheduler simply never fires.
"""
from __future__ import annotations

from .jobs import app, reconcile_orphaned_runs

if __name__ == "__main__":
    # SAY WHAT CODE THIS IS, every start. The container runs source COPYed in at build time and the
    # compose file mounts cache/, reports/, data/ and the geodata dir — never the source. So editing
    # `auditor/` changes nothing this process executes until someone rebuilds the image, and nothing
    # used to say so: a nine-brand crawl ran against an image built 3h37m before the code it was
    # meant to run, produced zero findings from the new checks, and looked entirely normal.
    #
    # The worker cannot check itself — it has no access to the repo. What it can do is state its
    # fingerprint plainly, so the value is in the logs when someone asks "was that run current?",
    # and so `scripts/check_image_current.py` on the host has something to compare against.
    from auditor.checks_version import code_fingerprint
    print(f"[startup] running check code fingerprint {code_fingerprint()} — if this does not match "
          f"`python3 scripts/check_image_current.py` on the host, the image is STALE and needs "
          f"`docker compose build`", flush=True)

    # Before taking work: clear up anything a previously-killed worker left mid-flight, so no run
    # is stuck in `running` with no process behind it.
    reconcile_orphaned_runs()
    with app.open():
        app.run_worker(queues=["audits", "mail"], concurrency=1, install_signal_handlers=True)
