# Backend/ollama_http.py
import os
import requests
from typing import Optional

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.1")

def ollama_generate(prompt: str, model: Optional[str] = None, temperature: float = 0.2) -> str:
    """
    Call Ollama over HTTP. No Python ollama package required.
    """
    payload = {
        "model": model or OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": temperature
        }
    }

    r = requests.post(
        f"{OLLAMA_HOST}/api/generate",
        json=payload,
        timeout=60
    )
    r.raise_for_status()
    return r.json().get("response", "").strip()