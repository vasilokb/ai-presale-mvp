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


def _extract_chat_text(payload: dict) -> str:
    message = payload.get("message") or {}
    return message.get("content", "")


def _extract_generate_text(payload: dict) -> str:
    return payload.get("response", "")


def call_ollama(prompt: str) -> str:
    chat_url = f"{settings.ollama_url}/api/chat"
    chat_payload = {
        "model": settings.ollama_model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }
    chat_response = httpx.post(chat_url, json=chat_payload, timeout=120)
    if chat_response.status_code == 200:
        return _extract_chat_text(chat_response.json())

    generate_url = f"{settings.ollama_url}/api/generate"
    generate_payload = {"model": settings.ollama_model, "prompt": prompt, "stream": False}
    generate_response = httpx.post(generate_url, json=generate_payload, timeout=120)
    generate_response.raise_for_status()
    return _extract_generate_text(generate_response.json())


def parse_llm_json(raw_text: str) -> dict:
    return json.loads(raw_text)
