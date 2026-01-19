import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT / "backend"))

from app import ollama_client  # noqa: E402


class DummyResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=None)


def test_call_ollama_uses_chat_when_available(monkeypatch):
    monkeypatch.setattr(ollama_client, "wait_for_ollama_ready", lambda: None)

    def fake_post(url, json, timeout):
        if url.endswith("/api/chat"):
            return DummyResponse(200, {"message": {"content": "chat ok"}})
        return DummyResponse(500, {})

    monkeypatch.setattr(httpx, "post", fake_post)
    output = ollama_client.call_ollama("hello")
    assert output == "chat ok"


def test_call_ollama_falls_back_to_generate(monkeypatch):
    monkeypatch.setattr(ollama_client, "wait_for_ollama_ready", lambda: None)

    def fake_post(url, json, timeout):
        if url.endswith("/api/chat"):
            return DummyResponse(404, {})
        return DummyResponse(200, {"response": "generate ok"})

    monkeypatch.setattr(httpx, "post", fake_post)
    output = ollama_client.call_ollama("hello")
    assert output == "generate ok"
