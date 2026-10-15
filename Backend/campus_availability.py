"""Turn a bulletins.psu.edu program page's campus information into the
`campus` list a degree-plan file carries.

Pure functions only (no network, no file writes) so every rule here is unit
tested. The scripts that fetch pages and edit plan files live in
scripts/scrape_campus_availability.py and scripts/apply_campus_availability.py.

Two kinds of campus statement exist on a program page, and they are NOT
interchangeable:

* the program-level picker ("At which campus can I study this program?"):
  a "Begin Campus" list and an "End Campus" list; and
* per-option lines ("Available at the following campuses: ...") sitting
  under each option's own heading, on pages whose options differ by campus
  (Biology, Business, ...).

A plan file models ONE option's curriculum, so on a page with per-option
lines the program-level picker alone over-claims. The rules below use the
plan's own option when it names one unambiguously, and otherwise say
"unknown" rather than guess (an intersection of option lines was tried and
rejected: on campus-specific pages those lines cross-reference other
campuses' programs, so an intersection can name a campus the plan does not
apply to).

The governing rule everywhere: telling a student their program runs at
their campus when it doesn't is worse than saying nothing. Anything this
module can't establish comes back as None, never as a guess.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from bs4 import BeautifulSoup

# Campuses PSU's Board of Trustees approved closing after Spring 2027. They
# still operate (continuing students are enrolled there), so "Any Penn State
# Campus" expands to include them -- the convention the 2026-09 hand-verified
# multi-campus files (FRENCHBA, PLSC, MDSBW) already use. Kept as a named set
# so a future decision to drop them after the closure is a one-line change.
CLOSING_CAMPUSES = frozenset(
    {"DuBois", "Fayette", "Mont Alto", "New Kensington", "Shenango", "Wilkes-Barre", "York"}
)

# Mirrors planner_engine.PSU_CAMPUSES (kept as a literal so this module stays
# import-light; a test asserts the two never drift).
KNOWN_CAMPUSES: Tuple[str, ...] = (
    "University Park", "Abington", "Altoona", "Beaver", "Berks", "Brandywine",
    "DuBois", "Erie", "Fayette", "Greater Allegheny", "Harrisburg", "Hazleton",
    "Lehigh Valley", "Mont Alto", "New Kensington", "Schuylkill", "Scranton",
    "Shenango", "Wilkes-Barre", "World Campus", "York",
)

_ALIASES = {
    "behrend": "Erie",
    "erie, the behrend college": "Erie",
    "penn state behrend": "Erie",
    "capital": "Harrisburg",
    "capital college": "Harrisburg",
    "penn state harrisburg": "Harrisburg",
    "worthington scranton": "Scranton",
    "great allegheny": "Greater Allegheny",
    "world campus": "World Campus",
    "penn state world campus": "World Campus",
    "online": "World Campus",
}

_NON_ALTERNATIVE_HEADING_RE = re.compile(
    r"integrated|accelerated|dual|master|\bm\.(?:s|ed|eng|b\.a)\b|\bph\.?d\b|4\+1|second degree",
    re.IGNORECASE,
)


def normalize_campus(name: str) -> Optional[str]:
    """Canonical PSU_CAMPUSES spelling for one bulletin campus name, or None
    when it isn't an undergraduate campus this app models."""
    s = re.sub(r"^penn state\s+", "", name.strip(), flags=re.IGNORECASE).strip(" .")
    if not s:
        return None
    low = s.lower()
    if low in _ALIASES:
        return _ALIASES[low]
    for c in KNOWN_CAMPUSES:
        if c.lower() == low:
            return c
    return None


def split_campus_text(raw: str) -> List[str]:
    return [p.strip() for p in re.split(r",|;|\band\b", raw) if p.strip()]


_OPTION_CAMPUS_RE = re.compile(r"Available at the following campuses:\s*(.+)", re.IGNORECASE | re.DOTALL)


def _parse_option_blocks(soup: BeautifulSoup) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for node in soup.find_all(string=_OPTION_CAMPUS_RE):
        m = _OPTION_CAMPUS_RE.search(node)
        if not m:
            continue
        raw = " ".join(m.group(1).split())
        heading = node.find_previous(["h2", "h3", "h4", "h5"])
        out.append({
            "heading": heading.get_text(" ", strip=True) if heading else "",
            "campuses": split_campus_text(raw),
        })
    return out


def parse_campus_availability(html: str) -> Dict[str, Any]:
    """{"found", "begin", "end", "any_begin", "raw_begin", "raw_end",
    "closure_banner", "options"}. "found" is False when the page has no
    program-level picker -- callers must treat that as UNKNOWN, never as UP."""
    soup = BeautifulSoup(html, "html.parser")
    options = _parse_option_blocks(soup)
    block = soup.find(id="beg-end-list") or soup.find(id="campus-list")
    if block is None:
        return {"found": False, "begin": [], "end": [], "any_begin": False,
                "raw_begin": "", "raw_end": "", "closure_banner": False, "options": options}

    def _list_after(label: str) -> str:
        for strong in block.find_all("strong"):
            if strong.get_text(strip=True).lower() == label:
                para = strong.find_parent("p")
                sib = para.find_next_sibling("p") if para else None
                return sib.get_text(" ", strip=True) if sib else ""
        return ""

    raw_begin = _list_after("begin campus")
    raw_end = _list_after("end campus")
    # "Degree Information At Additional Campuses" -- a dropdown of other
    # campuses that offer this degree under their own program page.
    additional: List[str] = []
    for opt in (soup.find(id="campus-list") or block).find_all("option"):
        label = opt.get_text(" ", strip=True)
        if label and not label.lower().startswith("choose"):
            additional.append(label)
    return {
        "found": bool(raw_begin or raw_end),
        "begin": split_campus_text(raw_begin),
        "end": split_campus_text(raw_end),
        "additional": additional,
        "any_begin": "any penn state campus" in raw_begin.lower(),
        "raw_begin": raw_begin,
        "raw_end": raw_end,
        "closure_banner": block.find(class_="bel-closure") is not None,
        "options": options,
    }


def _ordered(campuses: Iterable[str]) -> List[str]:
    s = set(campuses)
    return [c for c in KNOWN_CAMPUSES if c in s]


def _normalize_all(names: Iterable[str]) -> Tuple[List[str], List[str]]:
    ok: List[str] = []
    unknown: List[str] = []
    for n in names:
        # "Any Penn State Campus" is handled by the any_begin flag, not as a
        # campus name; reporting it as unrecognised would just be noise.
        if "any penn state campus" in n.lower():
            continue
        c = normalize_campus(n)
        if c:
            if c not in ok:
                ok.append(c)
        else:
            unknown.append(n)
    return ok, unknown


def _option_core(heading: str) -> str:
    h = re.sub(r"\([^)]*\)", "", heading)
    h = re.sub(r"\boptions?\b", "", h, flags=re.IGNORECASE)
    return " ".join(h.split()).strip(" -:,")


def match_plan_option(plan: Dict[str, Any], options: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The ONE page option this plan file models, or None if it can't be
    established unambiguously. A plan names its option as "<core> option" in
    its title or notes (e.g. "General Nursing Option", "Life Sciences
    option"); a bare word match is deliberately not enough."""
    text = f"{plan.get('title', '')}\n{plan.get('notes', '')}".lower()
    hits: List[Dict[str, Any]] = []
    for opt in options:
        core = _option_core(opt["heading"]).lower()
        if len(core) < 4:
            continue
        if re.search(r"\b" + re.escape(core) + r"\s+option\b", text):
            hits.append(opt)
    distinct = {tuple(sorted(o["campuses"])) for o in hits}
    if len(hits) >= 1 and len(distinct) == 1:
        return hits[0]
    return None


def derive_plan_campuses(plan: Dict[str, Any], page: Dict[str, Any]) -> Dict[str, Any]:
    """Decide what campus list a plan file should carry from its page.

    Returns {"campuses": [...] | None, "explicit": [...], "basis": str,
    "unknown": [names the page used that aren't modeled campuses]}.
    campuses is None when nothing trustworthy can be said; explicit is the
    subset the page NAMES outright (no "Any Penn State Campus" expansion). basis is one of:
      option, option-intersection, program-picker, none
    """
    if page.get("error"):
        return {"campuses": None, "basis": "none", "unknown": [], "why": page["error"]}

    real_options = [o for o in page.get("options", []) if not _NON_ALTERNATIVE_HEADING_RE.search(o["heading"])]
    # Campuses the program-level picker names outright (Begin/End). This is
    # the only evidence allowed to EXTEND an already hand-curated list.
    picker_names, _ = _normalize_all(list(page.get("begin", [])) + list(page.get("end", [])))
    picker_explicit = _ordered(picker_names)

    if real_options:
        chosen = match_plan_option(plan, real_options)
        if chosen is not None:
            names, unknown = _normalize_all(chosen["campuses"])
            if names:
                return {"campuses": _ordered(names), "explicit": _ordered(names), "basis": "option",
                        "picker_explicit": picker_explicit,
                        "unknown": unknown, "why": f"option '{chosen['heading']}'"}
        # No single option identified. Deliberately NOT guessing an
        # intersection: on a campus-specific page ("... (Behrend)") the
        # per-option lines are cross-references to other campuses' own
        # programs, and an intersection of them can name a campus the plan
        # doesn't apply to at all (found on IEBH and SECEDBH). Unknown is
        # safer than a wrong claim; the plan keeps its default.
        return {"campuses": None, "explicit": [], "picker_explicit": picker_explicit, "basis": "none",
                "unknown": [], "why": "page has per-option campus lines and the plan names no single option"}

    if not page.get("found"):
        return {"campuses": None, "basis": "none", "unknown": [], "why": "no campus information on page"}
    if "not available at any campus" in (page.get("raw_begin", "") + " " + page.get("raw_end", "")).lower():
        return {"campuses": None, "basis": "none", "unknown": [],
                "why": "bulletin says: currently not available at any campus location"}

    begin_names, unk_b = _normalize_all(page.get("begin", []))
    end_names, unk_e = _normalize_all(page.get("end", []))
    # "Degree Information At Additional Campuses" is deliberately NOT unioned
    # in: each campus listed there has its OWN program page with its own
    # Suggested Academic Plan (confirmed across English, Biology, Business,
    # ...: same-named programs at different campuses have different plans).
    # Claiming this plan for those campuses would be wrong; they are separate
    # plan files (Pattern B). It is parsed only so tooling can see which
    # sibling programs exist.
    unk_a: List[str] = []
    s = set(begin_names) | set(end_names)
    # Campuses the page NAMES, as opposed to ones only implied by "Any Penn
    # State Campus". Only these may be added to a hand-curated list.
    explicit = _ordered(s)
    if page.get("any_begin"):
        s |= {c for c in KNOWN_CAMPUSES if c != "World Campus"}
    if not s:
        return {"campuses": None, "basis": "none", "unknown": unk_b + unk_e + unk_a, "why": "picker named no known campus"}
    return {"campuses": _ordered(s), "explicit": explicit, "picker_explicit": picker_explicit,
            "basis": "program-picker", "unknown": unk_b + unk_e + unk_a,
            "why": f"begin: {page.get('raw_begin', '')[:60]} / end: {page.get('raw_end', '')[:60]}"}
