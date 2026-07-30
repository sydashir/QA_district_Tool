"""Broken-link classification: host-scope + status (external refusal = unverified, internal
refusal = broken), plus content-bug classes (malformed/truncated URLs, staging-host leaks)
flagged WITHOUT a probe. Network faked. The 403 rule is the samhsa/CDC lesson: an external 403
can't be auto-told from a dead link, so surface it (unverified) rather than call it broken or
hide it behind an allowlist.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from auditor import audit
from auditor.checks import links
from auditor.report import Severity

CFG = SimpleNamespace(base_url="https://www.gratitudelodge.com",
                      crawl=SimpleNamespace(max_concurrency=3, delay_seconds=0.0,
                                            timeout_seconds=20.0,
                                            external_link_timeout_seconds=5.0))


class _Resp:
    def __init__(self, code, url):
        self.status_code, self.url = code, url


class _Client:
    def __init__(self, codes):
        self.codes = codes
        self.timeouts: dict[str, float] = {}  # url -> timeout it was probed with

    async def request(self, method, url, headers=None, timeout=None):
        self.timeouts[url] = timeout
        return _Resp(self.codes.get(url, 200), url)


def _run(link, codes):
    p = audit.PageProjection(url="https://www.gratitudelodge.com/p", link_urls=[link])
    return asyncio.run(links.check_links([p], _Client(codes), CFG))


def test_external_403_is_unverified_not_broken():
    ext = "https://www.cdc.gov/adhd"
    f, s = _run(ext, {ext: 403})
    assert s["unverified"] == 1 and s["broken"] == 0
    assert f[0].details["class"] == "unverified_external" and f[0].severity is Severity.INFO


def test_internal_403_is_broken():
    internal = "https://www.gratitudelodge.com/secret"
    f, s = _run(internal, {internal: 403})
    assert s["broken"] == 1 and s["unverified"] == 0 and f[0].severity is Severity.ERROR


def test_external_404_is_broken():
    ext = "https://www.samhsa.gov/gone"  # 404 is "gone", not "refused" -> broken even external
    f, s = _run(ext, {ext: 404})
    assert s["broken"] == 1 and f[0].details["class"] == "broken"


def test_malformed_truncated_url_not_probed():
    f, s = _run("http://gabapen", {})  # truncated paste, no TLD
    assert s["malformed"] == 1 and s["probed"] == 0
    assert f[0].details["class"] == "malformed_link" and f[0].severity is Severity.ERROR


def test_staging_host_flagged_not_probed():
    stg = "https://wordpress-1325235-4846502.cloudwaysapps.com/drug-rehab/x"
    f, s = _run(stg, {})
    assert s["staging"] == 1 and s["probed"] == 0
    assert f[0].details["class"] == "staging_link" and f[0].severity is Severity.ERROR


def test_capped_probe_window_is_stable_and_unbiased():
    # The capped probe window must be (a) the SAME set every run — else a broken link that simply
    # wasn't probed this time reads as `resolved` in the diff — and (b) REPRESENTATIVE, not clustered
    # on one URL prefix. Plain sort()+head satisfied (a) but broke (b): measured on AR it made the
    # 400-link window 71% /city-data/ doorway URLs and the real link signal vanished.
    pages = [audit.PageProjection(
        url="https://www.gratitudelodge.com/p",
        # 90% of the link graph is one prefix (the AR doorway shape), 10% is everything else
        link_urls=[f"https://www.gratitudelodge.com/city-data/c{i:05d}/" for i in range(900)]
                  + [f"https://www.gratitudelodge.com/real/page{i:03d}/" for i in range(100)])]
    client = _Client({})
    _f1, s1 = asyncio.run(links.check_links(pages, client, CFG, max_links=100))
    probed1 = sorted(client.timeouts)
    client2 = _Client({})
    _f2, s2 = asyncio.run(links.check_links(pages, client2, CFG, max_links=100))
    probed2 = sorted(client2.timeouts)

    assert s1["probed"] == s2["probed"] == 100
    assert probed1 == probed2                      # (a) deterministic across runs
    doorway = sum(1 for u in probed1 if "/city-data/" in u)
    real = sum(1 for u in probed1 if "/real/" in u)
    # (b) informative: the budget must NOT be swallowed by the dominant section. A flat random pick
    # would spend ~90 of 100 probes on the duplicated /city-data/ cohort and reach only ~10 real
    # pages (measured on AR: 86% of the window, 2% coverage of the links that matter). Stratified
    # round-robin gives the two sections comparable shares.
    assert real >= 50, f"probe window under-covers the minority section: only {real}/100 real pages"
    assert doorway <= 50, f"dominant section swallowed the budget: {doorway}/100 doorway URLs"


def test_probe_timeout_split_by_host_scope():
    # internal links get the full timeout (a real 404 is a real finding); external hosts get the
    # short one (they classify `unverified` regardless) so the probe tail isn't serialized behind
    # slow gov servers. Was one shared full timeout -> GL's 108min/400-link stall.
    internal = "https://www.gratitudelodge.com/authors/jane"
    external = "https://www.cdc.gov/adhd"
    p = audit.PageProjection(url="https://www.gratitudelodge.com/p",
                             link_urls=[internal, external])
    client = _Client({})
    asyncio.run(links.check_links([p], client, CFG))
    assert client.timeouts[internal] == 20.0   # full page timeout
    assert client.timeouts[external] == 5.0     # short external timeout
