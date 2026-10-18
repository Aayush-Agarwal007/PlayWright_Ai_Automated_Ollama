"""Shared data models and constants (no Qt / Playwright imports)."""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import Any

SOURCE_PROFILE = "Profile"
SOURCE_AI = "AI draft"
SOURCE_USER = "User-confirmed"
SOURCE_NONE = "Unanswered"

ST_ANALYZED = "Analyzed"
ST_FILLED = "Filled"
ST_VERIFIED = "Verified"
ST_REVIEW = "Needs review"
ST_SKIPPED = "Skipped"
ST_FAILED = "Failed"

CHOICE_KINDS = {"radio", "checkbox", "dropdown"}
TEXT_KINDS = {"text", "paragraph", "email", "phone", "number", "date"}
UNSUPPORTED_KINDS = {"grid", "time", "unsupported"}


class OperationCancelled(Exception):
    """Raised when the user presses Stop."""


@dataclass
class Question:
    id: int
    text: str
    kind: str
    options: list[str] = dc_field(default_factory=list)
    required: bool = False
    dom_index: int = -1
    note: str = ""


@dataclass
class AnswerPlan:
    qid: int
    question: str
    kind: str
    field: str | None = None
    answer: Any = ""
    action: str = "skip"          # text | one | many | skip
    source: str = SOURCE_NONE
    confidence: float = 0.0
    status: str = ST_REVIEW
    reason: str = ""
    is_draft: bool = False
    options: list[str] = dc_field(default_factory=list)

    @property
    def ready_to_fill(self) -> bool:
        return (
            self.action in ("text", "one", "many")
            and self.answer not in ("", None, [])
            and self.status == ST_ANALYZED
        )

    def answer_text(self) -> str:
        if isinstance(self.answer, list):
            return ", ".join(map(str, self.answer))
        return str(self.answer or "")


@dataclass
class FillResult:
    qid: int
    status: str
    detail: str = ""
