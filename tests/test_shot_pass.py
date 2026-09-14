"""The pass that photographs findings — the three properties it must not lose.

Written last, and named as a gap before it was closed: `shot_pass.py` shipped with its selection
path covered only indirectly through `client_report`'s tests. The three below are the ones that
would fail silently and look fine from the outside.

1. It photographs ONLY what the report will show. The first version shot anything carrying a
   selector; on GL that wrote 61 pictures of which the report displayed 0, and nothing anywhere
   said so.
2. A page that will not load leaves an EXPLAINED absence on its findings, not a blank. The report
   renders that sentence; a blank would read as "no evidence".
3. MHD is never touched. Its origin 503s under concurrency and run 130 was refused for reaching
   37.1% of its pages; a render costs far more requests than a text fetch.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.models import Base, Brand, Finding, Run          # noqa: E402

shot_pass = pytest.importorskip("scripts.shot_pass",
                                reason="host-only: needs playwright and render/")


@pytest.fixture()
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False, future=True)() as s:
        yield s
    engine.dispose()


def _brand_with_findings(session, code="GL", n=4):
    b = Brand(code=code, name=code, base_url="https://gl.test")
    session.add(b)
    session.flush()
    r = Run(brand_id=b.id, status="ok", pages_audited=10)
    session.add(r)
    session.flush()
    for i in range(n):
        session.add(Finding(
            brand_id=b.id, run_id=r.id, fingerprint=f"phone:display_dial_mismatch:u{i}",
            fingerprint_hash=f"h{i}", url=f"https://gl.test/p{i}", check="phone",
            severity="error", issue="displayed number differs from the click-to-call target",
            status="persisting", details={"selector": "a.tel", "class": "display_dial_mismatch"}))
    session.commit()
    return b, r


# ---------------------------------------------------------------- 1. the report decides

def test_only_the_findings_the_report_shows_are_photographed(session, monkeypatch):
    """THE REPORT DECIDES; THIS PASS FOLLOWS. Four findings exist, the report shows one."""
    _b, run = _brand_with_findings(session, n=4)
    monkeypatch.setattr(shot_pass, "selected_fingerprints",
                        lambda s, code: ["phone:display_dial_mismatch:u2"])

    got = shot_pass.targets(session, "GL", run)
    picked = [f.fingerprint for fs in got.values() for f in fs]
    assert picked == ["phone:display_dial_mismatch:u2"], (
        f"photographed something the client will never see: {picked}")


def test_nothing_is_photographed_when_the_report_shows_nothing(session, monkeypatch):
    """An empty selection must mean an empty target list, never 'fall back to everything'."""
    _b, run = _brand_with_findings(session, n=4)
    monkeypatch.setattr(shot_pass, "selected_fingerprints", lambda s, code: [])
    assert shot_pass.targets(session, "GL", run) == {}


def test_a_selected_finding_with_no_element_is_skipped(session, monkeypatch):
    """A missing meta description is selected by the report and has nowhere to point a camera."""
    b, run = _brand_with_findings(session, n=1)
    session.add(Finding(brand_id=b.id, run_id=run.id, fingerprint="meta:missing_desc:u9",
                        fingerprint_hash="h9", url="https://gl.test/p9", check="meta",
                        severity="warning", issue="missing meta description",
                        status="persisting", details={}))
    session.commit()
    monkeypatch.setattr(shot_pass, "selected_fingerprints",
                        lambda s, code: ["phone:display_dial_mismatch:u0", "meta:missing_desc:u9"])
    got = shot_pass.targets(session, "GL", run)
    picked = [f.fingerprint for fs in got.values() for f in fs]
    assert picked == ["phone:display_dial_mismatch:u0"]


# ---------------------------------------------------- 2. a page that will not load is explained

class _DeadPage:
    def goto(self, *a, **k):
        raise RuntimeError("net::ERR_CONNECTION_TIMED_OUT")

    def wait_for_timeout(self, *a, **k):
        pass

    def close(self):
        pass


class _Browser:
    def new_page(self, **k):
        return _DeadPage()

    def close(self):
        pass


def test_a_page_that_will_not_load_leaves_an_explained_absence(session, monkeypatch):
    """Silence here would be indistinguishable from an element that could not be found, and the
    report would have nothing to print — so the reader sees a finding with no picture and no
    reason, which invites them to read it as no evidence."""
    _b, run = _brand_with_findings(session, n=2)
    monkeypatch.setattr(shot_pass, "selected_fingerprints",
                        lambda s, code: ["phone:display_dial_mismatch:u0",
                                         "phone:display_dial_mismatch:u1"])
    monkeypatch.setattr(shot_pass, "install", lambda *a, **k: None)
    monkeypatch.setattr(shot_pass, "prove_attached", lambda *a, **k: None)
    monkeypatch.setattr(shot_pass.time, "sleep", lambda *_a: None)

    from render.shots import ShotTally
    got = shot_pass.run_brand(session, _Browser(), "GL", max_pages=10, dry_run=False,
                              tally=ShotTally())

    assert got["shots"] == 0
    rows = session.query(Finding).all()
    assert rows, "fixture produced nothing"
    for f in rows:
        assert (f.details or {}).get("shot_absent") == "none", (
            f"a page that never loaded left no explanation on {f.fingerprint}")


# ------------------------------------------------------------------------- 3. never MHD

def test_mhd_is_excluded_even_when_named_directly(monkeypatch, capsys):
    """Not a default that can be overridden — MHD's origin refused run 130 at 37.1% of its pages,
    and rendering costs far more requests than the text fetch that already overwhelmed it."""
    monkeypatch.setattr(sys, "argv", ["shot_pass.py", "mhd"])
    with pytest.raises(SystemExit) as e:
        shot_pass.main()
    assert "MHD" in str(e.value)


def test_mhd_is_excluded_from_all(session, monkeypatch):
    """`--all` enumerates brands from the database; MHD must drop out of that list."""
    for code in ("GL", "MHD", "RR"):
        session.add(Brand(code=code, name=code, base_url=f"https://{code.lower()}.test"))
    session.commit()

    seen: list[str] = []

    class _Sess:
        def __enter__(self): return session
        def __exit__(self, *a): return False
        def scalars(self, q): return session.scalars(q)

    monkeypatch.setattr(shot_pass, "SessionLocal", _Sess)
    monkeypatch.setattr(shot_pass, "sync_playwright", _fake_playwright)
    monkeypatch.setattr(shot_pass, "run_brand",
                        lambda s, b, code, mp, dr, t: seen.append(code) or
                        {"pages": 0, "shots": 0, "absent": 0})
    monkeypatch.setattr(sys, "argv", ["shot_pass.py", "--all"])

    shot_pass.main()
    assert "MHD" not in seen, "the one brand that must never be rendered was rendered"
    assert seen == ["GL", "RR"]


def _fake_playwright():
    class _P:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        @property
        def chromium(self):
            class _C:
                def launch(self_inner): return _Browser()
            return _C()
    return _P()


def test_mhd_is_named_in_the_skip_set():
    """Pinned as data, so removing the guard cannot look like a refactor."""
    assert "MHD" in shot_pass.SKIP


# ------------------------------------------------ 4. the network guard is proven and REPORTED

class _Route:
    def __init__(self, url):
        self.request = type("Req", (), {"url": url})()
        self.aborted = None

    def abort(self, reason):
        self.aborted = reason

    def continue_(self):
        pass


class _GuardedPage:
    """Drives the REAL render.safety guard: every request goes through the handler it installs."""
    SUBRESOURCES = ["https://gl.test/style.css", "https://www.googletagmanager.com/gtm.js",
                    "https://cdn.unknown-widget.test/w.js", "https://gl.test/logo.png"]
    loaded: list = []

    def __init__(self, attach=True):
        self.handler = None
        self.attach = attach

    def route(self, _pattern, handler):
        if self.attach:
            self.handler = handler

    def set_content(self, html, **_k):
        import re
        if self.handler:
            self.handler(_Route(re.search(r'src="([^"]+)"', html).group(1)))

    def wait_for_timeout(self, *_a, **_k):
        pass

    def goto(self, url, **_k):
        _GuardedPage.loaded.append(url)
        for u in [url] + self.SUBRESOURCES:
            self.handler(_Route(u))

    def close(self):
        pass


def test_the_guard_totals_are_reported_per_brand(session, monkeypatch, capsys):
    """Configured is not the same as working, and working that nobody reports is not evidence.
    The first version built a ledger for every page and printed none of it."""
    _b, _run = _brand_with_findings(session, n=2)
    monkeypatch.setattr(shot_pass, "selected_fingerprints",
                        lambda s, code: ["phone:display_dial_mismatch:u0",
                                         "phone:display_dial_mismatch:u1"])
    monkeypatch.setattr(shot_pass, "attach_shots", lambda *a, **k: 0)
    monkeypatch.setattr(shot_pass.time, "sleep", lambda *_a: None)
    monkeypatch.setattr(shot_pass, "load_brand",
                        lambda code: type("Cfg", (), {"base_url": "https://gl.test"})())

    class _B:
        def new_page(self, **_k):
            return _GuardedPage()

    from render.shots import ShotTally
    got = shot_pass.run_brand(session, _B(), "GL", max_pages=10, dry_run=True, tally=ShotTally())
    guard = got["guard"]
    assert guard["canary_pages"] == 2 and guard["pages"] == 2
    assert guard["blocked"] == 2 and guard["blocked_hosts"] == {"www.googletagmanager.com": 2}
    assert guard["allowed"] == 8
    assert guard["unrecognised"] == {"cdn.unknown-widget.test": 2}
    out = capsys.readouterr().out
    assert "canary blocked on 2 of 2 pages" in out
    assert "blocked 2" in out and "allowed 8" in out and "cdn.unknown-widget.test" in out


def test_an_unproven_guard_stops_the_pass_before_any_client_page_loads(session, monkeypatch):
    """If the canary is not blocked, the guard is not attached. The old code caught that like any
    other page error, printed "page did not load", and moved on to the next client page."""
    _b, _run = _brand_with_findings(session, n=2)
    monkeypatch.setattr(shot_pass, "selected_fingerprints",
                        lambda s, code: ["phone:display_dial_mismatch:u0",
                                         "phone:display_dial_mismatch:u1"])
    monkeypatch.setattr(shot_pass.time, "sleep", lambda *_a: None)
    monkeypatch.setattr(shot_pass, "load_brand",
                        lambda code: type("Cfg", (), {"base_url": "https://gl.test"})())
    _GuardedPage.loaded = []

    class _B:
        def new_page(self, **_k):
            return _GuardedPage(attach=False)

    from render.safety import SafetyNotArmed
    from render.shots import ShotTally
    with pytest.raises(SafetyNotArmed):
        shot_pass.run_brand(session, _B(), "GL", max_pages=10, dry_run=True, tally=ShotTally())
    assert _GuardedPage.loaded == [], f"client pages loaded without a proven guard: {_GuardedPage.loaded}"
