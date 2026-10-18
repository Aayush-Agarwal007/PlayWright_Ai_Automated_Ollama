"""Profile-first, AI-fallback answer planning with strict validation."""
from __future__ import annotations

import difflib
import logging
import re
import threading
from typing import Any, Callable

from .models import (
    CHOICE_KINDS, ST_ANALYZED, ST_REVIEW, ST_SKIPPED, SOURCE_AI, SOURCE_NONE,
    SOURCE_PROFILE, UNSUPPORTED_KINDS, AnswerPlan, OperationCancelled, Question,
)
from .ollama_service import OllamaError
from .profile_service import CANONICAL_FIELDS
from .question_matcher import MatchResult, match_question
from .validation_service import (
    normalize_ws, parse_confidence, validate_answers, validate_classifications,
)

log = logging.getLogger(__name__)
Progress = Callable[[str, int], None]


# ---------- pure option helpers (unit-tested) ----------
def split_values(value: str) -> list[str]:
    return [v.strip() for v in re.split(r"[;,]", value) if v.strip()]


def match_options(wanted: list[str], options: list[str]) -> list[str] | None:
    """Exact (case/whitespace-insensitive) match of every wanted value."""
    by_cf = {normalize_ws(o).casefold(): o for o in options}
    out = []
    for w in wanted:
        hit = by_cf.get(normalize_ws(w).casefold())
        if hit is None:
            return None
        out.append(hit)
    return out or None


def fuzzy_match_options(wanted: list[str], options: list[str]) -> tuple[list[str], float] | None:
    """Unambiguous containment / similarity match. Returns (labels, similarity)."""
    labels: list[str] = []
    worst = 1.0
    for w in wanted:
        wc = normalize_ws(w).casefold()
        if len(wc) < 3:
            return None
        contained = [o for o in options if len(normalize_ws(o)) >= 3 and (
            wc in normalize_ws(o).casefold() or normalize_ws(o).casefold() in wc)]
        if len(contained) == 1:
            labels.append(contained[0]); worst = min(worst, 0.85); continue
        scored = sorted(
            ((difflib.SequenceMatcher(None, wc, normalize_ws(o).casefold()).ratio(), o) for o in options),
            reverse=True,
        )
        if not scored or scored[0][0] < 0.85 or (len(scored) > 1 and scored[1][0] >= scored[0][0] - 0.05):
            return None
        labels.append(scored[0][1]); worst = min(worst, scored[0][0])
    return (labels, worst) if labels else None


class AnswerPlanner:
    def __init__(self, profile: Any, ollama: Any | None, config: dict[str, Any]) -> None:
        self.profile, self.ollama, self.config = profile, ollama, config
        self.warnings: list[str] = []
        self.decisions: list[str] = []  # field names / sources only, never values

    # ---- config ----
    @property
    def min_conf(self) -> float:
        return float(self.config.get("min_match_confidence", 0.7))

    @property
    def batch_size(self) -> int:
        return max(1, int(self.config.get("max_questions_per_ai_call", 4)))

    @property
    def retries(self) -> int:
        return max(0, int(self.config.get("ollama_retries", 2)))

    # ---- plan constructors ----
    def _base(self, q: Question, **kw: Any) -> AnswerPlan:
        return AnswerPlan(qid=q.id, question=q.text, kind=q.kind, options=list(q.options), **kw)

    def _needs_input(self, q: Question, reason: str, field: str | None = None) -> AnswerPlan:
        self.decisions.append(f"Q{q.id}: needs input ({reason})")
        return self._base(q, field=field, reason=reason, status=ST_REVIEW, source=SOURCE_NONE)

    def _skipped(self, q: Question, reason: str) -> AnswerPlan:
        self.decisions.append(f"Q{q.id}: skipped ({reason})")
        return self._base(q, reason=reason, status=ST_SKIPPED, source=SOURCE_NONE)

    # ---- main entry ----
    def plan(self, questions: list[Question], cancel: threading.Event | None = None,
             progress: Progress | None = None) -> list[AnswerPlan]:
        cancel = cancel or threading.Event()
        progress = progress or (lambda m, p: None)
        self.warnings, self.decisions = [], []
        plans: dict[int, AnswerPlan] = {}
        to_classify: list[Question] = []
        to_draft: list[Question] = []

        for q in questions:
            self._check(cancel)
            routed = self._route(q)
            if isinstance(routed, AnswerPlan):
                plans[q.id] = routed
            elif routed == "draft":
                to_draft.append(q)
            else:
                to_classify.append(q)

        if to_classify:
            progress("Asking local AI to understand ambiguous questions…", 45)
            self._classify(to_classify, plans, to_draft, cancel)
        if to_draft:
            progress("Drafting answers to open-ended questions…", 70)
            self._draft(to_draft, plans, cancel)
        progress("Planning complete", 100)
        return [plans[q.id] for q in questions]

    def _route(self, q: Question) -> AnswerPlan | str:
        if q.kind == "file_upload":
            return self._needs_input(q, "File upload is not automated yet; attach the file manually.")
        if q.kind in UNSUPPORTED_KINDS:
            return self._skipped(q, q.note or f"Unsupported control type: {q.kind}")
        m = match_question(q.text)
        if m.field:
            return self._from_profile(q, m)
        if m.ambiguous:
            return self._needs_input(q, m.reason)
        if m.factual:
            return self._needs_input(q, "Personal factual question with no profile value; not guessing.")
        if m.open_ended and q.kind in ("text", "paragraph"):
            return "draft"
        return "classify"

    # ---- profile-backed answers ----
    def _from_profile(self, q: Question, m: MatchResult) -> AnswerPlan:
        value = self.profile.get_value(m.field)
        if value is None:
            return self._needs_input(q, f"Profile field '{m.field}' is empty", field=m.field)
        self.decisions.append(f"Q{q.id}: {m.field} via {m.source} (conf {m.confidence:.2f})")
        if q.kind in CHOICE_KINDS:
            return self._choice_from_value(q, m, value)
        return self._base(q, field=m.field, answer=value, action="text", source=SOURCE_PROFILE,
                          confidence=m.confidence, status=ST_ANALYZED, reason=m.reason)

    def _choice_from_value(self, q: Question, m: MatchResult, value: str) -> AnswerPlan:
        multi = q.kind == "checkbox"
        wanted = split_values(value) if multi else [value]
        action = "many" if multi else "one"

        def make(labels: list[str], conf: float, status: str, reason: str) -> AnswerPlan:
            return self._base(q, field=m.field, answer=labels if multi else labels[0], action=action,
                              source=SOURCE_PROFILE, confidence=conf, status=status, reason=reason)

        exact = match_options(wanted, q.options)
        if exact:
            return make(exact, m.confidence, ST_ANALYZED, "Exact option match with profile value")
        fuzzy = fuzzy_match_options(wanted, q.options)
        if fuzzy:
            labels, sim = fuzzy
            status = ST_ANALYZED if sim >= 0.9 else ST_REVIEW
            return make(labels, 0.8, status, "Close option match; please confirm")
        if self.ollama is not None and not multi:
            try:
                raw = self.ollama.pick_option(q.text, value, q.options)
                opt = normalize_ws(raw.get("option", "")) if isinstance(raw, dict) else ""
                conf = parse_confidence(raw.get("confidence")) if isinstance(raw, dict) else 0.0
                hit = match_options([opt], q.options) if opt else None
                if hit and conf >= self.min_conf:
                    return make(hit, 0.6, ST_REVIEW, "AI-suggested option; please confirm")
            except (OllamaError, ValueError) as exc:
                self.warnings.append(f"Option matching failed for Q{q.id}: {exc}")
        return self._needs_input(q, "Profile value matches no available option; choose manually.", field=m.field)

    # ---- AI classification ----
    def _classify(self, qs: list[Question], plans: dict[int, AnswerPlan],
                  to_draft: list[Question], cancel: threading.Event) -> None:
        if self.ollama is None:
            for q in qs:
                plans[q.id] = self._needs_input(q, "Ambiguous question and local AI is unavailable")
            return
        allowed = list(CANONICAL_FIELDS)
        for i in range(0, len(qs), self.batch_size):
            batch = qs[i:i + self.batch_size]
            pending = {q.id: q for q in batch}
            got: dict[int, dict] = {}
            try:
                for _ in range(self.retries + 1):
                    self._check(cancel)
                    if not pending:
                        break
                    raw = self.ollama.classify(
                        [{"id": q.id, "question": q.text, "kind": q.kind, "options": q.options}
                         for q in pending.values()], allowed)
                    valid, _missing = validate_classifications(raw, pending.keys(), allowed)
                    got.update(valid)
                    for k in valid:
                        pending.pop(k, None)
            except OllamaError as exc:
                self.warnings.append(f"AI classification failed: {exc}")
            for q in batch:
                c = got.get(q.id)
                if c is None:
                    plans[q.id] = self._needs_input(q, "AI returned no valid classification")
                    continue
                fld, conf = c["canonical_field"], c["confidence"]
                if fld in CANONICAL_FIELDS and conf >= self.min_conf:
                    plans[q.id] = self._from_profile(q, MatchResult(fld, conf, "ai", c["reason"] or "AI classification"))
                elif fld == "open_ended" and q.kind in ("text", "paragraph") and conf >= 0.5:
                    to_draft.append(q)
                else:
                    plans[q.id] = self._needs_input(q, f"Not matched reliably ({fld}, confidence {conf:.2f})")

    # ---- AI drafts ----
    def _draft(self, qs: list[Question], plans: dict[int, AnswerPlan], cancel: threading.Event) -> None:
        if self.ollama is None:
            for q in qs:
                plans[q.id] = self._needs_input(q, "Open-ended question and local AI is unavailable")
            return
        for i in range(0, len(qs), self.batch_size):
            batch = qs[i:i + self.batch_size]
            try:
                valid = self._draft_batch(batch, cancel)
            except OllamaError as exc:
                self.warnings.append(f"AI drafting failed: {exc}")
                valid = {}
            for q in batch:
                plans[q.id] = self._draft_plan(q, valid.get(q.id))

    def _draft_batch(self, batch: list[Question], cancel: threading.Event) -> dict[int, dict]:
        pending = {q.id: q for q in batch}
        out: dict[int, dict] = {}
        context = self.profile.ai_context()
        for _ in range(self.retries + 1):
            self._check(cancel)
            if not pending:
                break
            raw = self.ollama.draft(
                [{"id": q.id, "question": q.text, "required": q.required} for q in pending.values()], context)
            valid, _missing = validate_answers(raw, pending.keys(), {})
            out.update(valid)
            for k in valid:
                pending.pop(k, None)
        return out

    def _draft_plan(self, q: Question, a: dict | None) -> AnswerPlan:
        if not a or a["action"] != "text" or not a["answer"]:
            reason = (a or {}).get("reason") or "AI gave no usable draft"
            return self._needs_input(q, reason)
        limit = int(self.config.get("max_answer_chars", 1500))
        status = ST_ANALYZED if a["confidence"] >= 0.5 else ST_REVIEW
        self.decisions.append(f"Q{q.id}: AI draft (conf {a['confidence']:.2f})")
        return self._base(q, answer=a["answer"][:limit], action="text", source=SOURCE_AI,
                          confidence=a["confidence"], status=status, is_draft=True,
                          reason=a["reason"] or "Generated from profile context")

    def regenerate_draft(self, q: Question) -> AnswerPlan:
        if self.ollama is None:
            raise OllamaError("Local AI is unavailable")
        if q.kind not in ("text", "paragraph"):
            raise OllamaError("Only text questions can be drafted")
        valid = self._draft_batch([q], threading.Event())
        return self._draft_plan(q, valid.get(q.id))

    @staticmethod
    def _check(cancel: threading.Event) -> None:
        if cancel.is_set():
            raise OperationCancelled()
