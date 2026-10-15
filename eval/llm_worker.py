"""Subprocess worker for eval 3. USE_OLLAMA is read once at import time by the
app, so the LLM-on and LLM-off runs each need their own process.

    USE_OLLAMA=1 python eval/llm_worker.py PROMPTS.json OUT.json
"""
import json
import os
import sys
import time

os.environ.setdefault("RATELIMIT_ENABLED", "0")
HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.join(os.path.dirname(HERE), "Backend")
sys.path.insert(0, BACKEND)
os.chdir(BACKEND)

import app as backend_app  # noqa: E402

# Record the model's raw text for every llm_chat call, BEFORE the app's own
# grounding guard (_phrased_reply_stays_grounded) decides whether to show it.
# This wraps the function in the harness process only; app code is untouched.
# Without it, the faithfulness metric would only see replies that already
# passed the guard and would read as 0% by construction.
_RAW: list = []
_orig_llm_chat = backend_app.llm_chat


def _recording_llm_chat(prompt):
    text = _orig_llm_chat(prompt)
    _RAW.append(text)
    return text


backend_app.llm_chat = _recording_llm_chat


def main(prompts_path: str, out_path: str) -> None:
    with open(prompts_path) as f:
        prompts = json.load(f)["prompts"]
    client = backend_app.app.test_client()
    out = {
        "use_ollama": backend_app.USE_OLLAMA,
        "ollama_model": backend_app.OLLAMA_MODEL,
        "groq_model": backend_app.GROQ_MODEL if backend_app.GROQ_API_KEY else None,
        "responses": {},
    }
    for i, p in enumerate(prompts, 1):
        ctx = p["context"]
        del _RAW[:]
        t = time.time()
        r = client.post("/api/plan", json={
            "prompt": p["prompt"], "completed": ctx["completed"], "major": ctx["major"],
            "start_year": ctx.get("start_year", 2026), "grad_years": 4,
        })
        out["responses"][p["id"]] = {
            "status": r.status_code, "seconds": round(time.time() - t, 2), "body": r.get_json(silent=True),
            "raw_llm_replies": list(_RAW),
        }
        print(f"[{i}/{len(prompts)}] {p['id']} {r.status_code} {out['responses'][p['id']]['seconds']}s", file=sys.stderr, flush=True)
    with open(out_path, "w") as f:
        json.dump(out, f)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
