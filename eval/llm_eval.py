"""Eval 3 -- LLM faithfulness: does the phrasing layer stay inside the facts?

The app computes a deterministic reply (`rag_response` with USE_OLLAMA=0) and,
when an LLM is configured, asks it to PHRASE that reply. So for the same
request the LLM-off reply text IS the verified fact set. Two questions:

(a) How often does an LLM reply mention a course code that is not in the
    verified facts (nor in what the student said, nor in what they had already
    completed)?
(b) How often does the returned plan change between LLM on and LLM off? A
    control (LLM off vs LLM off, different hash seed) measures the noise floor.

Each configuration runs in its own subprocess: the app reads USE_OLLAMA once.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from typing import Any, Dict, List, Optional, Set

from independent_checker import Catalog, norm

HERE = os.path.dirname(os.path.abspath(__file__))
_CODE_TOKEN = re.compile(r"\b([A-Z][A-Z\-]{1,5})\s?(\d{1,3})([A-Z]{0,3})\b")


def llm_reachable(timeout: float = 3.0) -> Dict[str, Any]:
    """Which LLM the app would actually use, and whether it answers."""
    if os.environ.get("GROQ_API_KEY", "").strip():
        return {"provider": "groq", "reachable": True, "note": "GROQ_API_KEY set; not probed"}
    host = os.environ.get("OLLAMA_HOST") or ("https://ollama.com" if os.environ.get("OLLAMA_API_KEY") else "http://127.0.0.1:11434")
    try:
        with urllib.request.urlopen(host.rstrip("/") + "/api/tags", timeout=timeout) as r:
            return {"provider": "ollama", "host": host, "reachable": r.status == 200}
    except Exception as e:  # noqa: BLE001
        return {"provider": "ollama", "host": host, "reachable": False, "error": f"{type(e).__name__}: {e}"}


def _worker(prompts_path: str, use_ollama: str, hashseed: str) -> Dict[str, Any]:
    out_path = tempfile.mktemp(suffix=".json")
    env = {**os.environ, "USE_OLLAMA": use_ollama, "PYTHONHASHSEED": hashseed, "RATELIMIT_ENABLED": "0"}
    subprocess.run(
        [sys.executable, os.path.join(HERE, "llm_worker.py"), prompts_path, out_path],
        env=env, check=True, stderr=subprocess.DEVNULL if use_ollama == "0" else None,
    )
    with open(out_path) as f:
        data = json.load(f)
    os.unlink(out_path)
    return data


def _codes(text: str) -> Set[str]:
    return {norm(f"{m.group(1)} {m.group(2)}{m.group(3)}") for m in _CODE_TOKEN.finditer(text or "")}


def _canonical(body: Dict[str, Any]) -> str:
    """The plan, with phrasing removed and set-like lists put in a stable order
    (graph.edges is emitted in set-iteration order even with the LLM off)."""
    b = json.loads(json.dumps(body))
    b.pop("rag_response", None)
    cp = b.get("coursePlan") or {}
    cp.pop("rag_response", None)
    for g in (b.get("graph"), cp.get("graph")):
        if isinstance(g, dict) and isinstance(g.get("edges"), list):
            g["edges"] = sorted(g["edges"], key=lambda e: json.dumps(e, sort_keys=True))
    return json.dumps(b, sort_keys=True)


def run(prompts_path: str, backend_dir: str, limit: Optional[int] = None) -> Dict[str, Any]:
    probe = llm_reachable()
    if not probe["reachable"]:
        return {"skipped": True, "reason": f"no LLM reachable: {probe}"}

    with open(prompts_path) as f:
        meta = json.load(f)
    prompts = meta["prompts"][:limit] if limit else meta["prompts"]
    if limit:
        tmp = tempfile.mktemp(suffix=".json")
        with open(tmp, "w") as f:
            json.dump({"prompts": prompts}, f)
        prompts_path = tmp

    off = _worker(prompts_path, "0", "0")
    off_control = _worker(prompts_path, "0", "12345")
    on = _worker(prompts_path, "1", "0")

    cat = Catalog(backend_dir)
    depts = {c.split()[0] for c in cat.courses}
    per: List[Dict[str, Any]] = []
    phrased = 0
    raw_seen = raw_unsupported = raw_unsupported_strict = raw_fabricated = guard_discarded = 0
    with_unsupported = with_unsupported_strict = with_fabricated = 0
    plan_diff = control_diff = compared = 0
    unsupported_total = 0
    unsupported_in_engine_plan = 0
    for p in prompts:
        pid = p["id"]
        o, c, n = off["responses"][pid], off_control["responses"][pid], on["responses"][pid]
        if n["status"] != 200 or o["status"] != 200:
            per.append({"id": pid, "error": f"status on={n['status']} off={o['status']}"})
            continue
        facts = o["body"].get("rag_response") or ""
        reply = n["body"].get("rag_response") or ""
        was_phrased = reply != facts
        compared += 1
        if _canonical(n["body"]) != _canonical(o["body"]):
            plan_diff += 1
        if _canonical(c["body"]) != _canonical(o["body"]):
            control_diff += 1
        row: Dict[str, Any] = {"id": pid, "phrased_by_llm": was_phrased, "seconds_on": n["seconds"]}
        # Raw model output, before the app's grounding guard. A prompt can
        # trigger more than one llm_chat call; it counts as unsupported if
        # ANY raw reply names a real catalog code outside the allowed set.
        raws = [t for t in n.get("raw_llm_replies", []) if t and t.strip()]
        if raws:
            raw_seen += 1
            ctx_done_raw = {norm(x) for x in p["context"]["completed"]}
            ok = _codes(facts) | _codes(p["prompt"]) | ctx_done_raw
            ok_strict = _codes(facts) | _codes(p["prompt"])
            raw_codes = set().union(*(_codes(t) for t in raws))
            raw_real = {c_ for c_ in raw_codes if cat.known(c_)}
            r_uns = sorted(raw_real - ok)
            r_uns_strict = sorted(raw_real - ok_strict)
            r_fab = sorted(c_ for c_ in raw_codes if c_ not in raw_real and c_.split()[0] in depts)
            raw_unsupported += bool(r_uns)
            raw_unsupported_strict += bool(r_uns_strict)
            raw_fabricated += bool(r_fab)
            discarded = not was_phrased
            guard_discarded += discarded
            row.update({"raw_unsupported_codes": r_uns, "raw_unsupported_codes_strict": r_uns_strict,
                        "raw_code_shaped_not_in_catalog": r_fab, "raw_reply_discarded_by_app_guard": discarded})
        if was_phrased:
            phrased += 1
            ctx_done = {norm(x) for x in p["context"]["completed"]}
            allowed = _codes(facts) | _codes(p["prompt"]) | ctx_done
            strict_allowed = _codes(facts) | _codes(p["prompt"])
            engine_blob = json.dumps(o["body"])
            engine_codes = _codes(engine_blob)
            reply_codes = _codes(reply)
            real = {c_ for c_ in reply_codes if cat.known(c_)}
            fabricated = sorted(c_ for c_ in reply_codes if c_ not in real and c_.split()[0] in depts)
            unsupported = sorted(real - allowed)
            unsupported_strict = sorted(real - strict_allowed)
            with_unsupported += bool(unsupported)
            with_unsupported_strict += bool(unsupported_strict)
            with_fabricated += bool(fabricated)
            unsupported_total += len(unsupported)
            unsupported_in_engine_plan += sum(1 for u in unsupported if u in engine_codes)
            row.update({"unsupported_codes": unsupported, "unsupported_codes_strict": unsupported_strict,
                        "code_shaped_not_in_catalog": fabricated, "reply": reply})
        per.append(row)

    def frac(num, den):
        return {"numerator": num, "denominator": den, "rate": (round(num / den, 4) if den else None)}

    models = {"ollama_model": on.get("ollama_model"), "groq_model": on.get("groq_model")}
    if phrased == 0:
        return {"skipped": True, "reason": "LLM was reachable but produced no phrased reply for any prompt "
                                           "(every reply fell back to the deterministic text)", "probe": probe}
    return {
        "skipped": False, "probe": probe, "model": models,
        "prompts": len(prompts), "compared": compared, "phrased_by_llm": frac(phrased, compared),
        "a_raw_model_reply_mentions_code_not_in_verified_facts": frac(raw_unsupported, raw_seen),
        "a_raw_strict_variant_context_completed_not_counted_as_facts": frac(raw_unsupported_strict, raw_seen),
        "a_raw_model_reply_mentions_code_shaped_token_not_in_any_catalog": frac(raw_fabricated, raw_seen),
        "raw_model_replies_discarded_by_app_guard": frac(guard_discarded, raw_seen),
        "a_reply_shown_to_student_mentions_code_not_in_verified_facts": frac(with_unsupported, phrased),
        "a_strict_variant_context_completed_not_counted_as_facts": frac(with_unsupported_strict, phrased),
        "a_reply_mentions_code_shaped_token_not_in_any_catalog": frac(with_fabricated, phrased),
        "a_unsupported_codes_that_do_appear_in_engine_plan_output": frac(unsupported_in_engine_plan, unsupported_total),
        "b_plan_differs_llm_on_vs_off": frac(plan_diff, compared),
        "b_control_llm_off_vs_llm_off_different_hash_seed": frac(control_diff, compared),
        "per_prompt": per,
    }
