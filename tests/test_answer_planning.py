import json
import pytest
from services.answer_planner import AnswerPlanner, fuzzy_match_options, match_options
from services.models import (
    SOURCE_AI, SOURCE_NONE, SOURCE_PROFILE, ST_ANALYZED, ST_REVIEW, ST_SKIPPED, Question,
)
from services.ollama_service import OllamaError
from services.profile_service import ProfileService

CFG = {"max_questions_per_ai_call": 4, "ollama_retries": 1, "min_match_confidence": 0.7}


class FakeOllama:
    def __init__(self, classify=None, draft=None, pick=None, raises=None):
        self._c, self._d, self._p, self.raises = classify, draft, pick, raises
        self.calls = {"classify": 0, "draft": 0, "pick": 0}

    def _next(self, seq, name):
        self.calls[name] += 1
        if self.raises:
            raise self.raises
        item = seq[min(self.calls[name] - 1, len(seq) - 1)] if isinstance(seq, list) else seq
        return item(self) if callable(item) else item

    def classify(self, qs, allowed): return self._next(self._c or {}, "classify")
    def draft(self, qs, ctx): return self._next(self._d or {}, "draft")
    def pick_option(self, q, v, o): return self._next(self._p or {}, "pick")


@pytest.fixture
def profile(tmp_path):
    p = tmp_path / "profile.json"
    p.write_text(json.dumps({"full_name": "Asha Rao", "registered_email": "a@x.com", "branch": "Computer Science",
                             "skills": ["Python"], "batch": "2026"}))
    s = ProfileService(p); s.load()
    return s


def Q(i, text, kind="text", options=None):
    return Question(id=i, text=text, kind=kind, options=options or [], dom_index=i)


def test_profile_first_no_ai_call(profile):
    ai = FakeOllama()
    plans = AnswerPlanner(profile, ai, CFG).plan([Q(1, "Provide your email address"), Q(2, "Candidate name")])
    assert [p.answer for p in plans] == ["a@x.com", "Asha Rao"]
    assert all(p.source == SOURCE_PROFILE and p.status == ST_ANALYZED for p in plans)
    assert sum(ai.calls.values()) == 0


def test_missing_profile_value_not_fabricated(profile):
    ai = FakeOllama()
    p = AnswerPlanner(profile, ai, CFG).plan([Q(1, "Mobile number")])[0]
    assert p.status == ST_REVIEW and p.source == SOURCE_NONE and p.answer == ""
    assert sum(ai.calls.values()) == 0


def test_open_ended_gets_ai_draft(profile):
    ai = FakeOllama(draft={"answers": [{"id": 1, "action": "text", "answer": "I want to learn.",
                                        "draft": True, "confidence": "high", "reason": "ok"}]})
    p = AnswerPlanner(profile, ai, CFG).plan([Q(1, "Why are you interested in this opportunity?", "paragraph")])[0]
    assert p.source == SOURCE_AI and p.is_draft and p.status == ST_ANALYZED


def test_unknown_factual_never_drafted(profile):
    ai = FakeOllama()
    p = AnswerPlanner(profile, ai, CFG).plan([Q(1, "What is your CGPA?"), Q(2, "Have you received a recruiter call?")])
    assert all(x.status == ST_REVIEW and x.answer == "" for x in p)
    assert sum(ai.calls.values()) == 0


def test_ambiguous_qualifier_not_matched(profile):
    p = AnswerPlanner(profile, FakeOllama(), CFG).plan([Q(1, "Alternate email address")])[0]
    assert p.answer == "" and p.status == ST_REVIEW


def test_ai_classification_maps_odd_wording(profile):
    ai = FakeOllama(classify={"classifications": [
        {"id": 1, "canonical_field": "full_name", "confidence": "high", "reason": "name"}]})
    p = AnswerPlanner(profile, ai, CFG).plan([Q(1, "What should we print on your certificate?")])[0]
    assert p.answer == "Asha Rao" and p.field == "full_name"


def test_low_confidence_classification_rejected(profile):
    ai = FakeOllama(classify={"classifications": [
        {"id": 1, "canonical_field": "full_name", "confidence": "low"}]})
    p = AnswerPlanner(profile, ai, CFG).plan([Q(1, "Something odd")])[0]
    assert p.answer == "" and p.status == ST_REVIEW


def test_invalid_ai_ids_and_malformed_response(profile):
    bad = {"answers": [{"id": 77, "action": "text", "answer": "ghost"}]}
    ai = FakeOllama(draft=[bad, "not a dict"])
    p = AnswerPlanner(profile, ai, CFG).plan([Q(1, "Why do you want to join?", "paragraph")])[0]
    assert p.status == ST_REVIEW and p.answer == ""
    assert ai.calls["draft"] == 2          # initial + 1 retry, then gives up


def test_retry_recovers_missing_ids(profile):
    first = {"answers": [{"id": 1, "action": "text", "answer": "A", "confidence": "high"}]}
    second = {"answers": [{"id": 2, "action": "text", "answer": "B", "confidence": "high"}]}
    ai = FakeOllama(draft=[first, second])
    plans = AnswerPlanner(profile, ai, CFG).plan(
        [Q(1, "Why this program?", "paragraph"), Q(2, "What do you hope to learn?", "paragraph")])
    assert [p.answer for p in plans] == ["A", "B"]


def test_wrong_action_type_rejected(profile):
    ai = FakeOllama(draft={"answers": [{"id": 1, "action": "delete_everything", "answer": "x"}]})
    p = AnswerPlanner(profile, ai, {**CFG, "ollama_retries": 0}).plan([Q(1, "Why join?", "paragraph")])[0]
    assert p.answer == ""


def test_ollama_down_does_not_crash(profile):
    ai = FakeOllama(raises=OllamaError("down"))
    pl = AnswerPlanner(profile, ai, CFG)
    plans = pl.plan([Q(1, "Why join?", "paragraph"), Q(2, "Candidate name")])
    assert plans[0].status == ST_REVIEW and plans[1].answer == "Asha Rao"
    assert pl.warnings


def test_choice_exact_and_real_options_only(profile):
    q = Q(1, "Branch", "radio", ["Computer Science", "Mechanical"])
    p = AnswerPlanner(profile, None, CFG).plan([q])[0]
    assert p.action == "one" and p.answer == "Computer Science" and p.status == ST_ANALYZED


def test_choice_no_option_not_arbitrary(profile):
    q = Q(1, "Branch", "radio", ["Mechanical", "Civil"])
    p = AnswerPlanner(profile, None, CFG).plan([q])[0]
    assert p.answer == "" and p.status == ST_REVIEW


def test_choice_ai_pick_must_be_real_and_needs_review(profile):
    q = Q(1, "Branch", "dropdown", ["CSE", "ECE"])
    good = FakeOllama(pick={"option": "CSE", "confidence": "high"})
    p = AnswerPlanner(profile, good, CFG).plan([q])[0]
    assert p.answer == "CSE" and p.status == ST_REVIEW      # must be confirmed by the user
    fake = FakeOllama(pick={"option": "Astrology", "confidence": "high"})
    assert AnswerPlanner(profile, fake, CFG).plan([q])[0].answer == ""


def test_unsupported_and_upload(profile):
    plans = AnswerPlanner(profile, None, CFG).plan(
        [Q(1, "Grid", "grid"), Q(2, "Upload screenshot", "file_upload")])
    assert plans[0].status == ST_SKIPPED and plans[1].status == ST_REVIEW


def test_option_helpers():
    assert match_options(["yes"], ["Yes", "No"]) == ["Yes"]
    assert match_options(["Maybe"], ["Yes", "No"]) is None
    assert fuzzy_match_options(["Computer Science"], ["Computer Science and Engineering", "Mechanical"])[0] == \
        ["Computer Science and Engineering"]
    assert fuzzy_match_options(["xx"], ["Yes"]) is None
