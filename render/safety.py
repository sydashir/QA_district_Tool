"""Network safety for the rendering layer — the precondition for everything else in `render/`.

The HTML-only auditor never ran a line of JavaScript, so it could never touch the client's data.
A browser can. Rendering a page fires Google Tag Manager, Meta's pixel, Microsoft Clarity, VWO's
experiment enrolment and CallTrackingMetrics' number swap — all on LOAD, none of them waiting for a
click. At roughly 2,900 template renders a night that is a standing distortion of the client's
analytics, their A/B test results and their call attribution.

So this module exists to make one guarantee: **a render never reaches a tracker.** Everything else
in the rendering layer is downstream of that.

Two failure modes, and this module is built around the second:

* Blocking too little — the obvious one. Trackers fire, client data is polluted.
* **Blocking too much — the subtle one, and the one that produces bad findings.** Block
  `fonts.gstatic.com` and every text metric on the page is wrong, so the contrast and overflow
  checks report defects that exist only because we broke the page. That is exactly the mistake the
  text layer already made once with `space_before_punct`, where 2,315 "findings" turned out to be
  our own parser inserting a space. Never report a defect your own tooling created.

The lists below are not guessed. They come from reading the actual HTML of GL, RR, COC and CAD
(no JavaScript executed, so nothing fired) and looking at what those pages really reference.
Two things that a guessed list would have got wrong:

* **CTM does not serve from `calltrackingmetrics.com`.** The live sites load `418804.tctm.xyz` —
  an account-numbered subdomain. Hence the pattern, not a hostname.
* **VWO (`dev.visualwebsiteoptimizer.com`) is present and was not on anyone's list.** It enrols
  visitors into live A/B tests. Rendering would have quietly skewed real experiments.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

# --------------------------------------------------------------------------- deny
# Substring/pattern match on the request HOST. Kept as patterns rather than exact hosts because
# several of these are account-scoped subdomains that differ per brand.
TRACKER_PATTERNS: tuple[str, ...] = (
    # analytics + tag management
    r"googletagmanager\.com", r"google-analytics\.com", r"analytics\.google\.com",
    r"\bgtag\b", r"doubleclick\.net", r"googleadservices\.com", r"googlesyndication\.com",
    # session recording / heatmaps
    r"clarity\.ms", r"hotjar\.com", r"mouseflow\.com", r"crazyegg\.com", r"fullstory\.com",
    r"logrocket", r"smartlook", r"luckyorange",
    # A/B testing — enrolling a fake visitor corrupts a live experiment, not just a report
    r"visualwebsiteoptimizer\.com", r"optimizely\.com", r"vwo\.com",
    # social + ad pixels
    r"connect\.facebook\.net", r"facebook\.com/tr", r"px\.ads\.linkedin\.com",
    r"analytics\.tiktok\.com", r"ct\.pinterest\.com", r"bat\.bing\.com",
    r"analytics\.twitter\.com", r"static\.ads-twitter\.com", r"snap\.licdn\.com",
    r"criteo\.", r"taboola\.", r"outbrain\.",
    # marketing automation / chat identity
    r"hubspot\.com", r"hs-scripts\.com", r"marketo\.net", r"drift\.com", r"intercom\.io",
    # CALL TRACKING — the account-numbered subdomain is why this is a pattern
    r"tctm\.xyz", r"tctm\.co", r"calltrackingmetrics\.com", r"calltrk\.com", r"callrail\.com",
)

# --------------------------------------------------------------------------- never deny
# These must load or the rendered page is not the page a visitor sees, and every visual check
# becomes a measurement of our own blocking. Checked BEFORE the deny list.
#
#   fonts        -> text metrics. Without them, line boxes shift and overflow/clipping is fiction.
#   maps, video  -> real visible content; a blocked map is a blank box we would report as broken.
#   widgets      -> review badges and embedded forms are content the client can see and we must too.
NEVER_BLOCK: tuple[str, ...] = (
    r"fonts\.googleapis\.com", r"fonts\.gstatic\.com", r"use\.typekit\.net", r"fonts\.net",
    r"maps\.googleapis\.com", r"maps\.google\.com", r"maps\.gstatic\.com",
    r"youtube\.com", r"youtube-nocookie\.com", r"ytimg\.com", r"vimeo\.com", r"vimeocdn\.com",
    r"cdn\.jsdelivr\.net", r"cdnjs\.cloudflare\.com", r"unpkg\.com",
    r"cdn\.trustindex\.io", r"legitscript\.com", r"jointcommission\.org",
    r"form\.jotform\.com", r"gmpg\.org",
)

_DENY = tuple(re.compile(p, re.I) for p in TRACKER_PATTERNS)
_ALLOW = tuple(re.compile(p, re.I) for p in NEVER_BLOCK)


class SafetyNotArmed(RuntimeError):
    """The render layer refused to run because its network guard was not in place."""


def classify(url: str, first_party: str | None = None) -> str:
    """"allow" | "block" | "third-party" for one request URL.

    `first_party` is the site being audited; its own hosts are always allowed.
    """
    host = (urlparse(url).netloc or "").lower()
    if not host:
        return "allow"                     # data:, blob:, about:
    if first_party and _same_site(host, first_party):
        return "allow"
    for rx in _ALLOW:
        if rx.search(host):
            return "allow"                 # checked FIRST: allow beats deny, deliberately
    for rx in _DENY:
        if rx.search(url):                 # full URL — `facebook.com/tr` is a path, not a host
            return "block"
    return "third-party"                   # unknown third party: allowed, but recorded


def _same_site(host: str, first_party: str) -> bool:
    fp = (urlparse(first_party).netloc or first_party).lower()
    fp = fp.split(":")[0]
    reg = ".".join(fp.split(".")[-2:]) if fp.count(".") >= 1 else fp
    return host == fp or host.endswith("." + reg) or host == reg


@dataclass
class SafetyLedger:
    """What the guard actually did, per run. Configured is not the same as working."""

    blocked: int = 0
    allowed: int = 0
    blocked_hosts: dict[str, int] = field(default_factory=dict)
    # Every third party we did NOT block. This is how a new tracker gets discovered instead of
    # being silently leaked to — the client's marketing stack changes without telling us.
    unblocked_third_parties: dict[str, int] = field(default_factory=dict)
    pages: int = 0

    def record(self, url: str, verdict: str) -> None:
        host = (urlparse(url).netloc or "?").lower()
        if verdict == "block":
            self.blocked += 1
            self.blocked_hosts[host] = self.blocked_hosts.get(host, 0) + 1
        else:
            self.allowed += 1
            if verdict == "third-party":
                self.unblocked_third_parties[host] = self.unblocked_third_parties.get(host, 0) + 1

    def assert_worked(self, *, min_pages: int = 1) -> None:
        """Raise unless the guard demonstrably did something. Called at the END of every run.

        A deny-list that is configured but not attached looks exactly like a set of pages with no
        trackers on them, and the difference is invisible in the output. These sites demonstrably
        load GTM, Clarity, Meta and CTM, so a real run that blocked NOTHING did not work — and the
        cost of believing it is polluting the client's analytics for as long as nobody notices.
        """
        if self.pages < min_pages:
            raise SafetyNotArmed(
                f"network guard saw {self.pages} page(s); expected at least {min_pages}")
        if self.blocked == 0:
            raise SafetyNotArmed(
                f"network guard blocked NOTHING across {self.pages} page(s). These sites are known "
                f"to load Google Tag Manager, Microsoft Clarity, Meta and CallTrackingMetrics, so "
                f"zero blocks means the guard was not attached — not that the pages were clean. "
                f"Refusing to report results from an unguarded run.")

    def summary(self) -> str:
        top = sorted(self.blocked_hosts.items(), key=lambda kv: -kv[1])[:5]
        return (f"{self.pages} pages: blocked {self.blocked} tracker requests "
                f"({', '.join(h for h, _ in top)}), allowed {self.allowed}, "
                f"{len(self.unblocked_third_parties)} unrecognised third-party host(s)")


def install(page, first_party: str, ledger: SafetyLedger):
    """Attach the guard to a Playwright page. MUST be called before the first navigation.

    Aborts with `blockedbyclient`, which is what a content blocker reports, so a site that notices
    sees an ordinary ad-blocker rather than something anomalous.
    """
    def handler(route):
        url = route.request.url
        verdict = classify(url, first_party)
        ledger.record(url, verdict)
        if verdict == "block":
            route.abort("blockedbyclient")
        else:
            route.continue_()

    page.route("**/*", handler)
    return ledger
