import json

import httpx

from app.settings import settings


def build_prompt(user_prompt: str, schema_text: str) -> str:
    return (
        "You are an assistant that must return ONLY valid JSON.\n"
        "The JSON must strictly match this schema:\n"
        f"{schema_text}\n"
        f"User prompt: {user_prompt}\n"
        "Return ONLY JSON with no extra text."
    )


def call_ollama(prompt: str) -> str:
    url = f"{settings.ollama_url}/api/generate"
    payload = {"model": settings.ollama_model, "prompt": prompt, "stream": False}
    response = httpx.post(url, json=payload, timeout=120)
    response.raise_for_status()
    data = response.json()
    return data.get("response", "")


def parse_llm_json(raw_text: str) -> dict:
    return json.loads(raw_text)
