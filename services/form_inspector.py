"""Read Google Forms questions from a Playwright page (read-only)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .models import Question

LISTITEM = '[role="listitem"]'


class FormAccessError(Exception):
    """Login required, form closed, or no questions found."""


@dataclass
class InspectionResult:
    questions: list[Question]
    notes: list[str] = field(default_factory=list)


_EXTRACT_JS = """() => {
  const label = el => (el.getAttribute('data-value') || el.getAttribute('aria-label') || el.textContent || '').trim();
  const out = [];
  document.querySelectorAll('[role="listitem"]').forEach((card, i) => {
    if (card.parentElement && card.parentElement.closest('[role="listitem"]')) return;
    if (!(card.offsetWidth || card.offsetHeight)) return;
    const heading = card.querySelector('[role="heading"]');
    out.push({
      index: i,
      heading: heading ? heading.innerText : '',
      fullText: card.innerText || '',
      required: !!card.querySelector('[aria-label="Required question"]'),
      hasFile: !!card.querySelector('input[type="file"]'),
      radiogroups: card.querySelectorAll('[role="radiogroup"]').length,
      radios: Array.from(card.querySelectorAll('[role="radio"]')).map(label),
      checkboxes: Array.from(card.querySelectorAll('[role="checkbox"]')).map(label),
      groups: card.querySelectorAll('[role="group"]').length,
      hasGrid: !!card.querySelector('[role="grid"], table'),
      hasListbox: !!card.querySelector('[role="listbox"]'),
      listboxOptions: Array.from(card.querySelectorAll('[role="option"]')).map(label),
      selectOptions: Array.from(card.querySelectorAll('select option')).map(o => o.textContent.trim()),
      inputs: Array.from(card.querySelectorAll('input:not([type=hidden]):not([type=radio]):not([type=checkbox]):not([type=file])'))
               .map(e => ({type: (e.getAttribute('type') || 'text').toLowerCase(), aria: (e.getAttribute('aria-label') || '')})),
      textareas: card.querySelectorAll('textarea').length,
    });
  });
  return out;
}"""


def clean_question_text(heading: str, full_text: str) -> str:
    text = heading or (full_text.strip().splitlines() or [""])[0]
    text = re.sub(r"\bRequired question\b", " ", text)
    text = re.sub(r"\s*\*\s*$", "", text.strip())
    return re.sub(r"\s+", " ", text).strip()[:500]


def _clean_options(labels: list[str]) -> list[str]:
    out: list[str] = []
    for lab in labels:
        lab = re.sub(r"\s+", " ", lab or "").strip()
        if not lab or lab.startswith("__") or lab.lower() in {"choose", "select"}:
            continue
        if lab not in out:
            out.append(lab)
    return out


def classify_control(raw: dict[str, Any]) -> tuple[str, list[str], str]:
    """Decide the question kind from raw DOM facts. Returns (kind, options, note)."""
    if raw.get("hasFile"):
        return "file_upload", [], "File upload"
    if raw.get("radiogroups", 0) > 1 or raw.get("hasGrid") or (
        raw.get("checkboxes") and raw.get("groups", 0) > 1
    ):
        return "grid", [], "Multiple-choice / checkbox grids are not supported yet"
    if raw.get("radios"):
        return "radio", _clean_options(raw["radios"]), ""
    if raw.get("checkboxes"):
        return "checkbox", _clean_options(raw["checkboxes"]), ""
    if raw.get("hasListbox") or raw.get("selectOptions"):
        return "dropdown", _clean_options(raw.get("listboxOptions", []) or raw.get("selectOptions", [])), ""
    inputs = raw.get("inputs", [])
    if any(i.get("aria", "").lower() in {"hour", "minute"} for i in inputs):
        return "time", [], "Time controls are not supported yet"
    if raw.get("textareas"):
        return "paragraph", [], ""
    types = [i.get("type", "text") for i in inputs]
    if "date" in types:
        return "date", [], ""
    if "number" in types:
        return "number", [], ""
    if "email" in types:
        return "email", [], ""
    if "tel" in types:
        return "phone", [], ""
    if inputs:
        return "text", [], ""
    return "unsupported", [], "No supported input control found"


def inspect_form(page: Any, ready_timeout_s: int = 30) -> InspectionResult:
    """Extract the visible questions. Never clicks anything."""
    if "accounts.google.com" in (page.url or ""):
        raise FormAccessError("The form requires you to sign in. Log in inside Brave, then press Analyze again.")
    try:
        page.wait_for_selector(LISTITEM, state="visible", timeout=ready_timeout_s * 1000)
    except Exception as exc:
        raise FormAccessError(
            "No questions detected. The form may still be loading, closed, or require login."
        ) from exc
    rows = page.evaluate(_EXTRACT_JS)
    questions: list[Question] = []
    for raw in rows:
        text = clean_question_text(raw.get("heading", ""), raw.get("fullText", ""))
        if not text:
            continue
        kind, options, note = classify_control(raw)
        questions.append(Question(
            id=len(questions) + 1, text=text, kind=kind, options=options,
            required=bool(raw.get("required")) or raw.get("heading", "").strip().endswith("*"),
            dom_index=int(raw["index"]), note=note,
        ))
    if not questions:
        raise FormAccessError("No questions detected on this page.")
    notes: list[str] = []
    try:
        if page.get_by_role("button", name=re.compile(r"^Next$")).count():
            notes.append("Multi-section form: only the current section was analyzed. "
                         "Review it, move to the next section manually, and analyze again.")
    except Exception:
        pass
    return InspectionResult(questions, notes)
