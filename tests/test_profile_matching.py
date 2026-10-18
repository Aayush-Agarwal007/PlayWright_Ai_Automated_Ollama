import json
import pytest
from services.profile_service import ProfileService
from services.question_matcher import match_question


@pytest.mark.parametrize("q", [
    "Enter your email ID.", "Provide your email address.", "Enter your electronic mail.",
    "What is your registered e-mail?", "Email ID / E-mail address",
    "Provide the email associated with your registration.", "Email",
])
def test_email_wordings(q):
    assert match_question(q).field == "registered_email"


@pytest.mark.parametrize("q,field", [
    ("Full name", "full_name"), ("Candidate name", "full_name"), ("Applicant Name *", "full_name"),
    ("Name", "full_name"), ("Phone", "phone"), ("Mobile number", "phone"),
    ("Contact number", "phone"), ("Enrollment number", "enrollment_number"),
    ("Roll number", "roll_number"), ("Registration number", "registration_number"),
    ("Branch", "branch"), ("Academic specialization", "branch"),
    ("Graduation batch", "batch"), ("Passing-out year", "batch"), ("Batch", "batch"),
    ("College name", "college"),
])
def test_other_fields(q, field):
    assert match_question(q).field == field


def test_identifiers_not_interchangeable():
    assert match_question("Roll number").field != "enrollment_number"
    assert match_question("Registration number").field != "enrollment_number"


@pytest.mark.parametrize("q", ["Parent's phone number", "Alternate email", "Team name", "Name of your college"])
def test_ambiguous_or_other(q):
    m = match_question(q)
    assert m.field not in ("phone", "registered_email", "full_name") or q == "Name of your college"


def test_ambiguous_flag():
    assert match_question("Alternate email address").ambiguous
    assert match_question("Guardian's mobile number").ambiguous


def test_factual_and_open_ended_flags():
    assert match_question("What is your CGPA?").factual
    assert match_question("Have you ever received an offer?").factual
    assert match_question("Why do you want to join this program?").open_ended


def test_profile_lookup_and_missing(tmp_path):
    p = tmp_path / "profile.json"
    p.write_text(json.dumps({"name": "A B", "email": " ", "skills": ["Python", "SQL"],
                             "confirmed_application_info": {"shortlisting_mail_received": "No"}}))
    svc = ProfileService(p); svc.load()
    assert svc.get_value("full_name") == "A B"
    assert svc.get_value("registered_email") is None       # blank -> missing
    assert svc.get_value("phone") is None
    assert svc.get_value("shortlisting_mail_received") == "No"
    assert svc.get_value("skills") == "Python, SQL"
    assert "full_name" not in svc.ai_context() and "skills" in svc.ai_context()


def test_save_is_validated(tmp_path):
    svc = ProfileService(tmp_path / "p.json"); svc.load()
    with pytest.raises(ValueError):
        svc.save({"": "x"})
    svc.save({"full_name": "Z"})
    assert json.loads((tmp_path / "p.json").read_text())["full_name"] == "Z"
