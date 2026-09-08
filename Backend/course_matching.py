"""Chat course matching: find course mentions in free-form text ("I took
cmpsc131 and calc 2") and resolve them against a department catalog.

Split out of planner_engine.py verbatim as part of a size-reduction
refactor -- see the code-review note that flagged app.py/planner_engine.py
for splitting. Zero behavior change.

`norm_code`, `COURSE_ALIASES`, and `COURSE_CODE_RE` are imported from
planner_engine itself (rather than duplicated) -- safe at module level
because planner_engine.py only ever imports this module AFTER all three are
already defined in its own source (this module is never imported by
anything other than planner_engine.py), so there's no real import-order
hazard despite the two modules referencing each other.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Set, Tuple

from Courseplanner import Course
from planner_engine import norm_code, COURSE_ALIASES, COURSE_CODE_RE

_NOT_COURSE_WORDS = {
    "AND", "OR", "THE", "FOR", "TOOK", "SEM", "YEAR", "TERM", "TOP",
    "GPA", "GEN", "ED", "AP", "IB", "GHW", "FYS", "NEXT", "TAKE", "ALL",
    # A transcript's own GPA-summary rows ("Term Totals 18.000 18.000
    # 18.000 72.000", "Cum Totals 53.000 ...") regex-match the same
    # shape as a real course mention -- "Totals" immediately followed by
    # a 2-3 digit running-credit total looks exactly like "DEPT ###" to
    # COURSE_CODE_RE. Confirmed live against a real transcript export:
    # every term's running-total line leaked into "unmatched" as junk
    # like "TOTALS 18", "TOTALS 53" -- noise no real course was ever
    # dropped for, so it's filtered outright rather than surfaced as a
    # hint.
    "TOTALS",
    # Same story for a transcript's AP/IB "Test Credits" section -- "AP
    # Calculus AB 01/01/2023" reads as "AB" immediately followed by the
    # date's leading "01", producing an "AB 01" hint that isn't a real
    # course mention. "AB" isn't a real PSU department, so this is
    # always safe to drop (see the mention_code-in-catalog escape hatch
    # above -- a real "AB ###" course would still get through).
    "AB",
}

# Full department-name words a student might type instead of PSU's real
# short course-code prefix ("physics 211" instead of "PHYS 211") --
# COURSE_CODE_RE's own dept-prefix capture is capped at 6 letters (long
# enough for every real PSU prefix), so "PHYSICS" (7) can never match it
# directly and a mention like "physics 211" was silently dropped. Handled
# as its own small, explicit lookup rather than just widening that cap,
# so this can't start treating an arbitrary long English word ahead of a
# number ("completed 10 courses") as a course-code mention.
DEPT_NAME_ALIASES: Dict[str, str] = {
    "PHYSICS": "PHYS",
    "CHEMISTRY": "CHEM",
    "STATISTICS": "STAT",
    "PSYCHOLOGY": "PSYCH",
    "SOCIOLOGY": "SOC",
    "ECONOMICS": "ECON",
    "PHILOSOPHY": "PHIL",
    "BIOLOGY": "BIOL",
    # Expanded past the one department (Physics) that first surfaced this
    # bug to cover every other real PSU subject with a single-word full
    # name that doesn't literally equal its short catalog prefix -- a
    # spelled-out mention in ANY of these majors hit the identical silent
    # -drop bug, not just Physics. Only single-word official subject names
    # are listed here (COURSE_CODE_RE's alias slot is one token) -- compound
    # names ("Computer Science," "Electrical Engineering," "Political
    # Science," ...) aren't included since a student typing those out
    # wouldn't produce a single word immediately before the course number
    # anyway, so the underlying bug doesn't apply to them the same way.
    "ACCOUNTING": "ACCTG",
    "AGRICULTURE": "AG",
    "AGRONOMY": "AGRO",
    "AGROECOLOGY": "AGECO",
    "ANTHROPOLOGY": "ANTH",
    "ARCHITECTURE": "ARCH",
    "ASTRONOMY": "ASTRO",
    "BIOETHICS": "BIOET",
    "BIOTECHNOLOGY": "BIOTC",
    "CHINESE": "CHNS",
    "COMMUNICATIONS": "COMM",
    "CRIMINOLOGY": "CRIM",
    "CYBERSECURITY": "CYBER",
    "EDUCATION": "EDUC",
    "ENGLISH": "ENGL",
    "ENGINEERING": "ENGR",
    "ENTOMOLOGY": "ENT",
    "FINANCE": "FIN",
    "FORESTRY": "FOR",
    "FRENCH": "FR",
    "GEOGRAPHY": "GEOG",
    "GEOSCIENCES": "GEOSC",
    "GERMAN": "GER",
    "HEBREW": "HEBR",
    "HISTORY": "HIST",
    "HORTICULTURE": "HORT",
    "ITALIAN": "IT",
    "JAPANESE": "JAPNS",
    "KINESIOLOGY": "KINES",
    "KOREAN": "KOR",
    "LINGUISTICS": "LING",
    "MATHEMATICS": "MATH",
    "MANAGEMENT": "MGMT",
    "MARKETING": "MKTG",
    "METEOROLOGY": "METEO",
    "MICROBIOLOGY": "MICRB",
    "MINING": "MNG",
    "NURSING": "NURS",
    "NUTRITION": "NUTR",
    "PHOTOGRAPHY": "PHOTO",
    "RUSSIAN": "RUS",
    "SPANISH": "SPAN",
    "SURVEYING": "SUR",
    "THEATRE": "THEA",
    "THEATER": "THEA",
    "TURFGRASS": "TURF",
    "WILDLIFE": "WILDL",
}
# Lookahead-only (no number captured here) and requires a real 2-3 digit
# course number specifically -- "physics 1"/"physics 2" (the sequence-number
# phrasing already handled by COURSE_ALIASES below) must NOT be rewritten
# here, or the literal "PHYSICS 1" text that pass searches for would already
# be gone by the time it runs.
_DEPT_NAME_RE = re.compile(
    r"\b(" + "|".join(DEPT_NAME_ALIASES) + r")\b(?=\s*-?\s*\d{2,3}[A-Z]{0,2}\b)"
)

# DEPT_NAME_ALIASES intentionally excludes multi-word official names (see
# its own comment) -- but the regex above only requires the aliased word to
# sit immediately before the course number, with no check on what precedes
# THAT word. So "Electrical Engineering 210" still matches on the tail word
# "Engineering" -> ENGR, producing the phantom code "ENGR 210" (masking the
# real course EE 210); "Special Education 400" matches on "Education" ->
# EDUC, silently recording the real-but-wrong EDUC 400.
#
# A first attempt at guarding against this used an ALLOWLIST of "filler"
# words that were the only things permitted to precede the aliased word,
# blocking expansion for anything else. That inverted the actual odds: a
# real multi-word official compound name is the rare case, while ordinary
# sentences constantly put some other word right before the aliased word --
# including the app's own most common completion triggers ("completed",
# "passed", "finished", ...), none of which were in the filler list. So
# "I completed Physics 211" got blocked exactly like "Electrical
# Engineering 210" was supposed to be, and the whole mention silently
# vanished.
#
# Fixed as a DENYLIST instead: a small, explicit set of real PSU compound-
# major/department modifier words that are known to precede one of
# DEPT_NAME_ALIASES' tail words in a genuine official multi-word name.
# Only a preceding word in THIS set blocks expansion; every other preceding
# word (any ordinary verb, pronoun, article, or completion trigger a
# student would actually type) permits it, same as a clause boundary with
# no preceding word at all (start of string, or right after punctuation --
# _PRECEDING_WORD_RE simply finds none there).
#
# SCOPED PER TAIL WORD (round-2 regression fix): a first cut at this
# denylist was one flat, unscoped set checked with no awareness of which
# DEPT_NAME_ALIASES tail word was actually being matched -- so a modifier
# seeded to guard only one compound (e.g. "PHYSICAL" for "Physical
# Education") silently blocked every OTHER alias tail word it happened to
# precede too: "physical chemistry 457"/"physical geography 010" got
# blocked by the same "PHYSICAL" entry that was only ever meant to guard
# "Physical Education", and likewise "BIOLOGICAL" (seeded for "Biological
# Engineering") blocked "biological anthropology 021", "AGRICULTURAL"
# (seeded for "Agricultural Engineering") blocked "agricultural economics
# 104" -- all silently vanishing with no match at all. Keyed by the exact
# alias tail word (DEPT_NAME_ALIASES key / regex group(1) match) so a
# modifier only ever blocks the ONE compound it was actually seeded for.
_DEPT_NAME_BLOCK_WORDS: Dict[str, Set[str]] = {
    # ... ENGINEERING (Aerospace/Agricultural/Architectural/Biological/
    # Biomedical/Chemical/Civil/Computer/Electrical/Energy/Environmental/
    # Industrial/Mechanical/Mining/Nuclear/Petroleum Engineering are all
    # real official or commonly-shortened PSU major names)
    "ENGINEERING": {
        "AEROSPACE", "AGRICULTURAL", "ARCHITECTURAL", "BIOLOGICAL",
        "BIOMEDICAL", "CHEMICAL", "CIVIL", "COMPUTER", "ELECTRICAL", "ENERGY",
        "ENVIRONMENTAL", "INDUSTRIAL", "MECHANICAL", "MINING", "NUCLEAR",
        "PETROLEUM",
    },
    # ... EDUCATION (Adult/Career [and Technical]/Early [Childhood]/
    # Elementary [and Kindergarten]/Physical/Secondary/Special/Workforce
    # Education [and Development])
    "EDUCATION": {
        "ADULT", "CAREER", "CHILDHOOD", "EARLY", "ELEMENTARY", "PHYSICAL",
        "SECONDARY", "SPECIAL", "WORKFORCE",
    },
    # ... ARCHITECTURE (Landscape Architecture)
    "ARCHITECTURE": {"LANDSCAPE"},
    # ... HISTORY (Art History)
    "HISTORY": {"ART"},
    # ... MANAGEMENT (Risk Management, Supply Chain Management,
    # Hospitality Management)
    "MANAGEMENT": {"CHAIN", "HOSPITALITY", "RISK"},
}
_PRECEDING_WORD_RE = re.compile(r"([A-Z']+)\s*$")

# "MATH 140, 141" or "MATH 140 and 141" -- a student listing several course
# numbers under one department once, expecting each to count. COURSE_CODE_RE
# only ever anchors a dept prefix to the number immediately next to it and
# has no memory of an earlier one in the same sentence, so today only the
# first number in a run like this is ever recognized and the rest silently
# vanish. Requires at least one comma/and-joined continuation, so a lone
# "MATH 140" is left untouched.
_MULTI_COURSE_RUN_RE = re.compile(
    r"\b[A-Z]{2,6}\s*-?\s*\d{2,3}[A-Z]{0,2}"
    r"(?:(?:\s*,\s*(?:AND\s+)?|\s+AND\s+)\d{2,3}[A-Z]{0,2})+\b"
)


def _expand_dept_names(raw: str) -> str:
    def repl(m: "re.Match[str]") -> str:
        word = m.group(1)
        # Look only at what's immediately before this word (bounded search,
        # not a new substring) -- restricted to a real preceding *word*,
        # since anything else (digits, punctuation, nothing) means there's
        # no compound-name signal and this is safe to expand.
        prev = _PRECEDING_WORD_RE.search(m.string, 0, m.start(1))
        if prev and prev.group(1) in _DEPT_NAME_BLOCK_WORDS.get(word, ()):
            return word  # tail of a longer compound name -- leave as-is
        return DEPT_NAME_ALIASES[word]

    return _DEPT_NAME_RE.sub(repl, raw)


def _expand_multi_course_mentions(raw: str) -> str:
    def repl(m: "re.Match[str]") -> str:
        run = m.group(0)
        dept = re.match(r"[A-Z]{2,6}", run).group(0)
        nums = re.findall(r"\d{2,3}[A-Z]{0,2}", run)
        return " ".join(f"{dept} {n}" for n in nums)

    return _MULTI_COURSE_RUN_RE.sub(repl, raw)


def match_courses_in_text(text: str, catalog: Dict[str, Course]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Find course mentions in free-form text and resolve them against the catalog.

    Returns (matched, unmatched): matched entries carry code/name/credits so the
    UI can show the student exactly what was understood.
    """
    raw = (text or "").upper()
    raw = _expand_dept_names(raw)
    raw = _expand_multi_course_mentions(raw)
    matched: List[Dict[str, Any]] = []
    unmatched: List[str] = []
    seen: Set[str] = set()
    # A course-code-shaped alias (e.g. a cross-listed "CMPEN 315" -> "CMPSC
    # 315") gets resolved correctly by the alias pass below, but its raw
    # text ALSO looks like a real course-code mention to COURSE_CODE_RE --
    # without this, the second pass re-processes the same "CMPEN 315" text
    # as a literal, nonexistent course and dumps it in unmatched, so the
    # same mention shows up as both correctly credited AND "couldn't
    # match" at once. Tracking which raw mentions the alias pass already
    # claimed lets the second pass skip them.
    claimed_mentions: Set[str] = set()

    def add(code: str, mention: str):
        code = norm_code(code)
        if code in seen:
            return
        course = catalog.get(code)
        if course:
            seen.add(code)
            matched.append({
                "code": code,
                "name": course.name,
                "credits": course.credits,
                "mention": mention.strip(),
            })
        else:
            unmatched.append(mention.strip())

    for alias, code in COURSE_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", raw):
            add(code, alias.title())
            claimed_mentions.add(norm_code(alias))

    for m in COURSE_CODE_RE.finditer(raw):
        dept, num = m.groups()
        mention_code = norm_code(f"{dept} {num}")
        # _NOT_COURSE_WORDS exists to filter ordinary-English false positives
        # ("prerequisite FOR 200 level courses"), but a couple of its entries
        # (FOR, IB) are ALSO real PSU department prefixes. Checking the
        # catalog first lets a genuinely cataloged course through even when
        # its dept token is on the stopword list, while still treating that
        # same token as plain English when this student's currently-loaded
        # catalog has no such department (mention_code not in catalog).
        if dept in _NOT_COURSE_WORDS and mention_code not in catalog:
            continue
        if mention_code in claimed_mentions:
            continue
        add(f"{dept} {num}", m.group(0))

    return matched, unmatched
