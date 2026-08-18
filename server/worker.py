"""Worker entrypoint.

Run with:  python3 -m server.worker

Deployed, this is a systemd unit with Restart=always. The nightly trigger is a systemd OnCalendar
timer with Persistent=true rather than an in-process periodic task, because a persistent timer
REPLAYS a run missed while the machine was down — an in-process scheduler simply never fires.
"""
from __future__ import annotations

from .jobs import app, reconcile_orphaned_runs

if __name__ == "__main__":
    # Before taking work: clear up anything a previously-killed worker left mid-flight, so no run
    # is stuck in `running` with no process behind it.
    reconcile_orphaned_runs()
    with app.open():
        app.run_worker(queues=["audits", "mail"], concurrency=1, install_signal_handlers=True)
