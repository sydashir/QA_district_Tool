"""Worker entrypoint.

Run with:  python3 -m server.worker

Deployed, this is a systemd unit with Restart=always. The nightly trigger is a systemd OnCalendar
timer with Persistent=true rather than an in-process periodic task, because a persistent timer
REPLAYS a run missed while the machine was down — an in-process scheduler simply never fires.
"""
from __future__ import annotations

from .jobs import app

if __name__ == "__main__":
    with app.open():
        app.run_worker(queues=["audits", "mail"], concurrency=1, install_signal_handlers=True)
