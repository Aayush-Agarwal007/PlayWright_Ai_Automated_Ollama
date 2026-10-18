"""Fill and VERIFY answers in Google Forms. Never submits.

Only option / input elements *inside a question card* are ever clicked, and every
click goes through safe_click(), which refuses Submit/Send-style controls.
"""
from __future__ import annotations

import logging
import re
import threading
from typing import Any, Callable

from .models import (
    ST_FAILED, ST_FILLED, ST_SKIPPED, ST_VERIFIED, AnswerPlan, FillResult,
    OperationCancelled, Question,
)
from .validation_service import normalize_ws, verify_value

log = logging.getLogger(__name__)

_FORBIDDEN = re.compile(r"^\s*(submit|send|submit form|send form|submit response)\s*$", re.I)
_TEXT_INPUTS = ('input:not([type="hidden"]):not([type="radio"]):not([type="checkbox"])'
                ':not([type="file"]):not([type="submit"]), textarea')


class SubmissionBlocked(RuntimeError):
    """Raised if code ever tries to click a final-submission control."""


def is_submit_label(text: str | None) -> bool:
    return bool(text and _FORBIDDEN.match(text))


def safe_click(locator: Any, timeout: int = 2500) -> None:
    for getter in (lambda: locator.inner_text(timeout=800), lambda: locator.get_attribute("aria-label")):
        try:
            if is_submit_label(getter()):
                raise SubmissionBlocked("Refusing to click a Submit/Send control")
        except SubmissionBlocked:
            raise
        except Exception:
            pass
    locator.click(timeout=timeout)


def _label(el: Any) -> str:
    for getter in (lambda: el.get_attribute("data-value"), lambda: el.get_attribute("aria-label"),
                   lambda: el.inner_text(timeout=500)):
        try:
            v = getter()
            if v and v.strip():
                return normalize_ws(v)
        except Exception:
            continue
    return ""


def _visible_inputs(card: Any) -> list[Any]:
    loc = card.locator(_TEXT_INPUTS)
    found = []
    for i in range(loc.count()):
        el = loc.nth(i)
        try:
            if el.is_visible() and el.is_enabled():
                found.append(el)
        except Exception:
            continue
    return found


def _wanted(plan: AnswerPlan) -> list[str]:
    return list(plan.answer) if isinstance(plan.answer, list) else [str(plan.answer)]


def _fill_text(card: Any, q: Question, plan: AnswerPlan, cfg: dict) -> FillResult:
    inputs = _visible_inputs(card)
    if len(inputs) != 1:
        return FillResult(q.id, ST_FAILED, f"Expected exactly one input, found {len(inputs)}")
    value = str(plan.answer)[: int(cfg.get("max_answer_chars", 1500))]
    if plan.is_draft and cfg.get("mark_generated_drafts"):
        value = "[AI DRAFT — REVIEW] " + value
    el = inputs[0]
    el.fill(value, timeout=3000)
    if verify_value(value, el.input_value()):
        return FillResult(q.id, ST_VERIFIED, "Value verified in the field")
    return FillResult(q.id, ST_FAILED, "Field value did not match after filling")


def _fill_toggle(page: Any, card: Any, q: Question, plan: AnswerPlan) -> FillResult:
    role = "radio" if q.kind == "radio" else "checkbox"
    els = card.locator(f'[role="{role}"]')
    index: dict[str, Any] = {}
    for i in range(els.count()):
        el = els.nth(i)
        index.setdefault(_label(el).casefold(), el)
    wanted = _wanted(plan)
    missing = [w for w in wanted if normalize_ws(w).casefold() not in index]
    if missing:
        return FillResult(q.id, ST_FAILED, f"Option not found on page: {missing[0]}")
    for w in wanted:
        el = index[normalize_ws(w).casefold()]
        if el.get_attribute("aria-checked") != "true":
            safe_click(el)
    if page is not None:
        page.wait_for_timeout(150)
    for w in wanted:
        if index[normalize_ws(w).casefold()].get_attribute("aria-checked") != "true":
            return FillResult(q.id, ST_FAILED, f"Option '{w}' not selected after click")
    if role == "radio":
        others = [k for k, el in index.items()
                  if k != normalize_ws(wanted[0]).casefold() and el.get_attribute("aria-checked") == "true"]
        if others:
            return FillResult(q.id, ST_FAILED, "A different option is also selected")
    return FillResult(q.id, ST_VERIFIED, "Selection verified")


def _fill_dropdown(page: Any, card: Any, q: Question, plan: AnswerPlan) -> FillResult:
    target = normalize_ws(plan.answer).casefold()
    select = card.locator("select")
    if select.count() == 1:
        select.first.select_option(label=str(plan.answer))
        chosen = select.first.evaluate("e => e.options[e.selectedIndex].text")
        ok = normalize_ws(chosen).casefold() == target
        return FillResult(q.id, ST_VERIFIED if ok else ST_FAILED,
                          "Selection verified" if ok else "Selected option differs from intended")
    box = card.locator('[role="listbox"]').first
    if not box.count():
        return FillResult(q.id, ST_FAILED, "Dropdown control not found")
    safe_click(box)
    if page is not None:
        page.wait_for_timeout(300)
    opts = page.locator('[role="option"]') if page is not None else card.locator('[role="option"]')
    hit = None
    for i in range(opts.count()):
        el = opts.nth(i)
        try:
            if el.is_visible() and _label(el).casefold() == target:
                hit = el
                break
        except Exception:
            continue
    if hit is None:
        if page is not None:
            page.keyboard.press("Escape")
        return FillResult(q.id, ST_FAILED, "Option not found in dropdown list")
    safe_click(hit)
    if page is not None:
        page.wait_for_timeout(300)
    shown = normalize_ws(box.inner_text(timeout=1500)).casefold()
    ok = target in shown
    return FillResult(q.id, ST_VERIFIED if ok else ST_FAILED,
                      "Selection verified" if ok else "Dropdown does not show the intended option")


def fill_question(page: Any, q: Question, plan: AnswerPlan, cfg: dict) -> FillResult:
    """Fill one question. Never raises (except cancellation handled by caller)."""
    if not plan.ready_to_fill:
        return FillResult(q.id, ST_SKIPPED, plan.reason or "Not ready to fill")
    try:
        card = page.locator('[role="listitem"]').nth(q.dom_index)
        if q.kind in ("radio", "checkbox"):
            return _fill_toggle(page, card, q, plan)
        if q.kind == "dropdown":
            return _fill_dropdown(page, card, q, plan)
        if q.kind in ("text", "paragraph", "email", "phone", "number", "date"):
            return _fill_text(card, q, plan, cfg)
        return FillResult(q.id, ST_SKIPPED, f"Unsupported control: {q.kind}")
    except SubmissionBlocked as exc:
        return FillResult(q.id, ST_FAILED, str(exc))
    except Exception as exc:
        log.warning("Fill failed for Q%d (%s): %s", q.id, q.kind, exc.__class__.__name__)
        return FillResult(q.id, ST_FAILED, f"{exc.__class__.__name__}: {str(exc)[:160]}")


def fill_all(page: Any, questions: list[Question], plans: dict[int, AnswerPlan], cfg: dict,
             cancel: threading.Event, on_result: Callable[[FillResult], None]) -> list[FillResult]:
    results: list[FillResult] = []
    for q in questions:
        if cancel.is_set():
            raise OperationCancelled()
        plan = plans.get(q.id)
        if plan is None or not plan.ready_to_fill:
            continue
        res = fill_question(page, q, plan, cfg)
        results.append(res)
        on_result(res)
    return results
