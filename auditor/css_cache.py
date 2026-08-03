"""Per-brand external-stylesheet cache.

``visible_text`` must not contain text the rendered page hides, and a ``display:none`` rule can live
in an EXTERNAL stylesheet where inline-only parsing cannot see it.

Measured across the network, the split is stark: **Gratitude Lodge links ZERO stylesheets** (its CSS
is fully inlined, which is why GL's hidden `.geo-topic` chips were caught from a ``<style>`` block),
but **CAD, COC, AH, AR, MHD and TDRC each link 12+ same-host stylesheets**. For those six brands,
inline-only parsing means hidden content was never detected at all.

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

MAX_SHEETS = 80          # measured: AR links 47 and MHD 49, so 40 truncated real reads. The cap
                         # is a runaway guard; hitting it downgrades to "partial", never a false "ok"
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
        self.missing_sheets: list[str] = []   # 404/410 — a real client defect, not a degraded read

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
        unreadable = 0
        for u in urls:
            try:
                st, _final, body, _err, _hdr = await C._request(client, u, max_retries=max_retries)
            except Exception as e:               # a CSS fetch must never sink the crawl
                _log.warning("stylesheet fetch raised for %s: %s", u, e)
                unreadable += 1
                continue
            if st == 200 and body:
                parts.append(body)
                self.sheets_fetched += 1
                if sum(len(p) for p in parts) > MAX_BYTES:
                    break
            elif st in (404, 410):
                # A MISSING stylesheet is not a degraded read. The browser gets nothing from it
                # either, so it contributes no rules and our knowledge of what is hidden is still
                # complete. (It IS a real client defect — hello-elementor's custom-nav.css 404s on
                # CAD, COC, AR and MHD — but that belongs in the report, not in this status.)
                self.missing_sheets.append(u)
                _log.info("stylesheet missing on the site (%s): %s", st, u)
            else:
                # 5xx, timeout, connection error: the file EXISTS but we could not read it, so we
                # genuinely do not know what it hides.
                unreadable += 1
                _log.warning("stylesheet unreadable (%s): %s", st, u)

        self.css = "\n".join(parts)
        if self.sheets_fetched == 0 and unreadable:
            self.status = "unavailable"
        elif truncated or unreadable:
            self.status = "partial"
        else:
            self.status = "ok"
        _log.info("brand CSS: %d/%d stylesheets, %d bytes, status=%s",
                  self.sheets_fetched, self.sheets_found, len(self.css), self.status)

    @property
    def trustworthy(self) -> bool:
        """True when hidden-content detection had everything it needed."""
        return self.status in ("ok", "none")
