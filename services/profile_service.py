"""Profile (profile.json) and config (config.json) persistence."""
from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any

from .validation_service import validate_profile_dict

log = logging.getLogger(__name__)

DEFAULT_CONFIG: dict[str, Any] = {
    "ollama_model": "qwen2.5:3b",
    "ollama_base_url": "http://127.0.0.1:11434",
    "cdp_url": "http://127.0.0.1:9222",
    "request_timeout_seconds": 120,
    "ollama_retries": 2,
    "max_questions_per_ai_call": 4,
    "min_match_confidence": 0.7,
    "max_answer_chars": 1500,
    "mark_generated_drafts": False,
    "form_ready_timeout_seconds": 30,
    "enable_notifications": True,
}

# canonical field -> accepted keys in profile.json (first non-empty wins).
# Identifiers are deliberately NOT interchangeable.
CANONICAL_FIELDS: dict[str, list[str]] = {
    "full_name": ["full_name", "name"],
    "enrollment_number": ["enrollment_number", "enrollment_no", "enrollment_id", "enrolment_number"],
    "roll_number": ["roll_number", "roll_no"],
    "registration_number": ["registration_number", "registration_no"],
    "registered_email": ["registered_email", "email", "email_address"],
    "phone": ["phone", "phone_number", "mobile", "contact_number", "contact"],
    "college": ["college", "college_name", "university", "institute"],
    "degree": ["degree", "qualification"],
    "branch": ["branch", "degree_branch", "specialization"],
    "batch": ["batch", "batch_year", "graduation_year", "passing_year"],
    "linkedin_url": ["linkedin_url", "linkedin"],
    "github_url": ["github_url", "github"],
    "shortlisting_mail_received": [
        "shortlisting_mail_received",
        "shortlisting_mail_received_status",
    ],
}

_AI_CONTEXT_KEYS = [
    "college", "degree", "branch", "batch", "skills", "projects", "internships",
    "career_interests", "interests", "about", "summary", "bio",
]


def load_config(path: Path) -> dict[str, Any]:
    cfg = dict(DEFAULT_CONFIG)
    if path.exists():
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("config.json must contain a JSON object")
        cfg.update(data)
    else:
        save_config(path, cfg)
    return cfg


def save_config(path: Path, cfg: dict[str, Any]) -> None:
    path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


class ProfileService:
    def __init__(self, path: Path, example_path: Path | None = None) -> None:
        self.path = path
        self.example_path = example_path
        self.data: dict[str, Any] = {}

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            if self.example_path and self.example_path.exists():
                shutil.copy(self.example_path, self.path)
                log.info("Created profile.json from the example template")
            else:
                self.path.write_text("{}", encoding="utf-8")
        with self.path.open(encoding="utf-8") as f:
            data = json.load(f)
        errors = validate_profile_dict(data)
        if errors:
            raise ValueError("; ".join(errors))
        self.data = data
        return data

    def save(self, data: dict[str, Any]) -> None:
        errors = validate_profile_dict(data)
        if errors:
            raise ValueError("; ".join(errors))
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)  # atomic: never leaves a half-written profile
        self.data = data

    @staticmethod
    def _stringify(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, (list, tuple)):
            text = ", ".join(str(v).strip() for v in value if str(v).strip())
        elif isinstance(value, dict):
            return None
        else:
            text = str(value).strip()
        return text or None

    def get_value(self, canonical: str) -> str | None:
        """Return the profile value for a canonical field, or None if absent/blank."""
        keys = CANONICAL_FIELDS.get(canonical, [canonical])
        sources = [self.data]
        confirmed = self.data.get("confirmed_application_info")
        if isinstance(confirmed, dict):
            sources.append(confirmed)
        for src in sources:
            lowered = {str(k).lower(): v for k, v in src.items()}
            for key in keys:
                text = self._stringify(lowered.get(key.lower()))
                if text:
                    return text
        return None

    def ai_context(self) -> dict[str, Any]:
        """Minimal context for drafting open-ended answers (no contact/ID data)."""
        lowered = {str(k).lower(): v for k, v in self.data.items()}
        return {k: lowered[k] for k in _AI_CONTEXT_KEYS if lowered.get(k) not in (None, "", [], {})}
