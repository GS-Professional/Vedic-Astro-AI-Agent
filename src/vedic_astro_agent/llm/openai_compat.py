"""OpenAI-compatible chat-completions client (stdlib only).

Works with OpenAI, Azure-style proxies, Groq, and local servers (llama.cpp, vLLM,
Ollama's OpenAI endpoint) — anything that speaks POST {base_url}/chat/completions.
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Any

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"


class OpenAICompatClient:
    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL,
                 model: str = DEFAULT_MODEL, timeout: float = 60.0):
        if not api_key:
            raise ValueError("api_key is required")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout

    def complete(self, system: str, user: str, temperature: float = 0.2) -> str:
        payload: dict[str, Any] = {
            "model": self._model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        req = urllib.request.Request(
            f"{self._base_url}/chat/completions",
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            body = json.loads(resp.read().decode())
        try:
            return body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"unexpected chat-completions response: {body!r}") from exc


def llm_from_env() -> OpenAICompatClient | None:
    """Build a client from OPENAI_API_KEY / VEDIC_LLM_BASE_URL / VEDIC_LLM_MODEL, or None."""
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None
    return OpenAICompatClient(
        api_key=api_key,
        base_url=os.environ.get("VEDIC_LLM_BASE_URL", DEFAULT_BASE_URL),
        model=os.environ.get("VEDIC_LLM_MODEL", DEFAULT_MODEL),
    )
