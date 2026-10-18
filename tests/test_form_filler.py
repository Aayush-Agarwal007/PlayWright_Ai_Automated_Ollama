import pytest
from services.form_filler import SubmissionBlocked, fill_all, fill_question, is_submit_label, safe_click
from services.form_inspector import classify_control, clean_question_text
from services.models import ST_ANALYZED, ST_FAILED, ST_SKIPPED, ST_VERIFIED, AnswerPlan, Question
import threading


class El:
    def __init__(self, text="", attrs=None, value="", sticky=True):
        self.text, self.attrs, self.value, self.sticky = text, dict(attrs or {}), value, sticky
        self.clicked = 0
    def inner_text(self, timeout=0): return self.text
    def get_attribute(self, n): return self.attrs.get(n)
    def is_visible(self): return True
    def is_enabled(self): return True
    def fill(self, v, timeout=0): self.value = v if self.sticky else "tampered"
    def input_value(self): return self.value
    def click(self, timeout=0):
        self.clicked += 1
        self.attrs["aria-checked"] = "true"


class Loc:
    def __init__(self, els): self.els = els
    def count(self): return len(self.els)
    def nth(self, i): return self.els[i]
    @property
    def first(self): return self.els[0]


class Card:
    def __init__(self, inputs=(), radios=()): self.inputs, self.radios = list(inputs), list(radios)
    def locator(self, sel):
        if 'role="radio"' in sel: return Loc(self.radios)
        if "input" in sel: return Loc(self.inputs)
        return Loc([])


class Page:
    def __init__(self, cards): self.cards = cards
    def locator(self, sel): return Loc(self.cards)
    def wait_for_timeout(self, ms): pass


def plan(q, answer, action="text"):
    return AnswerPlan(qid=q.id, question=q.text, kind=q.kind, answer=answer, action=action,
                      status=ST_ANALYZED)


def test_text_fill_verified():
    q = Question(1, "Name", "text", dom_index=0)
    el = El()
    res = fill_question(Page([Card(inputs=[el])]), q, plan(q, "Asha"), {})
    assert res.status == ST_VERIFIED and el.value == "Asha"


def test_text_fill_mismatch_is_failure_not_success():
    q = Question(1, "Name", "text", dom_index=0)
    res = fill_question(Page([Card(inputs=[El(sticky=False)])]), q, plan(q, "Asha"), {})
    assert res.status == ST_FAILED


def test_ambiguous_inputs_fail_safely():
    q = Question(1, "Name", "text", dom_index=0)
    res = fill_question(Page([Card(inputs=[El(), El()])]), q, plan(q, "x"), {})
    assert res.status == ST_FAILED


def test_radio_select_and_verify():
    q = Question(1, "Branch", "radio", ["CS", "ME"], dom_index=0)
    cs, me = El(attrs={"data-value": "CS"}), El(attrs={"data-value": "ME"})
    res = fill_question(Page([Card(radios=[cs, me])]), q, plan(q, "CS", "one"), {})
    assert res.status == ST_VERIFIED and cs.clicked == 1 and me.clicked == 0


def test_radio_missing_option_fails():
    q = Question(1, "Branch", "radio", ["CS"], dom_index=0)
    res = fill_question(Page([Card(radios=[El(attrs={"data-value": "ME"})])]), q, plan(q, "CS", "one"), {})
    assert res.status == ST_FAILED


def test_not_ready_plans_are_skipped():
    q = Question(1, "x", "text", dom_index=0)
    p = plan(q, "v"); p.status = "Needs review"
    assert fill_question(Page([Card(inputs=[El()])]), q, p, {}).status == ST_SKIPPED


def test_exception_in_one_question_does_not_stop_others():
    q1, q2 = Question(1, "a", "text", dom_index=5), Question(2, "b", "text", dom_index=0)  # idx 5 -> IndexError
    out = []
    res = fill_all(Page([Card(inputs=[El()])]), [q1, q2], {1: plan(q1, "x"), 2: plan(q2, "y")}, {},
                   threading.Event(), out.append)
    assert [r.status for r in res] == [ST_FAILED, ST_VERIFIED]


def test_submit_is_never_clicked():
    assert is_submit_label("Submit") and is_submit_label(" send ") and not is_submit_label("Submitted by me")
    btn = El(text="Submit")
    with pytest.raises(SubmissionBlocked):
        safe_click(btn)
    assert btn.clicked == 0
    sendish = El(text="ok", attrs={"aria-label": "Send"})
    with pytest.raises(SubmissionBlocked):
        safe_click(sendish)


def test_cancel_raises():
    from services.models import OperationCancelled
    ev = threading.Event(); ev.set()
    q = Question(1, "a", "text", dom_index=0)
    with pytest.raises(OperationCancelled):
        fill_all(Page([Card(inputs=[El()])]), [q], {1: plan(q, "x")}, {}, ev, lambda r: None)


def test_classify_control():
    assert classify_control({"hasFile": True})[0] == "file_upload"
    assert classify_control({"radiogroups": 3})[0] == "grid"
    k, opts, _ = classify_control({"radios": ["A", "B", "__other_option__"], "radiogroups": 1})
    assert k == "radio" and opts == ["A", "B"]
    assert classify_control({"hasListbox": True, "listboxOptions": ["Choose", "X", "Y"]}) == ("dropdown", ["X", "Y"], "")
    assert classify_control({"textareas": 1})[0] == "paragraph"
    assert classify_control({"inputs": [{"type": "text", "aria": "Hour"}, {"type": "text", "aria": "Minute"}]})[0] == "time"
    assert classify_control({"inputs": [{"type": "date", "aria": ""}]})[0] == "date"
    assert classify_control({})[0] == "unsupported"


def test_clean_question_text():
    assert clean_question_text("Your name *", "") == "Your name"
    assert clean_question_text("", "Email\nRequired question") == "Email"
