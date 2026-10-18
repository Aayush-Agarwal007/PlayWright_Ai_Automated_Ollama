"""Local Ollama client. Everything stays on 127.0.0.1."""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import requests

from .validation_service import parse_json_loose

log = logging.getLogger(__name__)


class OllamaError(Exception):
    """Ollama is unreachable, the model is missing, or output is unusable."""


@dataclass
class OllamaStatus:
    reachable: bool
    model_available: bool
    model: str
    message: str
    models: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.reachable and self.model_available


CLASSIFY_SYSTEM = """You classify Google Forms questions for a form-filling assistant.
Return ONLY JSON: {"classifications":[{"id":1,"canonical_field":"<field>","confidence":"high|medium|low","reason":"short"}]}
canonical_field MUST be exactly one of the allowed_fields, or:
- "open_ended": the question asks for an opinion/motivation/description that can get a generic answer
- "unknown_factual": the question asks for a personal fact not in allowed_fields (grades, dates, status, declarations, history)
- "none": none of the above applies
Different wordings of the same meaning map to the same field (e.g. "electronic mail" = registered_email).
Do not treat enrollment, roll and registration numbers as the same thing.
Return exactly one entry per supplied id."""

DRAFT_SYSTEM = """You write short draft answers for open-ended Google Forms questions.
Return ONLY JSON: {"answers":[{"id":1,"action":"text|skip","answer":"text","draft":true,"confidence":"high|medium|low","reason":"short"}]}
Rules:
- Answer the question directly, naturally and professionally, in 1-2 sentences, under 40 words unless a longer answer is explicitly requested.
- Use the supplied profile context only where relevant. Do not exaggerate.
- NEVER invent personal facts: qualifications, grades, dates, experience, contact details, application status, recruiter contact.
- If the question needs a personal fact you do not have, use action "skip" and an empty answer.
- Return exactly one entry per supplied id. Never submit the form."""

PICK_SYSTEM = """You map a known profile value to ONE option of a form question.
Return ONLY JSON: {"option":"<exact option text or empty>","confidence":"high|medium|low"}
Choose only an option that clearly means the same as the value. If none clearly does, return an empty option."""


class OllamaService:
    def __init__(self, config: dict[str, Any], session: Any | None = None) -> None:
        self.session = session or requests.Session()
        self.reload(config)

    def reload(self, config: dict[str, Any]) -> None:
        base = config.get("ollama_base_url")
        if not base:
            legacy = str(config.get("ollama_url", "") or "")
            base = legacy.split("/api/")[0] if "/api/" in legacy else (legacy or "http://127.0.0.1:11434")
        self.base_url = str(base).rstrip("/")
        self.model = str(config.get("ollama_model", "qwen2.5:3b"))
        self.timeout = int(config.get("request_timeout_seconds", 120))
        self.retries = max(0, int(config.get("ollama_retries", 2)))

    def check(self) -> OllamaStatus:
        try:
            r = self.session.get(f"{self.base_url}/api/tags", timeout=5)
            r.raise_for_status()
            models = [m.get("name", "") for m in r.json().get("models", [])]
        except Exception as exc:
            return OllamaStatus(False, False, self.model,
                                f"Ollama not reachable at {self.base_url}. Start it with 'ollama serve'. ({exc.__class__.__name__})")
        available = self.model in models or f"{self.model}:latest" in models
        msg = "Ready" if available else f"Model '{self.model}' not installed. Run: ollama pull {self.model}"
        return OllamaStatus(True, available, self.model, msg, models)

    def chat_json(self, system: str, payload: dict[str, Any]) -> Any:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.1, "num_predict": 1024},
        }
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            if attempt:
                time.sleep(min(2.0, 0.5 * attempt))
            try:
                r = self.session.post(f"{self.base_url}/api/chat", json=body, timeout=self.timeout)
            except requests.exceptions.RequestException as exc:
                last = exc
                log.warning("Ollama request failed (attempt %d): %s", attempt + 1, exc.__class__.__name__)
                continue
            if r.status_code == 404:
                raise OllamaError(f"Model '{self.model}' not found. Run: ollama pull {self.model}")
            try:
                r.raise_for_status()
                return parse_json_loose(r.json()["message"]["content"])
            except Exception as exc:  # malformed JSON / HTTP error
                last = exc
                log.warning("Unusable Ollama response (attempt %d): %s", attempt + 1, exc.__class__.__name__)
        raise OllamaError(f"Ollama did not return a usable response: {last.__class__.__name__ if last else 'unknown'}")

    # --- task-specific calls (return raw parsed JSON; callers validate) ---
    def classify(self, questions: list[dict], allowed_fields: list[str]) -> Any:
        return self.chat_json(CLASSIFY_SYSTEM, {"allowed_fields": allowed_fields, "questions": questions})

    def draft(self, questions: list[dict], context: dict[str, Any]) -> Any:
        return self.chat_json(DRAFT_SYSTEM, {"profile_context": context, "questions": questions})

    def pick_option(self, question: str, value: str, options: list[str]) -> Any:
        return self.chat_json(PICK_SYSTEM, {"question": question, "profile_value": value, "options": options})
