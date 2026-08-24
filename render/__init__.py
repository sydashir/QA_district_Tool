"""Rendering layer — browser-based checks the HTML-only auditor structurally cannot do.

DELIBERATELY OUTSIDE `auditor/checks/`. `auditor/checks_version.py` hashes every file in that
directory, so a module placed there invalidates all nine brands' resume caches on every edit. This
package will be iterated on constantly and has nothing to do with the text audit's output, so it
lives at the top level and only its FINDINGS flow into the shared pipeline. See CLAUDE.md.

Nothing here runs against a client's production site until they have been told it will. That is
recorded in docs/plans/2026-08-25-rendering-layer-design.md §10.4, and it is not a technical gate —
it is theirs to agree to.
"""
