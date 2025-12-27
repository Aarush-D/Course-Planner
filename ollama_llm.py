# ollama_llm.py
import ollama_llm
import requests

def ollama_chat(prompt: str, model: str = "llama3") -> str:
    r = requests.post(
        "http://localhost:11434/api/chat",
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": "You are a helpful PSU course planning assistant."},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
        },
        timeout=120,
    )
    r.raise_for_status()
    return r.json()["message"]["content"]