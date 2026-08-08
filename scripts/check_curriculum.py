#!/usr/bin/env python3
"""Validate a curriculum.json against everything the engine assumes about it.

    python scripts/check_curriculum.py [path]

Run this immediately after dropping in the real file. It checks structure and
field names, and -- more importantly -- checks that the flagship day bands in
question_engine.py actually line up with where the real syllabus puts Agentic
AI/MCP and Embeddings/Vector. That mismatch is silent otherwise: rule (d) would
still fire, just on the wrong days.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import question_engine as qe
from app.models import Curriculum, CurriculumDay

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "curriculum.json"

# Each flagship constant, the module title it is supposed to track, and the
# keywords to fall back on when the file declares no modules.
BANDS = {
    "AGENTIC_DAYS": (qe.AGENTIC_DAYS, "agentic", ("agent", "mcp", "tool use", "orchestr")),
    "VECTOR_DAYS": (qe.VECTOR_DAYS, "vector", ("embed", "vector", "retriev", "chunk", "rag")),
}

problems: list[str] = []
warnings: list[str] = []


def main(path: Path) -> int:
    raw = json.loads(path.read_text(encoding="utf-8"))
    entries = raw if isinstance(raw, list) else raw.get("days", [])
    if isinstance(raw, dict):
        if dropped_top := set(raw) - set(Curriculum.model_fields):
            warnings.append(
                f"top-level keys not on Curriculum (silently ignored): {sorted(dropped_top)}"
            )
    print(f"{path}\n  top-level: {'list' if isinstance(raw, list) else 'object with .days'}")
    print(f"  entries:   {len(entries)}")

    # 1. Fields the models declare, vs fields the file actually carries.
    declared = set(CurriculumDay.model_fields)
    seen: set[str] = set()
    for entry in entries:
        if isinstance(entry, dict):
            seen |= set(entry)
    if missing := {"day", "title"} - seen:
        problems.append(f"required field(s) absent from every entry: {sorted(missing)}")
    if dropped := seen - declared:
        warnings.append(
            f"fields present in JSON but not on CurriculumDay (silently ignored): "
            f"{sorted(dropped)}"
        )
    if unused := declared - seen:
        warnings.append(f"CurriculumDay fields absent from JSON (will default): {sorted(unused)}")
    print(f"  fields:    {sorted(seen)}")

    # 2. Parse through the real model.
    try:
        curriculum = Curriculum.load(path)
    except Exception as exc:  # noqa: BLE001 -- surface the validation error verbatim
        problems.append(f"Curriculum.load failed: {exc}")
        return report()

    days = curriculum.day_numbers()
    if len(set(days)) != len(days):
        problems.append("duplicate day numbers")
    if days != list(range(1, len(days) + 1)):
        warnings.append(f"day numbers are not a contiguous 1..N run (got {days[:5]}...{days[-3:]})")
    print(f"  days:      {days[0]}..{days[-1]}  ({len(days)} total)")
    print(f"  tools:     {len(curriculum.tool_vocabulary())} distinct (feeds answer-depth scoring)")

    if blank := [d.day for d in curriculum.days if not d.title.strip()]:
        problems.append(f"days with an empty title: {blank}")
    if no_obj := [d.day for d in curriculum.days if not d.objectives]:
        warnings.append(f"days with no objectives (thin prompt briefs): {no_obj}")

    # 3. The band check: do the hardcoded flagship constants match the syllabus?
    #
    # When the file declares modules, that is the authoritative answer -- compare
    # the constant to the module's own day range and stop guessing from titles.
    if curriculum.modules:
        print(f"\n{curriculum.cohort or 'cohort'}: {len(curriculum.modules)} modules declared")
        for m in curriculum.modules:
            span = sorted(m.day_range)
            print(f"  {m.n}. {m.title:<38} days {span[0]}-{span[-1]}")

    print("\nFlagship bands (question_engine):")
    for const, (band, module_hint, keywords) in BANDS.items():
        band_days = sorted(band)
        if missing := set(band_days) - set(days):
            problems.append(f"{const} references days not in the file: {sorted(missing)}")
            continue

        module = curriculum.module_named(module_hint) if curriculum.modules else None
        if module is not None:
            actual = sorted(module.day_range)
            if set(actual) == set(band_days):
                print(f"  ok   {const} == module {module.n} '{module.title}' ({actual[0]}-{actual[-1]})")
            else:
                problems.append(
                    f"{const} is {band_days} but module {module.n} "
                    f"'{module.title}' spans {actual}. Update the constant in "
                    f"question_engine.py -- rule (d) would probe the wrong days."
                )
            continue

        # No modules in the file: fall back to matching titles and tools.
        hits = []
        for d in band_days:
            entry = curriculum.get(d)
            haystack = f"{entry.title} {' '.join(entry.tools)}".lower()
            match = any(k in haystack for k in keywords)
            hits.append(match)
            print(f"  {'ok  ' if match else 'MISS'} day {d:>2}: {entry.title}")
        if not any(hits):
            problems.append(
                f"{const}: no day in {band_days} looks like this topic. "
                f"Update the constant in question_engine.py to the real day range."
            )
        elif not all(hits):
            warnings.append(f"{const}: some days in {band_days} do not match the topic")

    return report()


def report() -> int:
    print()
    for w in warnings:
        print(f"WARN  {w}")
    for p in problems:
        print(f"FAIL  {p}")
    if not problems:
        print("PASS  curriculum is compatible with the engine.")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PATH))
