"""An independent prerequisite checker.

"Independent" is meant narrowly and precisely: this module imports NOTHING from
the planning engine (no planner_engine, Courseplanner, or app), and does not
call prereqs_satisfied / concurrent_satisfied / missing_prereqs / any Course
object. It reads the raw catalog JSON files itself and applies the documented
meaning of the data:

    prereq_groups      AND of OR-groups. Every group needs at least one of its
                       alternatives completed in an EARLIER term (or already in
                       the student's completed set).
    concurrent_groups  AND of OR-groups. Every group needs at least one of its
                       alternatives completed earlier OR taken in the SAME term.
    excludes           flat OR. None of these may already be completed.

What it does NOT make independent, and the README says so: it reads the same
catalog files the engine reads (so a wrong scraped prerequisite is wrong in
both), and the cross-listing equivalences come from the same
Backend/data/course_aliases.json. It checks the engine's scheduling logic
against the catalog data, not the catalog data against reality.
"""
from __future__ import annotations

import glob
import json
import os
import re
from typing import Any, Dict, Iterable, List, Optional, Set

_CODE_RE = re.compile(r"^([A-Z][A-Z\-]{0,5})\s?(\d{1,3})([A-Z]{0,3})$")


def norm(code: str) -> str:
    """Upper-case, collapse whitespace, drop leading zeros: 'engl 015' -> 'ENGL 15'."""
    s = re.sub(r"\s+", " ", str(code).replace(" ", " ").strip().upper())
    m = _CODE_RE.match(s)
    if not m:
        return s
    return f"{m.group(1)} {int(m.group(2))}{m.group(3)}"


def looks_like_code(s: str) -> bool:
    return bool(_CODE_RE.match(re.sub(r"\s+", " ", str(s).replace(" ", " ").strip().upper())))


class Catalog:
    def __init__(self, backend_dir: str):
        self.courses: Dict[str, Dict[str, Any]] = {}
        self.dept_files: Set[str] = set()
        for path in sorted(glob.glob(os.path.join(backend_dir, "catalogs", "*_catalog.json"))):
            self.dept_files.add(os.path.basename(path)[: -len("_catalog.json")].upper())
            with open(path) as f:
                data = json.load(f)
            for key, entry in data.items():
                if not isinstance(entry, dict) or "prereq_groups" not in entry:
                    continue
                self.courses.setdefault(norm(entry.get("code", key)), entry)
        self._equiv: Dict[str, Set[str]] = {}
        alias_path = os.path.join(backend_dir, "data", "course_aliases.json")
        if os.path.exists(alias_path):
            with open(alias_path) as f:
                aliases = json.load(f)
            for a, b in aliases.items():
                if looks_like_code(a) and looks_like_code(b):
                    na, nb = norm(a), norm(b)
                    grp = self._equiv.get(na, {na}) | self._equiv.get(nb, {nb})
                    for c in grp:
                        self._equiv[c] = grp

    def known(self, code: str) -> bool:
        return norm(code) in self.courses

    def _expand(self, codes: Iterable[str]) -> Set[str]:
        out: Set[str] = set()
        for c in codes:
            n = norm(c)
            out |= self._equiv.get(n, {n})
        return out

    def violations(
        self, code: str, before: Iterable[str], same_term: Iterable[str] = ()
    ) -> List[Dict[str, Any]]:
        """Why `code` cannot be taken given what was completed `before` and what
        is taken in the same term. Empty list = valid. Unknown code = no claim."""
        entry = self.courses.get(norm(code))
        if entry is None:
            return []
        done = self._expand(before)
        together = done | self._expand(same_term)
        out: List[Dict[str, Any]] = []
        for group in entry.get("prereq_groups", []):
            alts = [norm(x) for x in group]
            if alts and not (set(alts) & done):
                out.append({"kind": "prerequisite", "course": norm(code), "needs_one_of": sorted(alts)})
        for group in entry.get("concurrent_groups", []):
            alts = [norm(x) for x in group]
            if alts and not (set(alts) & together):
                out.append({"kind": "concurrent", "course": norm(code), "needs_one_of": sorted(alts)})
        ex = {norm(x) for x in entry.get("excludes", [])} & done
        if ex:
            out.append({"kind": "excluded", "course": norm(code), "already_completed": sorted(ex)})
        return out


_DEVELOPMENTAL_MATH_RE = re.compile(r"^MATH (\d{1,2})[A-Z]?$")


def is_developmental_math_gap(v: Dict[str, Any]) -> bool:
    """True when every alternative of an unmet group is a sub-calculus MATH
    course (MATH 1..41). PSU's published math-placement policy lets a higher
    math course stand in for these, which the engine models; this checker is
    deliberately strict and does not, so such a finding is tagged, not hidden."""
    alts = v.get("needs_one_of") or []
    return bool(alts) and all(
        (m := _DEVELOPMENTAL_MATH_RE.match(a)) is not None and int(m.group(1)) <= 41 for a in alts
    )
