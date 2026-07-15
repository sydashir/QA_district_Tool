"""P5: the page cache hash must be stable across Cloudflare cfemail rotation.

Raw-HTML hashing marks every page "changed" every run because Cloudflare rotates
data-cfemail / /cdn-cgi/l/email-protection hex per request (the M0 finding). The
cache/diff must hash the normalized markup instead.
"""
from __future__ import annotations

from auditor import crawl


# Same page, two fetches — differ ONLY in Cloudflare's rotating email obfuscation.
_A = ('<html><body><h1>Hi</h1><p>real body</p>'
      '<a href="/x" data-cfemail="111111">e</a>'
      '<a href="/cdn-cgi/l/email-protection#aaaaaa">m</a></body></html>')
_B = ('<html><body><h1>Hi</h1><p>real body</p>'
      '<a href="/x" data-cfemail="222222">e</a>'
      '<a href="/cdn-cgi/l/email-protection#bbbbbb">m</a></body></html>')
# A genuine content change.
_C = ('<html><body><h1>Hi</h1><p>DIFFERENT body</p>'
      '<a href="/x" data-cfemail="111111">e</a>'
      '<a href="/cdn-cgi/l/email-protection#aaaaaa">m</a></body></html>')


def test_page_hash_ignores_cfemail_rotation():
    assert crawl.page_hash(_A) == crawl.page_hash(_B)
    # and the raw hash would NOT be stable (proves the fix is doing work)
    assert crawl.content_hash(_A) != crawl.content_hash(_B)


def test_page_hash_detects_real_change():
    assert crawl.page_hash(_A) != crawl.page_hash(_C)
