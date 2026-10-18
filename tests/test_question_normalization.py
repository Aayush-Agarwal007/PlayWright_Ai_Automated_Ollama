import pytest
from services.question_matcher import normalize_question


@pytest.mark.parametrize("raw,expected", [
    ("  Enter your E-mail ID *  ", "enter your email id"),
    ("Provide your electronic mail", "provide your email"),
    ("Mail ID", "email id"),
    ("Enrolment No.", "enrollment no"),
    ("Academic Specialisation", "academic specialization"),
    ("Mob. No", "mobile number"),
    ("Passing-out year", "passing out year"),
    ("Name\nRequired question", "name"),
    ("", ""),
])
def test_normalize(raw, expected):
    assert normalize_question(raw) == expected
