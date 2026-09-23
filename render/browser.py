"""Where Chromium lives, and proving it is there before a pass starts.

macOS deleted ``~/Library/Caches/ms-playwright`` twice. On 2026-09-08 it took the accessibility
pass down mid-run; by 2026-09-23 it was gone again and 28 tests errored at ``chromium.launch()``.
That directory is the cache macOS reclaims under disk pressure, so nothing that lives there is
safe. Two changes, because neither alone is enough:

1. **The binaries live outside the cache.** ``~/.local/share/ms-playwright`` is a plain directory
   in the home folder, not the cache tree macOS empties. That removes the mechanism that actually
   bit us, twice. It is not a guarantee against every possible deletion — nothing is — which is
   why (2) exists.

2. **Every render entry point preflights.** A Playwright version bump asks for a different build
   number, a fresh machine has nothing, and somebody can always delete a folder. All three present
   as the identical crash at ``launch()``, and none is fixed by choosing a better directory. So we
   check first and install if needed, BEFORE a client page is ever loaded.

``PLAYWRIGHT_BROWSERS_PATH`` is read at RUN time as well as at install time — measured: with the
variable unset, ``chromium.launch()`` looks in the cache and raises ``Executable doesn't exist``
however the browsers were installed. So it is exported from here rather than from a shell profile,
which no launchd agent, cron job or other person's terminal would have inherited.

This module is in ``render/``, which ``checks_version`` does not hash, so editing it costs no
re-crawl. That is the same reason the whole rendering layer lives outside ``auditor/checks/``.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Callable

# A plain home directory, not `~/Library/Caches`. Playwright's own docs use `$HOME/pw-browsers`
# for exactly this, so a bare home path is the documented shape rather than an invention of ours.
BROWSERS_DIR = Path.home() / ".local" / "share" / "ms-playwright"

# setdefault, not assignment: a machine that deliberately sets this (CI, a shared install) must
# win. Silently repointing it at a directory that holds nothing would be a new failure, not a fix.
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(BROWSERS_DIR))

# The footgun this design introduces, spelled out wherever a human might copy it: a BARE
# `python3 -m playwright install chromium` now installs into the cache we stopped reading. It
# would look like it worked and change nothing. Every printed command carries the variable.
INSTALL_COMMAND = (f'PLAYWRIGHT_BROWSERS_PATH="{BROWSERS_DIR}" '
                   f'python3 -m playwright install chromium')


def chromium_path() -> Path | None:
    """The Chromium that Playwright would actually launch, or None if it is not on disk.

    Asks Playwright rather than guessing a build number: the directory name carries the build
    (``chromium-1217``) and changes with every upgrade, so a hardcoded path would rot silently.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        candidate = Path(p.chromium.executable_path)
    return candidate if candidate.exists() else None


def _install() -> None:
    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"],
                   check=True, env={**os.environ})


def ensure_chromium(*, probe: Callable[[], Path | None] = chromium_path,
                    installer: Callable[[], None] = _install,
                    log: Callable[[str], None] = print) -> Path:
    """Return the Chromium to launch, installing it first if it is missing.

    Call this before ``sync_playwright()`` in anything that opens a browser. Installing here costs
    ~96 MB once; the alternative is what happened on 2026-09-08 — a pass that had already loaded
    client pages dying at ``launch()`` partway through.
    """
    found = probe()
    if found is not None:
        return found

    log(f"chromium is not at {BROWSERS_DIR} — installing it now (~96 MB, one time).")
    log("macOS purges the old ~/Library/Caches location; this one is outside it.")
    try:
        installer()
    except Exception as exc:                       # noqa: BLE001 - reported, never swallowed
        # A failed download and an empty directory read identically from the outside, and only one
        # of them is fixed by trying again.
        raise RuntimeError(
            f"could not install chromium: {exc}\nRun it by hand:\n  {INSTALL_COMMAND}") from exc

    found = probe()
    if found is None:
        raise RuntimeError(
            "chromium is still missing after the install, so this pass will not start — it would "
            f"otherwise fail at launch() with client pages already loaded.\nRun:\n  "
            f"{INSTALL_COMMAND}")
    log(f"chromium installed at {found}")
    return found
