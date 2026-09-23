"""Where the browser lives, and proving it is there before a pass touches a client page.

macOS deleted `~/Library/Caches/ms-playwright` twice: on 2026-09-08 it took the accessibility pass
down mid-run, and by 2026-09-23 it was gone again with 28 tests erroring at `chromium.launch()`.
That directory is the cache macOS reclaims, so the binaries are installed OUT of it — and because
Playwright reads `PLAYWRIGHT_BROWSERS_PATH` at RUN time as well as at install time (verified: with
the variable unset it looks in the cache and fails), the path is exported from code rather than
from a shell profile, which no launchd agent or cron job would have inherited.

The preflight is the belt to that braces: a version bump, a fresh machine or a hand-deleted folder
all present as the same crash, and none of them is fixed by choosing a better directory.
"""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from render import browser as br


# --------------------------------------------------------------------------- where it lives
def test_the_browsers_live_outside_the_directory_macos_reclaims():
    """The whole point. `~/Library/Caches` is the cache macOS is free to empty under disk
    pressure, and it emptied this one twice."""
    cache = Path.home() / "Library" / "Caches"
    assert cache not in br.BROWSERS_DIR.parents
    assert "Caches" not in br.BROWSERS_DIR.parts


def test_the_path_is_exported_so_playwright_finds_it_at_run_time(monkeypatch):
    """Measured: with PLAYWRIGHT_BROWSERS_PATH unset, `chromium.launch()` looks in the cache and
    raises `Executable doesn't exist`. Installing elsewhere without exporting it at run time moves
    the failure, it does not fix it."""
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    reloaded = importlib.reload(br)
    import os
    assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == str(reloaded.BROWSERS_DIR)


def test_an_environment_that_already_chose_a_path_is_left_alone(monkeypatch):
    """A machine that deliberately sets this (CI, a shared install) must win over our default —
    otherwise importing us silently repoints it at a directory that holds nothing."""
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "/somewhere/else")
    importlib.reload(br)
    import os
    assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == "/somewhere/else"
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    importlib.reload(br)


def test_the_install_command_carries_the_path(monkeypatch):
    """The footgun this design introduces: a bare `playwright install chromium` now installs to the
    cache we deliberately stopped reading, so it would look like it worked and change nothing.
    Every printed command must carry the variable."""
    assert "PLAYWRIGHT_BROWSERS_PATH" in br.INSTALL_COMMAND
    assert str(br.BROWSERS_DIR) in br.INSTALL_COMMAND


# --------------------------------------------------------------------------- the preflight
def test_a_browser_that_is_there_is_not_reinstalled():
    calls = []
    got = br.ensure_chromium(probe=lambda: Path("/b/chrome"),
                             installer=lambda: calls.append(1), log=lambda m: None)
    assert got == Path("/b/chrome")
    assert calls == []


def test_a_missing_browser_is_installed_and_the_pass_continues():
    """The behaviour asked for: reinstall before the pass, rather than crash at launch() partway
    through a run that has already loaded client pages."""
    seen = iter([None, Path("/b/chrome")])
    calls = []
    got = br.ensure_chromium(probe=lambda: next(seen),
                             installer=lambda: calls.append(1), log=lambda m: None)
    assert got == Path("/b/chrome")
    assert calls == [1]


def test_a_browser_still_missing_after_the_install_refuses_and_names_the_command():
    """Never proceed on a hope. A pass that carries on here launches against a client site with no
    browser and dies later, which is the failure we are removing."""
    with pytest.raises(RuntimeError) as e:
        br.ensure_chromium(probe=lambda: None, installer=lambda: None, log=lambda m: None)
    assert "PLAYWRIGHT_BROWSERS_PATH" in str(e.value)


def test_an_install_that_fails_says_so_rather_than_reporting_a_missing_browser():
    """A network failure and an empty directory are different problems and read identically if the
    installer's own error is swallowed."""
    def boom():
        raise OSError("network is down")

    with pytest.raises(RuntimeError) as e:
        br.ensure_chromium(probe=lambda: None, installer=boom, log=lambda m: None)
    assert "network is down" in str(e.value)


def test_the_preflight_announces_itself_when_it_installs():
    """A silent 96 MB download inside a pass looks like a hang."""
    said: list[str] = []
    seen = iter([None, Path("/b/chrome")])
    br.ensure_chromium(probe=lambda: next(seen), installer=lambda: None, log=said.append)
    assert said, "an install that prints nothing is indistinguishable from a stall"


# --------------------------------------------------------------------------- the wiring
def test_every_module_that_launches_a_browser_preflights_first():
    """The invariant that survives the next pass somebody writes. Same shape as the collapse-
    component wiring test: adding a launch without the guard must fail here, not in production
    against a client's site."""
    root = Path(__file__).resolve().parent.parent
    offenders = []
    for path in sorted([*(root / "render").glob("*.py"), *(root / "scripts").glob("*.py")]):
        source = path.read_text(encoding="utf-8")
        if "def ensure_chromium(" in source:
            continue        # the module that DEFINES the preflight; it describes launch(), never calls it
        # The CALL, not the name: the first version of this test looked for "ensure_chromium",
        # which the `from render.browser import ensure_chromium` line satisfies on its own. A
        # mutation that deleted the call and left the import sailed straight through it.
        if "chromium.launch(" in source and "ensure_chromium()" not in source:
            offenders.append(path.name)
    assert not offenders, f"these launch a browser with no preflight: {offenders}"
