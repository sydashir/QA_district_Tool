"""Every collapsed check must be version-scoped to audit.py.

The collapses in `audit.py` MINT the fingerprint of the findings they merge. If a check is
collapsed but not mapped to `src:audit.py`, its merged rows vanish from the diff and are reported
as **resolved** — i.e. the sheet tells the QA team a defect was fixed when nothing was touched.

This is not hypothetical. `empty_slot`, `misspelling` and `scope` were added to the collapse and
this mapping was not updated, so a live republish told the client **"171 fixed"** on AH when the
truth was 169 `county_for_country` rows merged into one. GL (4,002 `empty_slot`) and MHD (1,056
`misspelling`) would have claimed thousands.
"""
from __future__ import annotations

from auditor import audit, diff


def test_every_collapsed_check_is_scoped_to_audit_py():
    missing = sorted(c for c in audit._TEMPLATE_COLLAPSE_CHECKS
                     if "src:audit.py" not in diff._CHECK_COMPONENT.get(c, set()))
    assert not missing, (
        f"{missing} are collapsed in audit.py but not mapped to src:audit.py in "
        f"diff._CHECK_COMPONENT — their collapsed rows will read as 'fixed' to the client")


def test_the_other_collapses_are_scoped_too():
    """`phone` and `heading_structure` have their own dedicated collapses."""
    for check in ("phone", "heading_structure"):
        assert "src:audit.py" in diff._CHECK_COMPONENT[check], check


def test_a_check_audit_py_does_not_touch_is_left_alone():
    """The scoping must stay narrow — an unrelated orchestration edit should not rule-change
    every check in the repo."""
    assert "src:audit.py" not in diff._CHECK_COMPONENT["meta"]
    assert "src:audit.py" not in diff._CHECK_COMPONENT["blank"]
