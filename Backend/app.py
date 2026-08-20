from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Tuple

import requests
from flask import Flask, jsonify, request
from flask_cors import CORS

from Courseplanner import build_progression_graph
import planner_engine as engine
import transfer_credit as tc

# Optional RAG retrieval (advising notes fed to the LLM explanation only).
try:
    from rag_retrieve import load_index, top_k_chunks, format_context
except Exception:  # pragma: no cover - missing optional module
    load_index = top_k_chunks = format_context = None

# ----------------------------
# Config (environment-driven)
# ----------------------------

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3:latest")
OLLAMA_TIMEOUT_S = int(os.getenv("OLLAMA_TIMEOUT_S", "25"))
USE_OLLAMA = os.getenv("USE_OLLAMA", "1") not in ("0", "false", "no")
FLASK_DEBUG = os.getenv("FLASK_DEBUG", "0") in ("1", "true", "yes")
CORS_ORIGINS = [
    o.strip()
    for o in os.getenv(
        "CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000,http://localhost:4200"
    ).split(",")
    if o.strip()
]
RAG_INDEX_PATH = os.getenv(
    "RAG_INDEX_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "rag_data", "rag_index.json"),
)

_MAJOR_ALIASES = {
    "COMPUTER SCIENCE": "CMPSC",
    "CS": "CMPSC",
    "CMPSC": "CMPSC",
    "COMPUTER ENGINEERING": "CMPEN",
    "CMPEN": "CMPEN",
    "MATHEMATICS": "MATH",
    "MATH": "MATH",
    "STATISTICS": "STAT",
    "STAT": "STAT",
    "BIOCHEMISTRY AND MOLECULAR BIOLOGY": "BMB",
    "BIOCHEMISTRY": "BMB",
    "MOLECULAR BIOLOGY": "BMB",
    "BMB": "BMB",
    "CHEMISTRY": "CHEM",
    "CHEM": "CHEM",
    "PREMEDICINE": "PREMED",
    "PRE-MEDICINE": "PREMED",
    "PRE MEDICINE": "PREMED",
    "PREMED": "PREMED",
    "PRE-MED": "PREMED",
    "PRE MED": "PREMED",
    "NURSING": "NURS",
    "NURS": "NURS",
    "ENGLISH": "ENGL",
    "BUSINESS": "BUSINESS",
    "CYBERSECURITY": "CYBER",
    "CYBERSECURITY ANALYTICS": "CYBER",
    "CYBER": "CYBER",
    "INFORMATION SCIENCES AND TECHNOLOGY": "CYBER",
    "IST": "CYBER",
    "BIOLOGY": "BIOL",
    "BIOL": "BIOL",
    "ACCOUNTING": "ACCTG",
    "ACCTG": "ACCTG",
    "FINANCE": "FIN",
    "SUPPLY CHAIN AND INFORMATION SYSTEMS": "SCM",
    "SUPPLY CHAIN": "SCM",
    "MARKETING": "MKTG",
    "MANAGEMENT": "MGMT",
    "ACTUARIAL SCIENCE": "ACTSC",
    "ACTUARIAL": "ACTSC",
    "BUSINESS ANALYTICS AND INFORMATION SYSTEMS": "BAIS",
    "BUSINESS ANALYTICS": "BAIS",
    "CORPORATE INNOVATION AND ENTREPRENEURSHIP": "CIE",
    "CORPORATE INNOVATION": "CIE",
    "ENTREPRENEURSHIP": "CIE",
    "REAL ESTATE": "REST",
    "RISK MANAGEMENT": "RM",
    "ELECTRICAL ENGINEERING": "EE",
    "MECHANICAL ENGINEERING": "ME",
    "CIVIL ENGINEERING": "CE",
    "ECONOMICS": "ECON",
    "ECON": "ECON",
    "POLITICAL SCIENCE": "PLSC",
    "POLI SCI": "PLSC",
    "PLSC": "PLSC",
    "INDUSTRIAL ENGINEERING": "IE",
    "PHYSICS": "PHYS",
    "MICROBIOLOGY": "MICRB",
    "BIOTECHNOLOGY": "BIOTECH",
    "CHEMICAL ENGINEERING": "CHE",
    "AEROSPACE ENGINEERING": "AERSP",
    "BIOMEDICAL ENGINEERING": "BME",
    "NUCLEAR ENGINEERING": "NUCE",
    "ASTRONOMY AND ASTROPHYSICS": "ASTRO",
    "ASTRONOMY": "ASTRO",
    "ASTROPHYSICS": "ASTRO",
    "FORENSIC SCIENCE": "FRNSC",
    "BIOLOGICAL ENGINEERING": "BE",
    "NEUROBIOLOGY": "NEURO",
    "PLANETARY SCIENCE AND ASTRONOMY": "PLANET",
    "PLANETARY SCIENCE": "PLANET",
    "ENGINEERING SCIENCE": "ESC",
    "DATA SCIENCES": "DS",
    "DATA SCIENCE": "DS",
    "SURVEYING ENGINEERING": "SUR",
    "ELECTRICAL ENGINEERING TECHNOLOGY": "EET",
    "ELECTRO-MECHANICAL ENGINEERING TECHNOLOGY": "EMET",
    "ELECTROMECHANICAL ENGINEERING TECHNOLOGY": "EMET",
    "INTEGRATIVE SCIENCE": "INTSC",
    "METEOROLOGY AND ATMOSPHERIC SCIENCE": "METEO",
    "METEOROLOGY": "METEO",
    "GEOSCIENCES": "GEOSCI",
    "GEOGRAPHY": "GEOG",
    "ENERGY ENGINEERING": "ENGY",
    "MATERIALS SCIENCE AND ENGINEERING": "MATSCI",
    "MATERIALS SCIENCE": "MATSCI",
    "EARTH SCIENCES": "EARTHSCI",
    "GEOBIOLOGY": "GEOBIO",
    "MINING ENGINEERING": "MINE",
    "PETROLEUM AND NATURAL GAS ENGINEERING": "PNG",
    "PETROLEUM ENGINEERING": "PNG",
    "ENVIRONMENTAL SYSTEMS ENGINEERING": "ENVSYS",
    "ENERGY BUSINESS AND FINANCE": "EBFIN",
    "ENERGY BUSINESS": "EBFIN",
    "EARTH SCIENCE AND POLICY": "ESP",
    "ENERGY AND SUSTAINABILITY POLICY": "ESUS",
    "ANIMAL SCIENCE": "ANSC",
    "FOOD SCIENCE": "FDSC",
    "PLANT SCIENCES": "PLSCI",
    "PLANT SCIENCE": "PLSCI",
    "AGRIBUSINESS MANAGEMENT": "AGBM",
    "IMMUNOLOGY AND INFECTIOUS DISEASE": "IID",
    "PHARMACOLOGY AND TOXICOLOGY": "PHTX",
    "ENVIRONMENTAL RESOURCE MANAGEMENT": "ERM",
    "WILDLIFE AND FISHERIES SCIENCE": "WFS",
    "AGRICULTURAL AND BIORENEWABLE SYSTEMS MANAGEMENT": "ABSM",
    "VETERINARY AND BIOMEDICAL SCIENCES": "VBS",
    "TURFGRASS SCIENCE": "TURF",
    "FOREST ECOSYSTEMS": "FORES",
    "COMMUNITY, ENVIRONMENT, AND DEVELOPMENT": "CED",
    "COMMUNITY ENVIRONMENT AND DEVELOPMENT": "CED",
    "ARTIFICIAL INTELLIGENCE METHODS AND APPLICATIONS": "AIMA",
    "INFORMATION TECHNOLOGY ETHICS AND COMPLIANCE": "IEC",
    "SECURITY AND RISK ANALYSIS": "SRA",
    "HUMAN-CENTERED DESIGN AND DEVELOPMENT": "HCDD",
    "HUMAN CENTERED DESIGN AND DEVELOPMENT": "HCDD",
    "ENTERPRISE TECHNOLOGY INTEGRATION": "ETI",
    "JOURNALISM": "JOURN",
    "ADVERTISING/PUBLIC RELATIONS": "ADPR",
    "ADVERTISING AND PUBLIC RELATIONS": "ADPR",
    "TELECOMMUNICATIONS AND MEDIA INDUSTRIES": "TELE",
    "FILM PRODUCTION": "FLMPR",
    "MEDIA STUDIES": "MDST",
    "KINESIOLOGY": "KINES",
    "NUTRITIONAL SCIENCES": "NUTR",
    "HUMAN DEVELOPMENT AND FAMILY STUDIES": "HDFS",
    "HEALTH POLICY AND ADMINISTRATION": "HPA",
    "BIOBEHAVIORAL HEALTH": "BBH",
    "COMMUNICATION SCIENCES AND DISORDERS": "CSD",
    "HOSPITALITY MANAGEMENT": "HM",
    "RECREATION, PARK, AND TOURISM MANAGEMENT": "RPTM",
    "RECREATION PARK AND TOURISM MANAGEMENT": "RPTM",
    "SYSTEMS NEUROSCIENCE": "NROSCI",
    "ELEMENTARY AND EARLY CHILDHOOD EDUCATION": "ELED",
    "SPECIAL EDUCATION": "SPLED",
    "SECONDARY EDUCATION": "SECED",
    "REHABILITATION AND HUMAN SERVICES": "RHS",
    "EDUCATION AND PUBLIC POLICY": "EDPP",
    "MIDDLE LEVEL EDUCATION": "MLED",
    "WORKFORCE EDUCATION AND DEVELOPMENT": "WFED",
    "ARCHITECTURE": "ARCHBARCH",
    "ART HISTORY": "ARTH",
    "GRAPHIC DESIGN": "GD",
    "ART EDUCATION": "AED",
    "LANDSCAPE ARCHITECTURE": "LARCH",
    "DIGITAL MULTIMEDIA DESIGN": "DMD",
    "PROFESSIONAL PHOTOGRAPHY": "PPHOTO",
    "DIGITAL ARTS AND MEDIA DESIGN": "DAMD",
    "THEATRE": "THEA",
    "MUSIC": "MUSIC",
    "MUSIC EDUCATION": "MUSED",
    "ACTING": "ACTING",
    "MUSICAL THEATRE": "MUSTHEA",
    "STAGE MANAGEMENT": "THEABFA",
    "MUSIC PERFORMANCE": "MUSICBM",
    "MUSIC TECHNOLOGY": "MUSTECH",
    "HISTORY": "HIST",
    "CRIMINOLOGY": "CRIM",
    "SOCIOLOGY": "SOCBA",
    "PHILOSOPHY": "PHILBA",
    "ANTHROPOLOGY": "ANTH",
    "LINGUISTICS": "LING",
    "COMMUNICATION ARTS AND SCIENCES": "CASBA",
    "AFRICAN AMERICAN STUDIES": "AFAM",
    "INTERNATIONAL POLITICS": "INTPOL",
    "ORGANIZATIONAL LEADERSHIP": "OLEAD",
    "LABOR AND HUMAN RESOURCES": "LHR",
    "SPANISH": "SPANBA",
    "FRENCH": "FRENCHBA",
    "GERMAN": "GERBA",
    "COMPARATIVE LITERATURE": "CMLIT",
    "SOCIAL DATA ANALYTICS": "SODA",
    "ITALIAN": "ITBA",
    "RUSSIAN": "RUSBA",
    "WOMEN'S, GENDER, AND SEXUALITY STUDIES": "WMNSTBA",
    "WOMENS GENDER AND SEXUALITY STUDIES": "WMNSTBA",
    "CLASSICS AND ANCIENT MEDITERRANEAN STUDIES": "CAMS",
    "JEWISH STUDIES": "JST",
    "CHINESE": "CHNSBA",
    "ECONOMICS BA": "ECONBA",
    "ECONOMICS B A": "ECONBA",
    "POLITICAL SCIENCE BA": "PLSCBA",
    "POLITICAL SCIENCE B A": "PLSCBA",
    "PHILOSOPHY BS": "PHILBS",
    "PHILOSOPHY B S": "PHILBS",
    "SOCIOLOGY BS": "SOCBS",
    "SOCIOLOGY B S": "SOCBS",
    "CRIMINOLOGY BS": "CRIMBS",
    "CRIMINOLOGY B S": "CRIMBS",
    "FRENCH BS": "FRENCHBS",
    "FRENCH B S": "FRENCHBS",
    "GERMAN BS": "GERBS",
    "GERMAN B S": "GERBS",
    "ITALIAN BS": "ITBS",
    "ITALIAN B S": "ITBS",
    "SPANISH BS": "SPANBS",
    "SPANISH B S": "SPANBS",
    "ARCHITECTURAL ENGINEERING": "AE",
    "AE": "AE",
    "ARTIFICIAL INTELLIGENCE ENGINEERING": "AIE",
    "AI ENGINEERING": "AIE",
    "AIE": "AIE",
    "DATA SCIENCES ENGINEERING": "DTSCE",
    "DATA SCIENCES B S ENGINEERING": "DTSCE",
    "DTSCE": "DTSCE",
    "DATA SCIENCES INFORMATION SCIENCES AND TECHNOLOGY": "DATSC",
    "APPLIED DATA SCIENCES": "DATSC",
    "DATSC": "DATSC",
    "COMMUNICATION ARTS AND SCIENCES BS": "CASBS",
    "COMMUNICATION ARTS AND SCIENCES B S": "CASBS",
    "CASBS": "CASBS",
    "GEOGRAPHY BA": "GEOBA",
    "GEOGRAPHY B A": "GEOBA",
    "GEOBA": "GEOBA",
    "MATHEMATICS BA": "MATHBA",
    "MATHEMATICS B A": "MATHBA",
    "MATHBA": "MATHBA",
    "ORGANIZATIONAL LEADERSHIP BS": "OLEADBS",
    "ORGANIZATIONAL LEADERSHIP B S": "OLEADBS",
    "OLEADBS": "OLEADBS",
    "WOMEN'S, GENDER, AND SEXUALITY STUDIES BS": "WMNSTBS",
    "WOMENS GENDER AND SEXUALITY STUDIES BS": "WMNSTBS",
    "WMNSTBS": "WMNSTBS",
    "APPLIED LINGUISTICS": "APLNGBA",
    "APLNGBA": "APLNGBA",
    "JAPANESE": "JAPNSBA",
    "JAPNSBA": "JAPNSBA",
    "KOREAN": "KORBA",
    "KORBA": "KORBA",
    "AFRICAN STUDIES": "AFRSTBA",
    "AFRSTBA": "AFRSTBA",
    "SUSTAINABILITY SOCIETY AND ENVIRONMENTAL GEOGRAPHY": "SSEVG",
    "SSEVG": "SSEVG",
    "ANTHROPOLOGICAL SCIENCE": "ANTHSBS",
    "ANTHSBS": "ANTHSBS",
    "LANDSCAPE CONTRACTING": "LSCPE",
    "LSCPE": "LSCPE",
}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024  # requests are small JSON payloads
CORS(app, origins=CORS_ORIGINS)

_RAG_INDEX = None


@app.errorhandler(Exception)
def handle_error(e):  # always return JSON, never a stack-trace page
    code = getattr(e, "code", 500)
    message = str(e) if FLASK_DEBUG else "Internal server error."
    if code != 500:
        message = getattr(e, "description", message)
    return jsonify({"error": message}), code


@app.get("/api/health")
def api_health():
    return jsonify({"status": "ok"})


@app.get("/api/campuses")
def api_campuses():
    return jsonify({"campuses": engine.PSU_CAMPUSES, "default": engine.DEFAULT_CAMPUS})


@app.get("/api/degree-plans")
def api_degree_plans():
    campus = request.args.get("campus")
    return jsonify({"plans": engine.list_degree_plans(campus)})


@app.get("/api/minor-plans")
def api_minor_plans():
    campus = request.args.get("campus")
    return jsonify({"minors": engine.list_minor_plans(campus)})


@app.post("/api/transfer-credit")
def api_transfer_credit():
    """PA community colleges near the student, ranked by how many of their
    requested PSU courses transfer there (then by distance). The equivalency
    data itself isn't scraped yet (LionPATH's Transfer Credit Tool has no
    public API — see docs/EXPANSION_PLAN.md §5), so courses_covered_count is
    currently always 0 and every response includes a note saying so; the
    distance ranking itself is real and already useful on its own.
    """
    payload = request.get_json(force=True, silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"error": "Request body must be a JSON object."}), 400

    zip_code = str(payload.get("zip_code") or "").strip()
    if not zip_code:
        return jsonify({"error": "'zip_code' is required."}), 400

    courses_in = payload.get("courses") or []
    if not isinstance(courses_in, list):
        return jsonify({"error": "'courses' must be a list of course codes."}), 400
    courses = [engine.norm_code(str(c)) for c in courses_in if str(c).strip()]

    if not tc.zip_to_coords(zip_code):
        return jsonify({
            "error": f"'{zip_code}' isn't in the supported area yet — Pennsylvania zip "
                     "codes only for now. Nationwide coverage is planned.",
        }), 400

    cache = tc.load_equivalency_cache()
    ranked = tc.rank_colleges_for_courses(zip_code, courses, cache=cache) if courses \
        else tc.nearest_colleges(zip_code)

    return jsonify({
        "zipCode": zip_code,
        "courses": courses,
        "colleges": ranked,
        "equivalencyDataAvailable": bool(cache),
        "note": None if cache else (
            "Transfer-equivalency data hasn't been collected yet — showing distance only. "
            "Once available, colleges will be ranked by how many of your courses transfer there."
        ),
    })


# ----------------------------
# Ollama (optional, explanation only)
# ----------------------------

def ollama_chat(prompt: str, model: str = OLLAMA_MODEL, timeout_s: int = OLLAMA_TIMEOUT_S) -> str:
    prompt = (prompt or "").strip()
    if not prompt:
        return ""
    base = OLLAMA_HOST.rstrip("/")
    body = {
        "model": model,
        "stream": False,
        "options": {"temperature": 0.2, "num_predict": 350},
    }
    system = (
        "You are a friendly Penn State academic advisor. "
        "You are given verified facts computed by a planning engine. "
        "Answer the student's question using ONLY those facts. "
        "Never invent courses or change the recommended list. Be concise."
    )
    # Preferred: /api/chat
    try:
        data = requests.post(
            f"{base}/api/chat",
            json={
                **body,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
            },
            timeout=timeout_s,
        ).json()
        text = (data.get("message") or {}).get("content", "") or ""
        if text:
            return text
    except Exception:
        pass
    # Fallback: /api/generate
    data = requests.post(
        f"{base}/api/generate",
        json={**body, "prompt": f"{system}\n\n{prompt}"},
        timeout=timeout_s,
    ).json()
    return data.get("response", "") or ""


# ----------------------------
# RAG helpers
# ----------------------------

def get_rag_index():
    global _RAG_INDEX
    if _RAG_INDEX is None and load_index and os.path.exists(RAG_INDEX_PATH):
        try:
            _RAG_INDEX = load_index(RAG_INDEX_PATH)
        except Exception:
            _RAG_INDEX = None
    return _RAG_INDEX


def retrieve_rag_context(prompt: str, dept: str, k: int = 4) -> str:
    if not prompt or not top_k_chunks or not format_context:
        return ""
    idx = get_rag_index()
    if idx is None:
        return ""
    try:
        hits = top_k_chunks(idx, query=prompt, k=k, dept=dept)
        return format_context(hits)
    except Exception:
        return ""


# ----------------------------
# Prompt parsing
# ----------------------------

def _extract_major_from_prompt(prompt: str) -> Optional[str]:
    """Find the major alias that appears earliest in the message.

    Course codes ("MATH 140", "STAT 200"...) can collide with short major
    aliases ("MATH", "STAT"); picking dict-iteration order instead of text
    position let a course mention anywhere in the message override an
    explicit "I am a premed student" earlier in the same sentence. Leftmost
    match wins instead, matching how a reader would parse the sentence.
    Ties (same start position) go to the longer alias, so a specific phrase
    like "business analytics" beats the generic "business" it contains.

    A short alias immediately followed by a course number ("STAT 200",
    "CHEM 110") is a course mention, not a major statement — skip those, or
    a routine "I took STAT 200" would silently reassign the student's major
    the same way a real course-code prefix collision (MATH/STAT/CHEM/BIOL/
    NURS/FIN/...) does for every one of these short, subject-prefix aliases.
    """
    raw = (prompt or "").upper()
    best: Optional[Tuple[int, int, str]] = None  # (start, -length, dept)
    for alias, dept in _MAJOR_ALIASES.items():
        for m in re.finditer(rf"\b{re.escape(alias)}\b", raw):
            if re.match(r"\s*-?\s*\d", raw[m.end():]):
                continue  # "STAT 200" / "CHEM-110" — a course code, not a major
            candidate = (m.start(), -len(alias), dept)
            if best is None or candidate < best:
                best = candidate
            break  # first valid occurrence of this alias is enough
    return best[2] if best else None


_TAKEN_TRIGGERS = [
    "i took", "i've taken", "i have taken", "i completed", "completed",
    "passed", "finished", "already took", "already taken", "i have credit",
    "transfer credit", "ap credit", "got credit",
]

_REMOVAL_TRIGGERS = [
    "did not take", "didn't take", "have not taken", "haven't taken",
    "have not completed", "haven't completed", "did not complete",
    "didn't complete", "not completed", "remove", "dropped", "i drop",
    "never took",
]


def _split_clauses(prompt: str) -> List[str]:
    return [c.strip() for c in re.split(r"[.;!?\n]|,?\s+but\s+", prompt or "") if c.strip()]


_START_VERB_RE = re.compile(r"\b(started|start|began|begin|enrolled|enroll)\b", re.IGNORECASE)
_COLLEGE_WORD_RE = re.compile(r"\b(college|school|university|psu|penn\s*state)\b", re.IGNORECASE)
_FOUR_DIGIT_YEAR_RE = re.compile(r"\b(20\d{2})\b")


def _extract_start_year_from_prompt(prompt: str) -> Optional[int]:
    """Detect a stated start year like "I started school in 2022" or
    "I began at Penn State in 2023" — lets a mid-chat correction override
    the dropdown's start year even if the student never touched it.

    Requires a start-verb, a college-word, and a year all in the SAME
    clause so casual course-taking language ("I started CMPSC 131 in
    Fall 2022") doesn't get misread as a college start date.
    """
    for clause in _split_clauses(prompt):
        if not (_START_VERB_RE.search(clause) and _COLLEGE_WORD_RE.search(clause)):
            continue
        m = _FOUR_DIGIT_YEAR_RE.search(clause)
        if m:
            year = int(m.group(1))
            if 2015 <= year <= 2035:
                return year
    return None


def parse_summer_unavailable(prompt: str, catalog: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Courses the student says aren't offered in summer.

    Matches clauses like "CMPSC 360 is not available over the summer" /
    "no summer section for MATH 230" / "can't take STAT 318 in summer".
    """
    found: List[Dict[str, Any]] = []
    seen = set()
    for clause in _split_clauses(prompt):
        low = clause.lower()
        if "summer" not in low:
            continue
        if not re.search(r"\bnot\b|n't\b|\bno\b|\bunavailable\b|\bcannot\b", low):
            continue
        matched, _ = engine.match_courses_in_text(clause, catalog)
        for m in matched:
            if m["code"] not in seen:
                seen.add(m["code"])
                found.append(m)
    return found


def parse_completion_changes(
    prompt: str, catalog: Dict[str, Any]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[str]]:
    """Per-clause parsing: returns (added, removed, unmatched mentions).

    A clause with removal wording removes its courses; a clause with
    completion wording adds its courses. Removal wins within one clause
    ("I did not take X" contains 'take' but must not add X).
    """
    added: List[Dict[str, Any]] = []
    removed: List[Dict[str, Any]] = []
    unmatched: List[str] = []
    seen_add, seen_rm = set(), set()

    for clause in _split_clauses(prompt):
        low = clause.lower()
        is_removal = any(t in low for t in _REMOVAL_TRIGGERS)
        is_taken = any(t in low for t in _TAKEN_TRIGGERS)
        if not (is_removal or is_taken):
            continue
        matched, unm = engine.match_courses_in_text(clause, catalog)
        unmatched.extend(u for u in unm if u not in unmatched)
        for m in matched:
            if is_removal:
                if m["code"] not in seen_rm:
                    seen_rm.add(m["code"])
                    removed.append(m)
            else:
                if m["code"] not in seen_add:
                    seen_add.add(m["code"])
                    added.append(m)

    return added, removed, unmatched


# ----------------------------
# Serialization
# ----------------------------

def _camel_category(cat: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "doneItems": cat["done_items"],
        "totalItems": cat["total_items"],
        "creditsDone": cat["credits_done"],
        "totalCredits": cat["total_credits"],
        "percent": cat["percent"],
    }


def _course_card(code: str, catalog: Dict[str, Any], fallback_name: Optional[str] = None) -> Dict[str, Any]:
    course = catalog.get(engine.norm_code(code))
    prereqs: List[str] = []
    if course:
        for group in course.prereq_groups:
            prereqs.append(" or ".join(sorted(group)))
    return {
        "id": engine.norm_code(code),
        "name": course.name if course else (fallback_name or code),
        "description": (course.description or "") if course else "",
        "prerequisites": prereqs,
    }


def _pick_card(pick: Dict[str, Any], catalog: Dict[str, Any]) -> Dict[str, Any]:
    if pick.get("code"):
        # Gen Ed picks (and any other course from a department we haven't
        # scraped a catalog for) carry their real title on the pick itself
        # — fall back to it instead of the bare course code.
        card = _course_card(pick["code"], catalog, fallback_name=pick.get("name"))
    else:
        card = {"id": "", "name": pick["name"], "description": pick["reason"], "prerequisites": []}
    card["credits"] = pick.get("credits")
    card["reason"] = pick.get("reason", "")
    card["flowchartSemester"] = pick.get("flowchart_semester")
    card["type"] = pick.get("type", "course")
    card["etm"] = pick.get("etm", False)
    card["unlocks"] = pick.get("unlocks", 0)
    return card


# ----------------------------
# Deterministic advisor reply
# ----------------------------

def _build_reply_text(
    major: str,
    catalog_year: Any,
    added: List[Dict[str, Any]],
    removed: List[Dict[str, Any]],
    unmatched: List[str],
    progress: Dict[str, Any],
    next_sem: Dict[str, Any],
    ranked: List[Dict[str, Any]],
    plan_warnings: List[str],
    summer_flagged: Optional[List[Dict[str, Any]]] = None,
    goal: Optional[Dict[str, Any]] = None,
    next_term_label: str = "",
    chat_start_year: Optional[int] = None,
    bulk_note: Optional[str] = None,
    opener: str = "",
) -> str:
    lines: List[str] = []

    if opener:
        lines.append(opener)
    if chat_start_year:
        lines.append(
            f"Got it — switched to the {chat_start_year} requirements "
            f"(the catalog year you started college)."
        )
    if bulk_note:
        lines.append(f"Got it — marked {bulk_note} as already completed.")
    if added:
        lines.append("Recorded as completed:")
        for m in added:
            lines.append(f"  • {m['code']} — {m['name']}")
    if removed:
        lines.append("Removed from completed:")
        for m in removed:
            lines.append(f"  • {m['code']} — {m['name']}")
    if summer_flagged:
        lines.append("Noted as NOT available in summer (plan adjusted):")
        for m in summer_flagged:
            lines.append(f"  • {m['code']} — {m['name']}")
    if unmatched:
        lines.append(f"Couldn't match: {', '.join(unmatched[:6])} (check the course code).")

    if goal:
        status = "on track" if goal.get("met") else "NOT currently on track"
        lines.append(
            f"Graduation goal: {goal['grad_years']} years (by {goal['deadline']}) — {status}."
        )

    lines.append(
        f"Progress on the {major} {catalog_year} plan: "
        f"{progress['done_items']}/{progress['total_items']} requirements "
        f"({progress['credits_done']}/{progress['total_credits']} credits)."
    )
    if progress.get("extra_courses"):
        lines.append(
            "Completed courses not on the plan (may count as electives): "
            + ", ".join(progress["extra_courses"][:8])
        )

    if next_sem["courses"]:
        lines.append("")
        term_name = next_term_label or "next semester"
        lines.append(f"Recommended for {term_name} ({next_sem['total_credits']} credits):")
        for p in next_sem["courses"]:
            label = p["code"] or p["name"]
            lines.append(f"  • {label} ({p['credits']:g} cr) — {p['reason']}")
    else:
        lines.append("All flowchart requirements are satisfied — you're set to graduate! 🎓")

    if ranked:
        lines.append("")
        lines.append("Top ranked eligible courses (weighted):")
        for r in ranked[:5]:
            lines.append(f"  • {r['code']} (score {r['score']}, {r['source']}) — {r['reasons'][0]}")

    if next_sem.get("blocked"):
        lines.append("")
        lines.append("Still locked:")
        for b in next_sem["blocked"][:3]:
            reasons = []
            if b.get("missing"):
                reasons.append(f"needs: {'; '.join(b['missing'])}")
            if b.get("excludedBy"):
                reasons.append(
                    f"can't be taken/counted — you've already completed {', '.join(b['excludedBy'])}"
                )
            lines.append(f"  • {b['code']} — {'; '.join(reasons)}")

    for w in plan_warnings:
        lines.append(f"⚠ {w}")

    return "\n".join(lines)


# Rotation for the deterministic reply's opening line. Index 0 is
# deliberately "" (no opener line) — that's today's exact behavior, so a
# request that never sends turn_index (every pre-existing caller) sees
# byte-identical output. Rotation only kicks in from turn 2 of a
# conversation onward, which is also the actual complaint: turn 1 has
# nothing to vary against yet.
_OPENERS = [
    "",
    "Here's where things stand:",
    "Updated plan:",
    "OK — here's the latest:",
    "Alright, here's what I've got:",
]


def _pick_opener(turn_index: int) -> str:
    if turn_index <= 0:
        return _OPENERS[0]
    return _OPENERS[1 + (turn_index - 1) % (len(_OPENERS) - 1)]


def _build_phrase_prompt(
    question: str, facts: str, rag_context: str, recent_reply_excerpt: str = "",
) -> str:
    context_block = f"\nAdvising notes (background):\n{rag_context}\n" if rag_context else ""
    anti_repeat = ""
    if recent_reply_excerpt.strip():
        anti_repeat = (
            "\nYour own previous reply in this conversation started with:\n"
            f"\"{recent_reply_excerpt.strip()}\"\n"
            "Vary your opening this time — do not reuse that phrasing or sentence structure, "
            "and don't restate facts that clearly haven't changed since then.\n"
        )
    return (
        "Verified planning facts:\n"
        f"{facts}\n"
        f"{context_block}"
        f"{anti_repeat}\n"
        f"Student question: {question}\n\n"
        "Write a short, friendly advisor reply (max ~180 words) grounded ONLY in the facts above. "
        "Keep every course code exactly as written. Do not add or remove recommendations."
    )


def _llm_phrase_reply(
    question: str, facts: str, rag_context: str, recent_reply_excerpt: str = "",
) -> Optional[str]:
    if not USE_OLLAMA or not question.strip():
        return None
    try:
        prompt = _build_phrase_prompt(question, facts, rag_context, recent_reply_excerpt)
        text = ollama_chat(prompt)
        return text.strip() or None
    except Exception:
        return None


# ----------------------------
# Main API
# ----------------------------

@app.post("/api/plan")
def api_plan():
    payload = request.get_json(force=True, silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"error": "Request body must be a JSON object."}), 400

    prompt = str(payload.get("prompt") or "").strip()[:4000]
    recent_reply = str(payload.get("recent_reply") or "")[:400]
    try:
        turn_index = int(payload.get("turn_index") or 0)
    except (TypeError, ValueError):
        turn_index = 0
    payload_major = str(payload.get("major") or payload.get("dept") or "").strip().upper()
    catalog_year = payload.get("catalog_year")
    completed_in = payload.get("completed") or []
    if not isinstance(completed_in, list):
        return jsonify({"error": "'completed' must be a list of course codes."}), 400
    # Slot ids (non-course items like a generic "GEN ED" box) that a prior
    # bulk-completion phrase ("I'm a junior") marked done. Unlike course
    # codes, these came from a one-time prompt, not `completed[]`, so a
    # later settings-only request (empty prompt — toggling summer, grad
    # years, majors, minors, ...) would otherwise silently forget them and
    # make previously-satisfied requirements look unmet again. The client
    # persists and re-sends whatever this endpoint last echoed back.
    consumed_slot_ids_in = payload.get("consumed_slot_ids") or []
    if not isinstance(consumed_slot_ids_in, list):
        return jsonify({"error": "'consumed_slot_ids' must be a list of integers."}), 400
    try:
        consumed_slot_ids_in = {int(i) for i in consumed_slot_ids_in}
    except (TypeError, ValueError):
        return jsonify({"error": "'consumed_slot_ids' must be a list of integers."}), 400
    max_credits = payload.get("max_credits")
    if max_credits is not None and not isinstance(max_credits, (int, float)):
        return jsonify({"error": "'max_credits' must be a number."}), 400

    # Year planning inputs
    try:
        start_year = int(payload.get("start_year") or 0) or None
        grad_years = int(payload.get("grad_years") or 4)
    except (TypeError, ValueError):
        return jsonify({"error": "'start_year' and 'grad_years' must be numbers."}), 400
    grad_years = min(max(grad_years, 1), 8)
    allow_summer = bool(payload.get("allow_summer"))
    summer_unavailable_in = payload.get("summer_unavailable") or []
    if not isinstance(summer_unavailable_in, list):
        return jsonify({"error": "'summer_unavailable' must be a list."}), 400

    # The chat message is the source of truth for the major when it names one.
    major = _extract_major_from_prompt(prompt) or payload_major or "CMPSC"

    # A stated start year ("oh, I started school in 2022") wins outright —
    # even over an already-synced dropdown value — since the student is
    # actively correcting it.
    chat_start_year = _extract_start_year_from_prompt(prompt)
    if chat_start_year:
        start_year = chat_start_year
        catalog_year = chat_start_year

    # Requirements follow the catalog year the student STARTED college.
    plan = engine.load_degree_plan(major, catalog_year or start_year)
    if plan is None:
        available = engine.list_degree_plans()
        fallback = available[0] if available else None
        if fallback:
            plan = engine.load_degree_plan(fallback["major"], fallback["catalog_year"])
        if plan is None:
            return jsonify({"error": f"No degree plan available for {major}."}), 404

    # Second/third/... major, minors — entirely opt-in. Absent every field
    # (any request that doesn't name them), merge_plans hands `plan` back
    # unchanged, so this can never affect a single-major request.
    second_major_code = str(payload.get("second_major") or "").strip().upper() or None
    additional_majors_in = payload.get("additional_majors") or []
    if not isinstance(additional_majors_in, list):
        return jsonify({"error": "'additional_majors' must be a list of major codes."}), 400
    minors_in = payload.get("minors") or []
    if not isinstance(minors_in, list):
        return jsonify({"error": "'minors' must be a list of minor codes."}), 400
    if second_major_code or additional_majors_in or minors_in:
        second_plan = (
            engine.load_degree_plan(second_major_code, catalog_year or start_year)
            if second_major_code else None
        )
        additional_plans = [
            p for code in additional_majors_in
            if (p := engine.load_degree_plan(str(code).strip().upper(), catalog_year or start_year))
        ]
        minor_plans = [
            p for code in minors_in
            if (p := engine.load_minor_plan(str(code).strip().upper(), catalog_year or start_year))
        ]
        plan = engine.merge_plans(
            plan, second_major=second_plan, additional_majors=additional_plans, minors=minor_plans,
        )

    catalog = engine.load_merged_catalog(plan.get("departments", [major]))

    # Slot ids only mean something against the plan they were computed for —
    # merge_plans renumbers ids whenever majors/minors change, so a stale id
    # from a since-changed plan shape must be dropped rather than silently
    # (and wrongly) reused against a different item.
    real_slot_ids = {item["id"] for _, item in engine._iter_plan_items(plan)}
    bulk_slot_ids: set = consumed_slot_ids_in & real_slot_ids

    # --- interpret the chat message (add AND remove, summer availability) ---
    added, removed, unmatched = parse_completion_changes(prompt, catalog)
    summer_flagged = parse_summer_unavailable(prompt, catalog)

    # Bulk/inverse completion ("I'm a junior", "everything except my last
    # year") — must run on the RAW prompt, not a parse_completion_changes
    # clause, since that splitter already breaks "everything but X" in two.
    # Slot ids accumulate across requests (see consumed_slot_ids_in above) —
    # a fresh bulk phrase adds to, rather than replaces, what's already
    # marked done, so restating "I'm a junior" a second time is a no-op
    # rather than a regression.
    bulk = engine.detect_bulk_completion(prompt, plan)
    bulk_codes: set = set()
    if bulk:
        bulk_exclude = set()
        if "except" in prompt.lower() or "but" in prompt.lower():
            named, _ = engine.match_courses_in_text(prompt, catalog)
            bulk_exclude = {m["code"] for m in named}
        new_bulk_codes, new_bulk_slot_ids = engine.apply_bulk_completion(
            plan, catalog, bulk["semesters_done"], excluded_codes=bulk_exclude,
        )
        bulk_codes = new_bulk_codes
        bulk_slot_ids |= new_bulk_slot_ids

    completed = {engine.norm_code(c) for c in completed_in if str(c).strip()}
    completed |= {m["code"] for m in added} | bulk_codes
    completed -= {m["code"] for m in removed}
    completed_sorted = sorted(completed)

    summer_unavailable = {engine.norm_code(c) for c in summer_unavailable_in if str(c).strip()}
    summer_unavailable |= {m["code"] for m in summer_flagged}
    summer_unavailable_sorted = sorted(summer_unavailable)

    # --- deterministic planning ---
    full_plan = engine.build_full_plan(
        plan, catalog, completed,
        start_year=start_year,
        grad_years=grad_years,
        allow_summer=allow_summer,
        summer_unavailable=summer_unavailable,
        initial_consumed_slots=bulk_slot_ids or None,
    )
    # The next term to plan is the first simulated term (summer-aware).
    first_term = full_plan["terms"][0] if full_plan["terms"] else None
    next_sem = engine.recommend_semester(
        plan, catalog, completed,
        consumed_slots=bulk_slot_ids or None,
        max_credits=max_credits or (engine.SUMMER_MAX_CREDITS if first_term and first_term["is_summer"] else None),
        exclude_codes=summer_unavailable if first_term and first_term["is_summer"] else None,
    )
    if first_term:
        next_sem["courses"] = first_term["courses"]
        next_sem["total_credits"] = first_term["total_credits"]
    progress = next_sem["progress"]
    mermaid = engine.build_mermaid(plan, catalog, completed, next_sem["courses"])
    unlock_map = engine.build_unlock_map(plan, catalog, completed)
    semester_flowchart = engine.build_semester_flowchart(catalog, completed, full_plan["terms"])
    low_cost_minors = engine.suggest_low_cost_minors(
        plan, completed, catalog_year or start_year, exclude_minors=set(minors_in),
    )

    # --- weighted ranking of all eligible courses ---
    interests = engine.extract_interests(prompt)
    ranked = engine.score_recommendations(plan, catalog, completed, interests=interests)
    tips = engine.default_tips(progress, next_sem["blocked"])

    # --- prereq graph (vis-network compatibility) ---
    try:
        graph_nodes, graph_edges, _ = build_progression_graph(catalog, completed, max_depth=2)
    except Exception:
        graph_nodes, graph_edges = [], []
    graph = {"nodes": graph_nodes, "edges": graph_edges}

    # --- reply text: deterministic facts, optionally rephrased by Ollama + RAG notes ---
    facts = _build_reply_text(
        plan.get("major", major), plan.get("catalog_year", ""),
        added, removed, unmatched,
        progress, next_sem, ranked, full_plan["warnings"],
        summer_flagged=summer_flagged,
        goal=full_plan.get("goal"),
        next_term_label=first_term["label"] if first_term else "",
        chat_start_year=chat_start_year,
        bulk_note=bulk["description"] if bulk else None,
        opener=_pick_opener(turn_index),
    )
    rag_response = facts
    if prompt:
        rag_context = retrieve_rag_context(prompt, dept=plan.get("major", major))
        phrased = _llm_phrase_reply(prompt, facts, rag_context, recent_reply_excerpt=recent_reply)
        if phrased:
            rag_response = phrased

    # --- serialize ---
    recommendations = [
        {
            "name": r["code"],
            "reason": " ".join(r["reasons"]),
            "credits": r["credits"],
            "score": r["score"],
            "source": r["source"],
            "title": r["name"],
            "flowchartSemester": r["flowchart_semester"],
        }
        for r in ranked
    ]

    flowchart_cards = [_course_card(c, catalog) for c in completed_sorted]
    flowchart_cards += [_pick_card(p, catalog) for p in next_sem["courses"]]

    full_plan_out = {
        "terms": [
            {
                "index": t["index"],
                "label": t["label"],
                "isSummer": t["is_summer"],
                "withinGoal": t["within_goal"],
                "totalCredits": t["total_credits"],
                "belowFullTime": t["below_full_time"],
                "aboveFlatRate": t["above_flat_rate"],
                "courses": [_pick_card(p, catalog) for p in t["courses"]],
            }
            for t in full_plan["terms"]
        ],
        "warnings": full_plan["warnings"],
        "goal": {
            "startYear": full_plan["goal"]["start_year"],
            "gradYears": full_plan["goal"]["grad_years"],
            "deadline": full_plan["goal"]["deadline"],
            "allowSummer": full_plan["goal"]["allow_summer"],
            "met": full_plan["goal"]["met"],
        },
    }

    eligible_codes = [p["code"] for p in next_sem["courses"] if p["code"]]
    matched_payload = {
        "courses": added,
        "removed": removed,
        "summerUnavailable": summer_flagged,
        "unmatched": unmatched,
        "treatedAsCompleted": bool(added),
    }
    state = {
        "dept": plan.get("major", major),
        "completed": completed_sorted,
        "startYear": full_plan["goal"]["start_year"],
        "gradYears": grad_years,
        "allowSummer": allow_summer,
        "summerUnavailable": summer_unavailable_sorted,
        # Non-course items (e.g. a generic "GEN ED" box) that a bulk-
        # completion phrase marked done — the client re-sends this as
        # consumed_slot_ids on every later request so a settings-only
        # change (no new prompt) doesn't forget it. See consumed_slot_ids_in.
        "consumedSlotIds": sorted(bulk_slot_ids),
    }

    course_plan = {
        "major": plan.get("major", major),
        "catalogYear": plan.get("catalog_year"),
        "dept": plan.get("major", major),
        "completed": completed_sorted,
        "eligible": eligible_codes,
        "graph": graph,
        "rag_response": rag_response,
        "flowchart": flowchart_cards,
        "recommendations": recommendations,
        "tips": tips,
        "llm_flowchart": mermaid,
        "unlockMap": unlock_map,
        "semesterFlowchart": semester_flowchart,
        "lowCostMinors": low_cost_minors,
        "matched": matched_payload,
        "nextSemester": {
            "label": first_term["label"] if first_term else "",
            "isSummer": bool(first_term and first_term["is_summer"]),
            "totalCredits": next_sem["total_credits"],
            "belowFullTime": bool(first_term and first_term["below_full_time"]),
            "aboveFlatRate": bool(first_term and first_term["above_flat_rate"]),
            "courses": [_pick_card(p, catalog) for p in next_sem["courses"]],
            "blocked": next_sem["blocked"],
        },
        "fullPlan": full_plan_out,
        "progress": {
            "doneItems": progress["done_items"],
            "totalItems": progress["total_items"],
            "creditsDone": progress["credits_done"],
            "totalCredits": progress["total_credits"],
            "extraCourses": progress["extra_courses"],
            "byCategory": {k: _camel_category(v) for k, v in progress["by_category"].items()},
        },
    }

    course_plan["state"] = state

    return jsonify({
        # legacy top-level keys (state is the client's source of truth)
        "state": state,
        "dept": course_plan["dept"],
        "completed": completed_sorted,
        "eligible": eligible_codes,
        "graph": graph,
        "rag_response": rag_response,
        "semantic_results": [],
        "search_results": [],
        "why_not_answer": "",
        "llm_flowchart": mermaid,
        # structured payload used by the frontend
        "coursePlan": course_plan,
    })


if __name__ == "__main__":
    # 5001 by default: macOS AirPlay Receiver squats on port 5000.
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5001")), debug=FLASK_DEBUG)
