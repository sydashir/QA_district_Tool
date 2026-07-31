"""Seeded ground-truth control for the Phase-2 AI pass.

The GL pilot measured PRECISION (of what the model reports, how much is real). It cannot measure
RECALL, because we have no list of every defect on the page. This measures recall against known
ground truth: take REAL GL page blocks, corrupt exactly one word in each, and check whether the
model finds the word we broke.

Two groups, because they answer different questions:

  A. CONFIRMED  — the 7 misspellings Connor reported to the client. `checks/misspelling.py` already
     catches these by exact match, so the model finding them proves only that the mechanism works.
  B. NOVEL      — plausible misspellings that are on NO list anywhere. This is the model's actual
     job: the next typo nobody has reported yet. Group B recall is the number that decides whether
     the AI tier is worth paying for.

CLEAN blocks (no seeded defect) are mixed in unlabelled to measure the false-alarm rate under the
identical prompt, so recall is not read in isolation from noise.

Every seeded word was verified absent from allowlist.json before running (an allowlisted term would
be suppressed by the prompt, invalidating the trial).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path

import anthropic

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from spike.gl_pilot import ALLOWLIST, SCHEMA, SYSTEM, collect  # noqa: E402

# (correct, misspelled) — corrupting a word that REALLY occurs in GL copy keeps the surrounding
# sentence authentic, so the model sees a normal page with one broken word.
CONFIRMED = [           # group A: Connor's reported set (deterministic layer already has these)
    ("inpatient", "inpateint"), ("alcohol", "alchohol"), ("residential", "residental"),
    ("Tennessee", "tennesse"), ("programming", "programing"),
]
NOVEL = [               # group B: on no list anywhere — the real test
    ("detoxification", "detoxifcation"), ("behavioral", "behaviorial"),
    ("assessment", "assesment"), ("receive", "recieve"), ("occurrence", "occurence"),
    ("administration", "adminstration"), ("withdrawal", "withdrawl"),
    ("psychiatric", "psychatric"), ("counseling", "couseling"), ("treatment", "treatmnet"),
]


def seed(blocks: list[str], correct: str, wrong: str) -> tuple[str, str] | None:
    """Corrupt ONE occurrence of `correct` in the first block long enough to be real page copy."""
    pat = re.compile(rf"\b{re.escape(correct)}\b", re.IGNORECASE)
    for b in blocks:
        m = pat.search(b)
        if m and 120 <= len(b) <= 1200:
            # preserve the original capitalisation so the defect is a spelling error, not a case one
            repl = wrong.capitalize() if m.group(0)[0].isupper() else wrong
            return b[:m.start()] + repl + b[m.end():], repl
    return None


def ask(client, model: str, system: str, block: str) -> list[dict]:
    r = client.messages.create(
        model=model, max_tokens=1000,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": block}],
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}})
    try:
        return json.loads(next(x.text for x in r.content if x.type == "text"))["findings"]
    except Exception:
        return []


def main() -> None:
    uniq, *_ = asyncio.run(collect(25))
    allow = json.loads(ALLOWLIST.read_text())["terms"]
    system = SYSTEM.format(allowlist=", ".join(allow))
    client = anthropic.Anthropic()

    trials, skipped = [], []
    for group, pairs in (("A-confirmed", CONFIRMED), ("B-novel", NOVEL)):
        for correct, wrong in pairs:
            got = seed(uniq, correct, wrong)
            if got is None:
                skipped.append(f"{correct} (no GL block contains it)")
                continue
            trials.append((group, correct, got[1], got[0]))
    # unlabelled clean blocks, same prompt — measures noise alongside recall
    clean = [b for b in uniq if 200 <= len(b) <= 1200][:8]

    print(f"seeded trials: {len(trials)}  ({sum(g=='A-confirmed' for g,*_ in trials)} confirmed / "
          f"{sum(g=='B-novel' for g,*_ in trials)} novel) | clean control blocks: {len(clean)}")
    if skipped:
        print(f"  SKIPPED (word absent from the GL sample): {', '.join(skipped)}")

    for model in ("claude-haiku-4-5", "claude-sonnet-5"):
        print(f"\n{'='*70}\n{model}\n{'='*70}")
        hits = {"A-confirmed": [0, 0], "B-novel": [0, 0]}
        for group, correct, wrong, block in trials:
            fs = ask(client, model, system, block)
            found = any(wrong.lower() in f["wrong"].lower() for f in fs)
            hits[group][0] += found
            hits[group][1] += 1
            other = [f"{f['wrong']!r}->{f['correct']!r}" for f in fs
                     if wrong.lower() not in f["wrong"].lower()]
            print(f"  {'HIT ' if found else 'MISS'} [{group}] {correct} -> {wrong}"
                  + (f"   (also said: {'; '.join(other[:2])})" if other else ""))
        noise = 0
        for b in clean:
            fs = ask(client, model, system, b)
            noise += len(fs)
        for g, (h, n) in hits.items():
            print(f"  RECALL {g}: {h}/{n} = {h/max(1,n):.0%}")
        print(f"  false alarms on {len(clean)} clean blocks: {noise}")


if __name__ == "__main__":
    os.environ.setdefault("ANTHROPIC_LOG", "warn")
    main()
