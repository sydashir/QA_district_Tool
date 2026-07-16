"""M2 report writers — JSONL, CSV, summary.

All consume an ITERABLE of findings (never a materialized list is required), so the
streaming orchestrator can feed findings as they're produced. The summary uses an
in-stream ``Rollup`` accumulator rather than recomputing from a collection.

- JSONL: one finding per line, complete — broken-link findings keep their full source array.
- CSV: one row per finding, human triage — broken links flatten to a representative page_url
  + source_count (the full array lives in JSONL). first_seen/last_seen are filled by the diff step.
- summary.json: run metadata + rollup counters.
"""
from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

from .report import Finding

CSV_COLUMNS = [
    "brand", "page_url", "page_title", "check", "issue", "severity",
    "location", "snippet", "suggestion", "source_count", "first_seen", "last_seen",
]


class Rollup:
    """In-stream severity/check counters. Call ``add(finding)`` per finding as it streams."""

    def __init__(self) -> None:
        self.total = 0
        self.by_severity: Counter = Counter()
        self.by_check: Counter = Counter()
        self.by_check_severity: Counter = Counter()

    def add(self, f: Finding) -> None:
        self.total += 1
        self.by_severity[f.severity.value] += 1
        self.by_check[f.check] += 1
        self.by_check_severity[(f.check, f.severity.value)] += 1

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "by_severity": dict(self.by_severity),
            "by_check": dict(self.by_check),
            "by_check_severity": {f"{c}:{s}": n for (c, s), n in self.by_check_severity.items()},
        }


def write_jsonl(findings, path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for f in findings:
            fh.write(f.model_dump_json())
            fh.write("\n")
    return path


def _row(f: Finding, brand: str, titles: dict) -> dict:
    sources = f.details.get("sources") if isinstance(f.details, dict) else None
    return {
        "brand": brand,
        "page_url": f.url,  # for broken links this is the representative source page
        "page_title": titles.get(f.url, "") or "",
        "check": f.check,
        "issue": f.issue,
        "severity": f.severity.value,
        "location": f.location or "",
        "snippet": f.snippet or "",
        "suggestion": f.suggestion or "",
        "source_count": str(len(sources)) if sources else "",
        "first_seen": "",  # filled by the diff step
        "last_seen": "",
    }


def write_csv(findings, path, brand: str, titles: dict | None = None) -> Path:
    titles = titles or {}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        w.writeheader()
        for f in findings:
            w.writerow(_row(f, brand, titles))
    return path


def write_summary(rollup: Rollup, meta: dict, path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({**meta, "rollup": rollup.to_dict()}, indent=2), encoding="utf-8")
    return path
