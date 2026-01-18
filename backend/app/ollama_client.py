import json
import time

import httpx

from app.settings import settings


JSON_SKELETON = """{
  "document_id": "string",
  "version": 1,
  "llm_model": "string",
  "epics": [
    {
      "title": "string",
      "tasks": [
        {
          "title": "string",
          "role": "Backend",
          "pert_hours": {
            "optimistic": 1,
            "most_likely": 2,
            "pessimistic": 3,
            "expected": 2.0
          }
        }
      ]
    }
  ],
  "totals": {
    "expected_hours": 2.0
  }
}"""


def build_prompt(user_prompt: str, schema_text: str) -> str:
    return (
        "Return ONLY a single JSON object. No markdown. No comments. No explanations.\n"
        "Output MUST start with '{' and end with '}'.\n"
        "Do NOT add fields that are not in the schema.\n"
        "Do NOT remove fields from the skeleton.\n"
        "All numeric values must be numbers (no strings).\n"
        "Do NOT use placeholder titles like \"Epic Title\" or \"Task Title\" — titles must be derived from the document text.\n"
        "Roles allowed: \"SA/BA\", \"Backend\", \"Frontend\", \"Data-engineer\", \"DevOps\".\n"
        "You MUST strictly follow this structure exactly as shown:\n"
        f"{JSON_SKELETON}\n"
        "The JSON must strictly match this schema:\n"
        f"{schema_text}\n"
        f"User prompt: {user_prompt}\n"
        "Return ONLY corrected JSON that matches the schema EXACTLY. No other text."
    )


def _extract_chat_text(payload: dict) -> str:
    message = payload.get("message") or {}
    return message.get("content", "")


def _extract_generate_text(payload: dict) -> str:
    return payload.get("response", "")


def _timeout() -> httpx.Timeout:
    return httpx.Timeout(600.0, connect=600.0)


def _snippet(text: str, limit: int = 120) -> str:
    return text.replace("\n", " ")[:limit]


def _raise_http_error(endpoint: str, response: httpx.Response) -> None:
    raise RuntimeError(
        f"llm_http_error: {response.status_code} {endpoint} {_snippet(response.text)}"
    )


def wait_for_ollama_ready(timeout_seconds: int = 120) -> None:
    deadline = time.monotonic() + timeout_seconds
    tags_url = f"{settings.ollama_url}/api/tags"
    while time.monotonic() < deadline:
        try:
            response = httpx.get(tags_url, timeout=_timeout())
            if response.status_code == 200:
                return
        except httpx.TimeoutException:
            pass
        except httpx.HTTPError:
            pass
        time.sleep(2)
    raise RuntimeError("llm_http_error: timeout /api/tags")


def call_ollama(prompt: str) -> str:
    wait_for_ollama_ready()
    chat_url = f"{settings.ollama_url}/api/chat"
    chat_payload = {
        "model": settings.ollama_model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "temperature": 0,
        "top_p": 0.1,
    }
    generate_url = f"{settings.ollama_url}/api/generate"
    generate_payload = {"model": settings.ollama_model, "prompt": prompt, "stream": False}

    backoffs = [5, 15]
    attempts = len(backoffs) + 1
    for attempt in range(attempts):
        try:
            chat_response = httpx.post(chat_url, json=chat_payload, timeout=_timeout())
        except httpx.TimeoutException:
            if attempt < len(backoffs):
                time.sleep(backoffs[attempt])
                continue
            raise RuntimeError("llm_http_error: timeout /api/chat")
        except httpx.HTTPError:
            if attempt < len(backoffs):
                time.sleep(backoffs[attempt])
                continue
            raise RuntimeError("llm_http_error: http_error /api/chat")

        if chat_response.status_code == 200:
            return _extract_chat_text(chat_response.json())
        if chat_response.status_code >= 500:
            if attempt < len(backoffs):
                time.sleep(backoffs[attempt])
                continue
            _raise_http_error("/api/chat", chat_response)

        try:
            generate_response = httpx.post(generate_url, json=generate_payload, timeout=_timeout())
        except httpx.TimeoutException:
            if attempt < len(backoffs):
                time.sleep(backoffs[attempt])
                continue
            raise RuntimeError("llm_http_error: timeout /api/generate")
        except httpx.HTTPError:
            if attempt < len(backoffs):
                time.sleep(backoffs[attempt])
                continue
            raise RuntimeError("llm_http_error: http_error /api/generate")

        if generate_response.status_code == 200:
            return _extract_generate_text(generate_response.json())
        if generate_response.status_code >= 500:
            if attempt < len(backoffs):
                time.sleep(backoffs[attempt])
                continue
            _raise_http_error("/api/generate", generate_response)
        _raise_http_error("/api/generate", generate_response)

    raise RuntimeError("llm_http_error: unexpected /api/chat")


def check_ollama_health() -> dict:
    tags_url = f"{settings.ollama_url}/api/tags"
    try:
        response = httpx.get(tags_url, timeout=_timeout())
    except httpx.TimeoutException:
        return {"status": "error", "reason": "timeout /api/tags"}
    except httpx.HTTPError as exc:
        return {"status": "error", "reason": f"http_error /api/tags {exc.__class__.__name__}"}
    if response.status_code != 200:
        return {"status": "error", "reason": f"{response.status_code} /api/tags"}
    return {"status": "ok"}


def parse_llm_json(raw_text: str) -> dict:
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        start = raw_text.find("{")
        end = raw_text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("llm_no_json_found")
        snippet = raw_text[start : end + 1]
        try:
            return json.loads(snippet)
        except json.JSONDecodeError as exc:
            raise ValueError("llm_invalid_json") from exc
