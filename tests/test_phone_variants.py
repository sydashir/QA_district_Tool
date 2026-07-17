"""Regression lock for the phone check against the client's OWN documented mangled variants
(NAP sheet 'OLD GL Numbers:' find-and-replace map, cols 62-104). It locks the behavior we're
KEEPING — proven by feeding the real variants through the check. Vanity conversion is marked
pending a ruling (we're about to change it), because a regression test that enshrines a known
bug is worse than no test.
"""
from __future__ import annotations

import pytest

from auditor.checks.phone import normalize


@pytest.mark.parametrize("variant,expected", [
    ("888-861-16858", None),                     # 8-digit typo -> REJECTED, not silently truncated
    ("<b>(</b>800) 994-2184", "+18009942184"),   # HTML tags inside the number -> correct number
    ("800) 994-2184", "+18009942184"),           # missing opening paren -> correct
    ("800-994/2184", "+18009942184"),            # slash separator -> correct
    ("(800) 692-9850", "+18006929850"),          # the retired headline number -> parsed
    ("+1 (800) 692-9850", "+18006929850"),
    ("1-800-662-HELP (4357)", None),             # mixed letters+digits -> rejected
])
def test_mangled_variants_locked(variant, expected):
    assert normalize(variant) == expected


@pytest.mark.skip(reason="vanity conversion (TALK->8255) pending Syed's ruling; assert after the fix")
def test_vanity_pending():
    # Today normalize() converts vanity letters (keypad T-A-L-K -> 8255) but the visible-text
    # PhoneNumberMatcher path does NOT — the two extraction paths disagree. Once the fix lands
    # (convert in both paths), un-skip and assert they agree here.
    assert normalize("1-800-273-TALK") == "+18002738255"
