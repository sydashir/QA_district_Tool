"""District Site Auditor — crawls live WordPress sites and reports content-QA issues.

Package layout (see CLAUDE.md §4). M0 delivers crawl + config only:
  auditor/config.py   pydantic brand-config models + TOML loader           (M0)
  auditor/crawl.py    sitemap + WP-REST enumeration, async fetch, cache     (M0)
  auditor/parse.py    HTML -> links / headings / text primitives (bs4)      (M0 scaffold, M1 consumes)
  auditor/report.py   result models (M1 emits) + CSV/JSON writers (M2)
  auditor/checks/     one module per check                                  (M1)
  auditor/cli.py      Typer CLI                                             (M0)
"""

__version__ = "0.0.0"  # M0 — crawl layer only
