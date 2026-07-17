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
                      crawl=SimpleNamespace(max_concurrency=3, delay_seconds=0.0))


class _Resp:
    def __init__(self, code, url):
        self.status_code, self.url = code, url


class _Client:
    def __init__(self, codes):
        self.codes = codes

    async def request(self, method, url, headers=None):
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
