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
# The canary is a deny-list ENTRY, not a special case in the guard. That is the point: a probe
# that the guard has to reason about differently from a real tracker proves less than one that
# takes the identical code path. `.invalid` is reserved by RFC 2606 and can never resolve, so this
# pattern cannot match a host that really exists.
CANARY_HOST = "canary.invalid"

TRACKER_PATTERNS: tuple[str, ...] = (
    r"canary\.invalid",
    # analytics + tag management
    # cloudflareinsights was NOT on this list and got through on the first production render. It
    # was caught only because unrecognised third parties are LOGGED rather than silently allowed —
    # which is the whole reason that mechanism exists.
    r"cloudflareinsights\.com", r"static\.cloudflareinsights",
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
    # Exact URLs we aborted. `images.find_broken` needs these to avoid reporting an image that WE
    # stopped as an image the client broke. Kept on the ledger rather than in a set the caller
    # maintains alongside it, because a caller who forgets the set gets false broken-image findings
    # and no error — the failure is silent, so the data structure has to make it hard to miss.
    blocked_urls: set = field(default_factory=set)
    pages: int = 0

    def record(self, url: str, verdict: str) -> None:
        host = (urlparse(url).netloc or "?").lower()
        if verdict == "block":
            self.blocked += 1
            self.blocked_urls.add(url)
            self.blocked_hosts[host] = self.blocked_hosts.get(host, 0) + 1
        else:
            self.allowed += 1
            if verdict == "third-party":
                self.unblocked_third_parties[host] = self.unblocked_third_parties.get(host, 0) + 1

    def assert_worked(self, *, min_pages: int = 1) -> None:
        """Confirm pages were actually seen. Attachment is proven separately — see below.

        This USED to assert on the BLOCK COUNT, and that was wrong twice, both times failing a run
        that was working perfectly:

        * DBH's headless rebuild references no external assets at all, so it blocks zero.
        * The FIRST production render — GL's homepage — blocked zero because **the trackers are
          consent-gated**. 81 requests, all first-party bar two, and no GTM, Clarity, Meta or CTM
          anywhere, because we never accept a cookie banner. That is excellent news for the client
          and it makes a block count useless as a health signal.

        So a count of zero blocks is now INFORMATION, not an error.
        """
        if self.pages < min_pages:
            raise SafetyNotArmed(
                f"network guard saw {self.pages} page(s); expected at least {min_pages}")

    def assert_attached(self, canary_host: str = "canary.invalid") -> None:
        """Prove the route handler is live, by confirming a deliberate canary request was blocked.

        Attachment is what actually matters, and unlike a block count it holds regardless of
        whether the page under test loads any trackers of its own. Call this BEFORE navigating to
        a client page.
        """
        if not any(canary_host in h for h in self.blocked_hosts):
            raise SafetyNotArmed(
                "the network guard did not block a deliberate canary request, so it is not "
                "attached. Refusing to render: an unguarded render fires the client's analytics, "
                "A/B tests and call tracking.")

    def summary(self) -> str:
        top = sorted(self.blocked_hosts.items(), key=lambda kv: -kv[1])[:5]
        return (f"{self.pages} pages: blocked {self.blocked} tracker requests "
                f"({', '.join(h for h, _ in top)}), allowed {self.allowed}, "
                f"{len(self.unblocked_third_parties)} unrecognised third-party host(s)")


def prove_attached(page, ledger: SafetyLedger, *, timeout_ms: int = 2000) -> None:
    """Fire a request the deny list must block, then assert it was. Call BEFORE the client page.

    The canary is a request to a host that cannot exist, taking the same route handler, the same
    `classify`, and the same `abort` as a real tracker would. If it comes back blocked, the guard
    is attached; if it does not, we have proof to refuse on rather than a hopeful assumption.

    The canary is then REMOVED from the ledger, so the run's reported numbers describe the client's
    page and not our own probe.
    """
    page.set_content(f'<img src="https://{CANARY_HOST}/probe.gif">', wait_until="domcontentloaded")
    waited = 0
    while CANARY_HOST not in ledger.blocked_hosts and waited < timeout_ms:
        page.wait_for_timeout(50)
        waited += 50
    ledger.assert_attached(CANARY_HOST)
    ledger.blocked -= ledger.blocked_hosts.pop(CANARY_HOST, 0)


def install(page, first_party: str, ledger: SafetyLedger):
    """Attach the guard to a Playwright page. MUST be called before the first navigation.

    Aborts with `blockedbyclient`, which is what a content blocker reports, so a site that notices
    sees an ordinary ad-blocker rather than something anomalous.
    """
    def handler(route):                # EXACTLY one parameter: Playwright passes (route, request)
        url = route.request.url        # to any handler that will accept two, which corrupts args.
        verdict = classify(url, first_party)
        ledger.record(url, verdict)
        if verdict == "block":
            route.abort("blockedbyclient")
        else:
            route.continue_()

    page.route("**/*", handler)
    return ledger
