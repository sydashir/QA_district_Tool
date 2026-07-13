"""Check modules — one per check. Populated in M1.

Planned (CLAUDE.md §5, v1 deterministic):
  links.py      broken links (dedup + HEAD/GET status classification)
  blank.py      blank/thin pages & empty ACF sections
  phone.py      phone display/tel mismatch vs brand canonical numbers
  structure.py  heading outline + leftover [acf field=...] / empty-slot artifacts
  meta.py       meta title/description/slug basics

Each check consumes an ``auditor.parse.ParsedPage`` and emits
``auditor.report.Finding`` objects. Nothing here is implemented in M0.
"""
