from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Tuple

from io import BytesIO

import requests
from flask import Flask, jsonify, request
from flask_cors import CORS
from pypdf import PdfReader

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

OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY", "").strip()
# Cloud mode (OLLAMA_API_KEY set): talk to ollama.com's hosted models
# instead of a local Ollama process, so the backend can run on a server
# with no LLM of its own. Local dev is untouched -- without the key,
# every default here is exactly what it was before this existed.
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "https://ollama.com" if OLLAMA_API_KEY else "http://127.0.0.1:11434")
# gemma4:cloud specifically, not one of the "reasoning" cloud models
# (gpt-oss/deepseek/qwen/kimi/glm/nemotron/minimax) -- those put their
# output in a separate "thinking" field and can leave content/response
# empty if num_predict runs out before they finish reasoning, which
# silently degrades every reply to the plain deterministic fallback.
# Confirmed live: gpt-oss:20b-cloud did exactly this on a real facts
# prompt. gemma4:cloud answers directly with no thinking field.
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma4:cloud" if OLLAMA_API_KEY else "llama3:latest")
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
# Every other endpoint is small JSON, but /api/parse-transcript accepts an
# uploaded PDF -- transcripts are text-heavy (not images), so even a long
# one is normally well under 1MB, but this leaves real headroom.
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024
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


@app.post("/api/explore-majors")
def api_explore_majors():
    """For a student marked Undecided — no degree plan exists yet, so none
    of the scheduling engine runs here. Pure conversation, grounded against
    the real major list (never invents one), that asks narrowing questions
    and suggests real majors once it has enough to go on."""
    payload = request.get_json(force=True, silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"error": "Request body must be a JSON object."}), 400

    prompt = str(payload.get("prompt") or "").strip()[:2000]
    campus = str(payload.get("campus") or "").strip() or None
    recent_reply = str(payload.get("recent_reply") or "")[:400]
    try:
        turn_index = int(payload.get("turn_index") or 0)
    except (TypeError, ValueError):
        turn_index = 0

    majors_summary = _real_majors_summary(campus)
    reply = _llm_explore_majors_reply(prompt, majors_summary, recent_reply, turn_index)
    if not reply:
        reply = _explore_majors_fallback(majors_summary, turn_index)

    return jsonify({"reply": reply})


_TRANSCRIPT_COURSE_HEADER_RE = re.compile(r"^[ \t]*course\b", re.IGNORECASE | re.MULTILINE)


def _extract_transcript_course_text(text: str) -> str:
    """Anchor extraction on the literal "Course" column header instead of
    scanning the whole PDF indiscriminately.

    A real transcript export lists courses in a table under a "Course"
    heading -- often repeated once per term. Segmenting at each occurrence
    and only matching course codes within those segments (rather than the
    full document) keeps stray numbers elsewhere on the page -- student
    ID, page numbers, phone numbers -- from ever reaching the course-code
    matcher in the first place, instead of relying on the matcher to
    reject them after the fact.

    Anchored on "Course" at the START of a line specifically, not just
    the word appearing anywhere -- a course's own title can legitimately
    contain the word "course" (e.g. "Intro to Course Design"), and that
    must not be mistaken for a new header and silently cut off whatever
    real course code preceded it on an earlier line.

    Falls back to the full text when "Course" never appears anywhere, so
    an unusually-formatted document still gets best-effort matching
    rather than silently returning nothing.
    """
    matches = list(_TRANSCRIPT_COURSE_HEADER_RE.finditer(text))
    if not matches:
        return text
    segments = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        segments.append(text[start:end])
    return "\n".join(segments)


@app.post("/api/parse-transcript")
def api_parse_transcript():
    """Upload a PDF transcript instead of typing courses one by one.

    Extracts the PDF's text, anchors on the "Course" column header via
    _extract_transcript_course_text (real transcripts list courses in a
    table under that heading), then hands the anchored text to the exact
    same match_courses_in_text() real-catalog matcher chat-typed course
    mentions already go through -- a transcript is just a different INPUT
    PATH into the same matching, not a separate parser with its own drift
    risk.

    Honest limitation: pypdf's text extraction can mangle spacing/column
    order on a heavily tabular transcript layout (a real risk for a
    genuine PSU transcript export, not just a theoretical one) -- course
    codes that come out fused or reordered won't match. unmatched hints
    are returned so the student can see what wasn't picked up and add it
    by hand instead of it silently vanishing.
    """
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded."}), 400
    file = request.files["file"]
    if not file or not file.filename:
        return jsonify({"error": "No file uploaded."}), 400
    if not file.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Only PDF files are supported."}), 400

    major = str(request.form.get("major") or "CMPSC").strip().upper()
    second_major = str(request.form.get("second_major") or "").strip().upper() or None
    additional_majors_in = request.form.getlist("additional_majors") or []
    minors_in = request.form.getlist("minors") or []
    catalog_year = request.form.get("catalog_year")
    start_year = request.form.get("start_year")

    plan = engine.load_degree_plan(major, catalog_year or start_year)
    if plan is None:
        return jsonify({"error": f"No degree plan available for {major}."}), 404

    if second_major or additional_majors_in or minors_in:
        second_plan = (
            engine.load_degree_plan(second_major, catalog_year or start_year)
            if second_major else None
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

    try:
        pdf_bytes = file.read()
        reader = PdfReader(BytesIO(pdf_bytes))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception:
        return jsonify({
            "error": "Couldn't read that file — make sure it's a real, non-corrupted PDF.",
        }), 400

    if not text.strip():
        return jsonify({
            "error": "No readable text found in that PDF — a scanned image transcript "
                     "(rather than a real text PDF export) isn't supported yet.",
        }), 400

    course_text = _extract_transcript_course_text(text)
    matched, unmatched = engine.match_courses_in_text(course_text, catalog)
    return jsonify({
        "matched": [
            {"code": m["code"], "name": m["name"], "credits": m["credits"]} for m in matched
        ],
        "unmatched": unmatched[:20],
    })


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
    # Cloud requests need the key as a bearer token; local Ollama takes no
    # auth at all, so this header is simply omitted when there's no key.
    headers = {"Authorization": f"Bearer {OLLAMA_API_KEY}"} if OLLAMA_API_KEY else {}
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
            headers=headers,
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
        headers=headers,
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


def _detect_unconfirmed_major_mentions(
    prompt: str, confirmed_depts: set,
) -> List[str]:
    """Major-alias mentions in the prompt whose dept isn't in confirmed_depts.

    Catches "double major in MATH and ECON" when only MATH actually got set
    (second_major/additional_majors are payload-only fields — nothing in
    the prompt parser ever fills them in). Rather than silently dropping
    the second one, the caller surfaces this back to the student as a
    confirm-or-correct question. Same course-code-collision guard as
    _extract_major_from_prompt (a short alias immediately followed by a
    number is a course mention, e.g. "MATH 140", not a major statement).
    Order of first appearance in the prompt, deduped.
    """
    raw = (prompt or "").upper()
    found: List[Tuple[int, str]] = []
    seen_depts = set()
    for alias, dept in _MAJOR_ALIASES.items():
        if dept in confirmed_depts or dept in seen_depts:
            continue
        for m in re.finditer(rf"\b{re.escape(alias)}\b", raw):
            if re.match(r"\s*-?\s*\d", raw[m.end():]):
                continue
            # "minoring in MATH" / "MATH minor" — already unambiguous, not
            # a second-major candidate, so don't flag it as one.
            window = raw[max(0, m.start() - 20):m.end() + 10]
            if "MINOR" in window:
                continue
            found.append((m.start(), dept))
            seen_depts.add(dept)
            break
    found.sort()
    return [dept for _, dept in found]


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

_NEXT_COURSES_TRIGGERS = [
    "what should i take", "what do i take", "what courses should i",
    "what course should i", "what classes should i", "what class should i",
    "which courses should i", "which course should i", "which classes should i",
    "what's next", "whats next", "what should i take next",
    "what courses can i take", "what classes can i take",
    "what courses do i need", "what classes do i need",
    "what should i register for", "what should i sign up for",
    "what should i schedule", "recommend a course", "recommend courses",
    "recommend a class", "recommend classes",
]


def _is_asking_next_courses(prompt: str) -> bool:
    """"What should I take [for/next semester]?" and its common phrasings —
    the one case where the itemized next-semester list belongs directly in
    the reply instead of a count + Flowchart link, since a count doesn't
    actually answer the question. See detailed_next_sem in
    _build_reply_text and allow_full_next_sem in _build_phrase_prompt."""
    low = (prompt or "").lower()
    return any(t in low for t in _NEXT_COURSES_TRIGGERS)


_WHY_BLOCKED_TRIGGERS = [
    "why can't i", "why cant i", "why can i not", "why won't", "why wont",
    "what do i need for", "what do i need to take", "what's required for",
    "whats required for", "what are the prereqs", "what are the prerequisites",
    "prereqs for", "prerequisites for", "when can i take", "am i eligible for",
    "am i eligible to take", "can i take", "why is", "why isn't", "why isnt",
]


def _extract_asked_course(prompt: str, catalog: Dict[str, Any]) -> Optional[str]:
    """The one specific course code a "why can't I take X" / "what do I
    need for X" question is asking about — first (only) match from the
    real catalog, same matcher parse_completion_changes already uses for
    "I took X" statements. None if zero or more than one course is named
    (an ambiguous multi-course question isn't this feature's job)."""
    matched, _ = engine.match_courses_in_text(prompt, catalog)
    codes = {m["code"] for m in matched}
    return next(iter(codes)) if len(codes) == 1 else None


def _is_asking_why_blocked(prompt: str) -> bool:
    low = (prompt or "").lower()
    return any(t in low for t in _WHY_BLOCKED_TRIGGERS)


def _build_specific_course_answer(
    code: str, catalog: Dict[str, Any], completed: set,
) -> Optional[str]:
    """Deterministic, focused answer about ONE named course's real
    prerequisite/exclusion status — computed the same way scan_once/
    recommend_semester decide eligibility, not a guess. Unlike the "Still
    locked" section (top 3 blocked courses generically), this answers
    about the specific course asked about even if it isn't in that top 3,
    and confirms eligibility when it's already clear either way."""
    course = catalog.get(engine.norm_code(code))
    if not course:
        return None
    if code in completed:
        return f"You've already completed {code} ({course.name})."
    missing = engine.missing_prereqs(course, completed)
    conflict = engine.exclusion_conflict(course, completed)
    if conflict:
        return (
            f"{code} ({course.name}) — you can't take or count this: you've already "
            f"completed {', '.join(sorted(conflict))}, which excludes it."
        )
    if missing:
        needs = "; ".join(" or ".join(g) for g in missing)
        return f"{code} ({course.name}) — needs: {needs}. You haven't completed that yet."
    return f"{code} ({course.name}) — you're eligible to take this now; its prerequisites are satisfied."


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
    placement_note: Optional[str] = None,
    opener: str = "",
    unconfirmed_majors: Optional[List[str]] = None,
    detailed_next_sem: bool = False,
    specific_course_answer: Optional[str] = None,
) -> str:
    lines: List[str] = []

    if opener:
        lines.append(opener)
    if specific_course_answer:
        lines.append(specific_course_answer)
    if chat_start_year:
        lines.append(
            f"Got it — switched to the {chat_start_year} requirements "
            f"(the catalog year you started college)."
        )
    if bulk_note:
        lines.append(f"Got it — marked {bulk_note} as already completed.")
    if placement_note:
        lines.append(placement_note)
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

    confirmation_question = _build_confirmation_question(major, unconfirmed_majors)
    if confirmation_question:
        lines.append(confirmation_question)

    # Progress % and full graduation-goal detail are already the headline
    # numbers on the Home and Progress pages — restating them here is the
    # single biggest source of duplicate text in the reply, so this is
    # deliberately one short line, with a real clickable link (reply_links,
    # rendered by the frontend) standing in for the pointer phrase that
    # used to be typed out here.
    status_bits = (
        f"{progress['done_items']}/{progress['total_items']} requirements complete "
        f"on the {major} {catalog_year} plan"
    )
    if goal:
        status = "on track" if goal.get("met") else "not on track"
        status_bits += f", {status} for a {goal['grad_years']}-year graduation"
    lines.append(f"{status_bits}.")

    if progress.get("extra_courses"):
        lines.append(
            "Completed courses not on the plan (may count as electives): "
            + ", ".join(progress["extra_courses"][:8])
        )

    # The full next-semester course list already appears on Home ("Next
    # up") and Flowchart ("Recommended next semester") — a chat reply that
    # re-lists every course with its reason is just re-rendering those
    # pages as text on every turn, so this is normally a count + pointer.
    # BUT when the student directly asks "what should I take" (detected by
    # _is_asking_next_courses and passed in as detailed_next_sem), a count
    # doesn't actually answer the question they asked — this is the one
    # case where the itemized list, with its real prerequisite/flowchart-
    # grounded reason per course (computed by recommend_semester, not
    # invented here), belongs directly in the reply.
    if next_sem["courses"]:
        term_name = next_term_label or "next semester"
        n = len(next_sem["courses"])
        course_word = "course" if n == 1 else "courses"
        if detailed_next_sem:
            lines.append(_build_next_sem_detail_block(next_sem, term_name))
        else:
            lines.append(
                f"{n} {course_word} recommended for {term_name} "
                f"({next_sem['total_credits']:g} credits)."
            )
    else:
        lines.append("All flowchart requirements are satisfied — you're set to graduate! 🎓")

    # The full weighted-ranking list is the entire Recommendations page,
    # reasons included — repeating it here is pure duplication, so this is
    # a count, not the list itself (reply_links carries the real link).
    if ranked:
        lines.append(f"{len(ranked)} more eligible course(s), ranked with reasons.")

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


def _build_confirmation_question(major: str, unconfirmed_majors: Optional[List[str]]) -> str:
    if not unconfirmed_majors:
        return ""
    return (
        f"Just to confirm — you also mentioned {', '.join(unconfirmed_majors)}. "
        f"I only set {major} for now, since I can't add a second major or minor "
        "from chat text alone. Was that meant as one? Pick it from the Major/Minors "
        "fields above if so, and I'll fold it into your plan."
    )


def _build_next_sem_detail_block(next_sem: Dict[str, Any], term_name: str) -> str:
    """The itemized "what should I take" answer — every real course from
    recommend_semester(), by name and its real prerequisite/flowchart
    reason. Factored out so api_plan can also use it as a deterministic
    guarantee: an LLM asked to "name every one of those courses" was
    observed silently under-counting duplicate-looking Gen Ed slots and
    dropping a distinct course entirely (a real student-facing "advisor
    missed a requirement" bug, caught by testing, not theoretical) — so
    this exact block gets appended verbatim after phrasing rather than
    trusted to the model's own enumeration. See _next_sem_fully_covered."""
    lines = [f"For {term_name} ({next_sem['total_credits']:g} credits), you need:"]
    for c in next_sem["courses"]:
        label = c.get("code") or c.get("name")
        lines.append(f"  • {label} ({c['credits']:g} cr) — {c['reason']}")
    return "\n".join(lines)


def _next_sem_fully_covered(next_sem: Dict[str, Any], text: str) -> bool:
    """False if the phrased reply doesn't actually name every real course
    -- including getting the COUNT of duplicate-looking items (multiple
    generic "GEN ED" slots) right, not just whether "GEN ED" appears at
    all as a substring. A reply that says "two Gen Eds" when there are
    really three has silently dropped one, the same failure mode as
    dropping a uniquely-named course."""
    from collections import Counter
    labels = [c.get("code") or c.get("name") for c in next_sem["courses"]]
    counts = Counter(labels)
    low = text.lower()
    for label, needed in counts.items():
        if needed == 1:
            if label.lower() not in low:
                return False
        else:
            if low.count(label.lower()) < needed:
                return False
    return True


def _build_reply_links(
    next_sem: Dict[str, Any], ranked: List[Dict[str, Any]],
) -> List[Dict[str, str]]:
    """Where the detail _build_reply_text condensed to a count actually lives.

    Rendered by the frontend as clickable chips under the chat reply —
    real in-app navigation, not another paragraph of text.
    """
    links: List[Dict[str, str]] = []
    if next_sem.get("courses"):
        links.append({"label": "See your Flowchart", "route": "/flowchart"})
    if ranked:
        links.append({"label": "See full Recommendations", "route": "/recommendations"})
    links.append({"label": "See Progress breakdown", "route": "/progress"})
    return links


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
    allow_full_next_sem: bool = False,
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
    if allow_full_next_sem:
        list_instruction = (
            "The student directly asked what to take, so the facts above spell out the full "
            "next-semester course list with real, prerequisite/flowchart-grounded reasons — name "
            "every one of those courses and its reason; a count alone would not answer their "
            "question. Still don't expand the OTHER count in the facts (ranked eligible courses) "
            "into a list, and don't tell the student to go check another page or mention any page "
            "by name; the app already handles that separately, outside your reply."
        )
    else:
        list_instruction = (
            "The facts intentionally give counts, not full course lists — do not expand a count "
            "back into a full list, and do not tell the student to go check another page or "
            "mention any page by name; the app already handles that separately, outside your reply."
        )
    return (
        "Verified planning facts:\n"
        f"{facts}\n"
        f"{context_block}"
        f"{anti_repeat}\n"
        f"Student question: {question}\n\n"
        f"Write a short, friendly advisor reply (max ~{'220' if allow_full_next_sem else '110'} words) "
        "grounded ONLY in the facts above. "
        "Keep every course code exactly as written. Do not add or remove recommendations. "
        f"{list_instruction} "
        "Do not make a definitive judgment call the student didn't ask for — e.g. don't declare "
        "which major is 'the priority' or say you'll 'focus on X and explore Y later' unless the "
        "student's own question specifically asked something like 'which should I focus on' or "
        "'which is faster/easier.' If a fact is phrased as a question needing the student's "
        "confirmation, leave it open and ask it — don't quietly resolve it yourself."
    )


def _llm_phrase_reply(
    question: str, facts: str, rag_context: str, recent_reply_excerpt: str = "",
    allow_full_next_sem: bool = False,
) -> Optional[str]:
    if not USE_OLLAMA or not question.strip():
        return None
    try:
        prompt = _build_phrase_prompt(
            question, facts, rag_context, recent_reply_excerpt,
            allow_full_next_sem=allow_full_next_sem,
        )
        text = ollama_chat(prompt)
        return text.strip() or None
    except Exception:
        return None


# ----------------------------
# Undecided-major exploration (no plan exists yet — pure conversation)
# ----------------------------

_NARROWING_QUESTIONS = [
    "What subjects have you enjoyed most so far — things like math, science, "
    "writing, art, history, or business?",
    "Do you picture yourself working more hands-on and technical, or more "
    "people-facing and creative?",
    "Is there a career or field you're already curious about, even loosely — "
    "health, tech, business, education, engineering, the arts?",
    "Would you rather work behind a computer or in a lab, out in the field, "
    "or directly with people?",
]


def _real_majors_summary(campus: Optional[str] = None) -> str:
    """Every real major (deduped across catalog years), grouped by college —
    the ONLY majors the exploration prompt is allowed to mention. Grounds
    the LLM against the actual catalog instead of letting it invent a
    major, matching this project's real-data-only discipline everywhere
    else. Title's trailing "(College Name)" is the grouping key, same
    parsing PlannerSetupComponent's frontend counterpart uses."""
    plans = engine.list_degree_plans(campus)
    seen: Dict[str, str] = {}
    for p in plans:
        major = p.get("major") or ""
        title = p.get("title") or major
        if major and major not in seen:
            seen[major] = title

    by_college: Dict[str, List[str]] = {}
    for major, title in sorted(seen.items()):
        m = re.search(r"\(([^)]+)\)\s*$", title)
        college = m.group(1) if m else "Other"
        name = re.sub(r"\s*\([^)]+\)\s*$", "", title).strip()
        by_college.setdefault(college, []).append(f"{major} — {name}")

    lines: List[str] = []
    for college in sorted(by_college):
        lines.append(f"{college}:")
        lines.extend(f"  {entry}" for entry in by_college[college])
    return "\n".join(lines)


def _build_explore_majors_prompt(
    question: str, majors_summary: str, recent_reply_excerpt: str, turn_index: int,
) -> str:
    anti_repeat = ""
    if recent_reply_excerpt.strip():
        anti_repeat = (
            "\nYour own previous reply in this conversation started with:\n"
            f"\"{recent_reply_excerpt.strip()}\"\n"
            "Vary your opening this time — do not reuse that phrasing.\n"
        )
    return (
        "The student hasn't picked a major yet and is exploring options. Here is the "
        "REAL, complete list of majors they can actually choose, grouped by college — "
        "never suggest, describe, or invent details about a major that isn't on this "
        "list:\n"
        f"{majors_summary}\n"
        f"{anti_repeat}\n"
        f"Student said: {question}\n\n"
        "Reply in under 120 words, friendly and conversational — just the reply itself, "
        "never a note explaining your own reasoning or why you're asking something. If "
        "the student explicitly asked for major suggestions or said they're ready, "
        "suggest 2-4 specific real majors from the list above now, with a one-sentence "
        "reason each grounded in whatever they've already told you — do not deflect with "
        "another question just because it's early in the conversation. Otherwise, if they "
        "haven't shared much about their interests yet, ask ONE specific narrowing "
        "question (subjects they enjoy, hands-on vs. people-facing work, a field they're "
        "curious about) rather than listing majors. Once they've shared enough on their "
        "own to narrow it down, suggest 2-4 majors the same way, and invite them to ask "
        "for more detail on any one of them or say when they're ready to pick."
    )


def _explore_majors_fallback(majors_summary: str, turn_index: int) -> str:
    if turn_index < len(_NARROWING_QUESTIONS):
        return _NARROWING_QUESTIONS[turn_index]
    return (
        "Here's the real list of PSU majors, grouped by college — take a look and tell me "
        "if anything catches your eye, or share more about what you enjoy and I'll narrow "
        "it down with you:\n\n" + majors_summary
    )


def _llm_explore_majors_reply(
    question: str, majors_summary: str, recent_reply_excerpt: str, turn_index: int,
) -> Optional[str]:
    if not USE_OLLAMA or not question.strip():
        return None
    try:
        prompt = _build_explore_majors_prompt(question, majors_summary, recent_reply_excerpt, turn_index)
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
    # An ALEKS score or "I took calc in high school" is a one-time placement
    # fact, not something restated every message — same round-trip pattern
    # as consumed_slot_ids_in: the client persists and re-sends whatever this
    # endpoint last echoed back, merged forward with anything newly detected
    # in this message (see detect_math_placement below).
    try:
        math_placement_tier_in = int(payload.get("math_placement_tier") or 0) or None
    except (TypeError, ValueError):
        math_placement_tier_in = None
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

    # consumed_slot_ids_in was computed against whatever major was in effect
    # when the client last saw a response — if THIS request's prompt just
    # changed the effective major (e.g. "actually I'm a NURS major" while
    # the dropdown/payload still says CMPSC), those ids describe positions
    # in a completely different plan. Item ids are small sequential
    # integers assigned fresh per plan, so blindly reusing them could
    # coincidentally collide with a real item in the new major's plan and
    # silently mark an un-completed requirement done. The client already
    # clears its own copy on an explicit dropdown change (see
    # PlannerStateService.onPromptSubmitted); this covers the chat-detected
    # case that check can't see coming until the response comes back.
    if payload_major and major != payload_major:
        consumed_slot_ids_in = set()

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

    unconfirmed_majors = _detect_unconfirmed_major_mentions(
        prompt,
        {major, second_major_code, *(str(c).strip().upper() for c in additional_majors_in)} - {None},
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

    # ALEKS score / high-school-calculus math placement ("I scored 75 on
    # ALEKS", "I took calc in high school") — same persist-and-merge pattern
    # as bulk completion above; a placement never gets *worse* mid-session,
    # so a fresh mention only raises the stored tier, never lowers it.
    detected_placement = engine.detect_math_placement(prompt)
    math_placement_tier = max(
        math_placement_tier_in or 0, (detected_placement or {}).get("tier") or 0,
    ) or None
    # Only worth telling the student about the first time it raises their
    # placement — restating "I took calc in high school" on a later turn
    # (now already reflected in math_placement_tier_in) shouldn't repeat it.
    placement_note = None
    if detected_placement and detected_placement["tier"] > (math_placement_tier_in or 0):
        if detected_placement["source"] == "high school calculus":
            placement_note = (
                "Got it — since you took calculus in high school, PSU's real ALEKS "
                "placement policy places you straight past the developmental algebra/"
                "trig sequence toward MATH 110/140, so those won't show up as something "
                "you still owe."
            )
        else:
            placement_note = (
                f"Got it — with an ALEKS score of {detected_placement['score']}, you're "
                "placed past the lower algebra/trig courses your major's plan might "
                "otherwise list, per PSU's real ALEKS placement chart."
            )

    completed = {engine.norm_code(c) for c in completed_in if str(c).strip()}
    completed |= {m["code"] for m in added} | bulk_codes
    completed -= {m["code"] for m in removed}
    completed_sorted = sorted(completed)

    # Scheduling/progress/recommendations all need math-placement waivers
    # folded in — see expand_math_placement's docstring for why that has to
    # happen once, upstream, rather than inside each of those functions.
    # `completed` itself (and completed_sorted above) stays the honest,
    # literal set for anything student-facing — a waiver was never actually
    # taken, so it must never appear in the completed-courses list itself.
    completed_for_planning = engine.expand_math_placement(completed, math_placement_tier)

    summer_unavailable = {engine.norm_code(c) for c in summer_unavailable_in if str(c).strip()}
    summer_unavailable |= {m["code"] for m in summer_flagged}
    summer_unavailable_sorted = sorted(summer_unavailable)

    # --- deterministic planning ---
    full_plan = engine.build_full_plan(
        plan, catalog, completed_for_planning,
        start_year=start_year,
        grad_years=grad_years,
        allow_summer=allow_summer,
        summer_unavailable=summer_unavailable,
        initial_consumed_slots=bulk_slot_ids or None,
    )
    # The next term to plan is the first simulated term (summer-aware).
    first_term = full_plan["terms"][0] if full_plan["terms"] else None
    next_sem = engine.recommend_semester(
        plan, catalog, completed_for_planning,
        consumed_slots=bulk_slot_ids or None,
        max_credits=max_credits or (engine.SUMMER_MAX_CREDITS if first_term and first_term["is_summer"] else None),
        exclude_codes=summer_unavailable if first_term and first_term["is_summer"] else None,
    )
    if first_term:
        next_sem["courses"] = first_term["courses"]
        next_sem["total_credits"] = first_term["total_credits"]
    progress = next_sem["progress"]
    # Mermaid/flowchart visuals use the honest `completed` (not the expanded
    # set) — they render a "Completed" bucket the student sees as their own
    # transcript, which a synthetic placement waiver must never join.
    mermaid = engine.build_mermaid(plan, catalog, completed, next_sem["courses"])
    unlock_map = engine.build_unlock_map(plan, catalog, completed_for_planning)
    semester_flowchart = engine.build_semester_flowchart(catalog, completed, full_plan["terms"])
    low_cost_minors = engine.suggest_low_cost_minors(
        plan, completed, catalog_year or start_year, exclude_minors=set(minors_in),
    )

    # --- weighted ranking of all eligible courses ---
    interests = engine.extract_interests(prompt)
    ranked = engine.score_recommendations(
        plan, catalog, completed_for_planning, interests=interests,
    )
    tips = engine.default_tips(progress, next_sem["blocked"])

    # --- prereq graph (vis-network compatibility) ---
    try:
        graph_nodes, graph_edges, _ = build_progression_graph(catalog, completed, max_depth=2)
    except Exception:
        graph_nodes, graph_edges = [], []
    graph = {"nodes": graph_nodes, "edges": graph_edges}

    # --- reply text: deterministic facts, optionally rephrased by Ollama + RAG notes ---
    asking_next_courses = _is_asking_next_courses(prompt)
    specific_course_answer = None
    if _is_asking_why_blocked(prompt):
        asked_code = _extract_asked_course(prompt, catalog)
        if asked_code:
            specific_course_answer = _build_specific_course_answer(asked_code, catalog, completed)
    facts = _build_reply_text(
        plan.get("major", major), plan.get("catalog_year", ""),
        added, removed, unmatched,
        progress, next_sem, ranked, full_plan["warnings"],
        summer_flagged=summer_flagged,
        goal=full_plan.get("goal"),
        next_term_label=first_term["label"] if first_term else "",
        chat_start_year=chat_start_year,
        bulk_note=bulk["description"] if bulk else None,
        placement_note=placement_note,
        opener=_pick_opener(turn_index),
        unconfirmed_majors=unconfirmed_majors,
        detailed_next_sem=asking_next_courses,
        specific_course_answer=specific_course_answer,
    )
    reply_links = _build_reply_links(next_sem, ranked)
    rag_response = facts
    if prompt:
        rag_context = retrieve_rag_context(prompt, dept=plan.get("major", major))
        phrased = _llm_phrase_reply(
            prompt, facts, rag_context, recent_reply_excerpt=recent_reply,
            allow_full_next_sem=asking_next_courses,
        )
        if phrased:
            rag_response = phrased
            # A clarifying question (or a specific-course answer, same
            # risk) is too important to leave to the LLM's ~110-word
            # compression pass — the confirmation-question case was
            # observed being dropped entirely rather than risk it, so
            # both are always appended deterministically when phrasing
            # succeeds, no exceptions. (Checking whether the LLM already
            # "covered it" isn't a reliable substring match — its
            # phrasing never matches this template word-for-word — so a
            # false negative there would silently reintroduce the exact
            # bug this fixes.)
            confirmation_question = _build_confirmation_question(
                plan.get("major", major), unconfirmed_majors,
            )
            # Same guarantee for the itemized next-semester list: an LLM
            # told to "name every one of those courses" was observed
            # under-counting duplicate Gen Ed slots and dropping a
            # distinct course (see _next_sem_fully_covered) — only append
            # the deterministic block when the phrasing actually missed
            # something, so a reply that already got it right isn't
            # cluttered with a redundant repeat of the same list.
            next_sem_gap = None
            if asking_next_courses and next_sem["courses"]:
                if not _next_sem_fully_covered(next_sem, phrased):
                    term_name = first_term["label"] if first_term else "next semester"
                    next_sem_gap = _build_next_sem_detail_block(next_sem, term_name)
            appended = [t for t in (confirmation_question, specific_course_answer, next_sem_gap) if t]
            if appended:
                rag_response = "\n\n".join([phrased, *appended])

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
        # ALEKS/high-school-calculus math placement tier — same persist-and-
        # resend pattern as consumedSlotIds. See math_placement_tier_in.
        "mathPlacementTier": math_placement_tier,
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
        "replyLinks": reply_links,
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
    # Local dev only -- this is Werkzeug's single-process dev server, not a
    # production WSGI server. threaded=True at least lets it handle more
    # than one request at a time locally; real concurrency (multiple
    # worker processes) comes from running via gunicorn instead, e.g.:
    #   gunicorn --workers 4 --threads 4 --bind 0.0.0.0:$PORT app:app
    # 5001 by default: macOS AirPlay Receiver squats on port 5000.
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5001")), debug=FLASK_DEBUG, threaded=True)
