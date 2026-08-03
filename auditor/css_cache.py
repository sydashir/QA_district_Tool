"""Per-brand external-stylesheet cache.

``visible_text`` must not contain text the rendered page hides. Most ``display:none`` rules that
matter live in an EXTERNAL stylesheet, not an inline ``<style>``: live Gratitude Lodge ships

    <a href="…">Partial hospitalization program (PHP)<span>Costa Mesa, CA</span></a>

whose span is hidden by a rule in a linked CSS file. Verified in a browser: the span's computed
display is ``none`` and the whole chip's ``innerText`` is EMPTY — the link renders nothing at all.
Reading only inline styles left that text in ``visible_text``, and the AI layer then reported a
"missing space" defect for a string no reader can see.

ONE FETCH PER BRAND, not per page: a WordPress theme serves the same stylesheets on every URL, so
the first page's ``<link rel=stylesheet>`` set is the brand's CSS. At ~31k pages the difference is
a handful of requests versus tens of thousands.

FAILURE IS RECORDED, NEVER SWALLOWED. If a stylesheet cannot be fetched we do not quietly fall back
to inline-only and keep reporting findings as though extraction were clean — that is the same trap
as emitting coverage findings from a partial sitemap read. The status travels with every parsed
page so downstream findings can be marked lower-confidence.
"""
from __future__ import annotations

import logging
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

_log = logging.getLogger(__name__)

MAX_SHEETS = 40          # WordPress themes routinely link 20+; the cap is a runaway guard,
                         # and hitting it downgrades status to "partial" rather than lying "ok"
MAX_BYTES = 3_000_000    # a runaway CSS bundle must not blow up memory on every page parse


class BrandCSS:
    """Stylesheet text for one brand, fetched once.

    ``status`` is one of:
      ``none``        — no same-host stylesheets were linked (nothing to miss)
      ``ok``          — every discovered stylesheet was fetched
      ``partial``     — some fetched, some failed: hidden-content rules may be incomplete
      ``unavailable`` — stylesheets were discovered but none could be fetched
    """

    def __init__(self) -> None:
        self.css: str = ""
        self.status: str = "none"
        self._loaded = False
        self.sheets_found = 0
        self.sheets_fetched = 0

    @staticmethod
    def _sheet_urls(html: str, page_url: str) -> list[str]:
        soup = BeautifulSoup(html, "lxml")
        host = urlparse(page_url).netloc
        out: list[str] = []
        for link in soup.find_all("link", href=True):
            rel = " ".join(link.get("rel") or []).lower()
            if "stylesheet" not in rel:
                continue
            url = urljoin(page_url, link["href"])
            # same host only: a third-party CDN font sheet cannot hide this site's content, and
            # fetching arbitrary external hosts is not something a crawl should start doing.
            if urlparse(url).netloc == host and url not in out:
                out.append(url)
        return out

    async def load(self, client, html: str, page_url: str, max_retries: int = 2) -> None:
        """Populate from the first page's <link rel=stylesheet> set. Idempotent."""
        if self._loaded:
            return
        self._loaded = True

        from . import crawl as C  # local import: crawl imports parse, parse must not import crawl

        urls = self._sheet_urls(html, page_url)
        self.sheets_found = len(urls)
        # A CAP THAT SILENTLY TRUNCATES WOULD REPORT "ok" ON AN INCOMPLETE READ — the same lie as
        # a partial sitemap producing confident coverage findings. Truncation is recorded instead.
        truncated = len(urls) > MAX_SHEETS
        urls = urls[:MAX_SHEETS]
        if not urls:
            self.status = "none"
            return

        parts: list[str] = []
        for u in urls:
            try:
                st, _final, body, _err, _hdr = await C._request(client, u, max_retries=max_retries)
            except Exception as e:               # a CSS fetch must never sink the crawl
                _log.warning("stylesheet fetch raised for %s: %s", u, e)
                continue
            if st == 200 and body:
                parts.append(body)
                self.sheets_fetched += 1
                if sum(len(p) for p in parts) > MAX_BYTES:
                    break
            else:
                _log.warning("stylesheet unavailable (%s): %s", st, u)

        self.css = "\n".join(parts)
        if self.sheets_fetched == 0:
            self.status = "unavailable"
        elif truncated or self.sheets_fetched < len(urls):
            self.status = "partial"
        else:
            self.status = "ok"
        _log.info("brand CSS: %d/%d stylesheets, %d bytes, status=%s",
                  self.sheets_fetched, self.sheets_found, len(self.css), self.status)

    @property
    def trustworthy(self) -> bool:
        """True when hidden-content detection had everything it needed."""
        return self.status in ("ok", "none")
