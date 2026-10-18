"""Question normalisation and deterministic semantic matching.

Deterministic rules handle obvious wordings without calling the LLM.
Anything else is passed to the AI classifier (see answer_planner).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_SPELLING = [
    (r"\be[\s\-]?mail\b", "email"),
    (r"\belectronic mail\b", "email"),
    (r"\bmail id\b", "email id"),
    (r"\benrolment\b", "enrollment"),
    (r"\bspecialisation\b", "specialization"),
    (r"\bprogramme\b", "program"),
    (r"\bmob(?:ile)?\.? ?(no|num|number)\b", "mobile number"),
    (r"\bph(?:one)?\.? ?(no|num|number)\b", "phone number"),
    (r"\bpassing[\s\-]out\b", "passing out"),
    (r"\bpass[\s\-]out\b", "passing out"),
]


def normalize_question(text: str) -> str:
    """Lower-case, unify spellings, drop punctuation / required markers."""
    t = unicodedata.normalize("NFKC", str(text or "")).lower()
    t = re.sub(r"\brequired question\b", " ", t)
    for pattern, repl in _SPELLING:
        t = re.sub(pattern, repl, t)
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


@dataclass
class MatchResult:
    field: str | None
    confidence: float
    source: str  # "rule" | "ai" | "none"
    reason: str
    ambiguous: bool = False
    factual: bool = False
    open_ended: bool = False


_QUALIFIERS = re.compile(
    r"\b(alternate|alternative|secondary|parent|parents|guardian|father|mother|emergency|"
    r"friend|referee|reference|teammate|team member|whatsapp|official|company|organization|"
    r"organisation|college email|institute email)\b"
)
_NAME_EXCLUDE = re.compile(
    r"\b(college|university|company|team|project|father|mother|parent|guardian|"
    r"organization|organisation|institute|branch|event|course|school)\b"
)

# (canonical field, include regex, exclude regex or None, subject to qualifier check)
_RULES: list[tuple[str, re.Pattern, re.Pattern | None, bool]] = [
    ("shortlisting_mail_received", re.compile(r"shortlist"), None, False),
    ("enrollment_number", re.compile(r"\benrollment\b"), None, False),
    ("roll_number", re.compile(r"\broll (number|no|num|id)\b"), None, False),
    ("registration_number", re.compile(r"\bregistration (number|no|num|id)\b"), None, False),
    ("registered_email", re.compile(r"\bemail\b"), None, True),
    (
        "phone",
        re.compile(r"\b(phone|mobile|telephone|cell ?phone)\b|\bcontact (number|no|num)\b|^contact$"),
        None,
        True,
    ),
    (
        "full_name",
        re.compile(
            r"\b(full name|candidate name|applicant name|participant name|student name|your name|"
            r"name of (the )?(candidate|applicant|participant|student))\b|^name$"
        ),
        _NAME_EXCLUDE,
        True,
    ),
    ("college", re.compile(r"\b(college|university|institute|institution)\b"), None, False),
    ("degree", re.compile(r"\b(degree|qualification|program of study)\b"), None, False),
    ("branch", re.compile(r"\b(branch|specialization|stream|department|discipline)\b"), None, False),
    (
        "batch",
        re.compile(
            r"\b(batch|graduation year|passing out year|year of (graduation|passing out)|"
            r"graduating year|graduation batch)\b"
        ),
        None,
        False,
    ),
    ("linkedin_url", re.compile(r"\blinkedin\b"), None, False),
    ("github_url", re.compile(r"\bgithub\b"), None, False),
]

_FACTUAL = re.compile(
    r"\b(shortlisted|cgpa|gpa|percentage|marks|grade|date of birth|dob|age|gender|address|aadhaar|"
    r"aadhar|passport|salary|ctc|years of experience|have you (ever|already|previously)|"
    r"did you (receive|get)|received (a|any|the)|contacted by|recruiter|declare|declaration|"
    r"certify|i agree|consent|eligible|eligibility|citizenship|nationality)\b"
)

_OPEN_ENDED = re.compile(
    r"\b(why|describe|explain|tell us|what do you hope|what are you looking|hope to|expect|"
    r"motivat\w*|interested|interest|goals?|aspirations?|how (do|would|will|can) you|"
    r"your thoughts|suggestions?|feedback|about yourself|strengths?|learn|opinion)\b"
)


def match_question(text: str) -> MatchResult:
    """Deterministic classification of a question."""
    q = normalize_question(text)
    if not q:
        return MatchResult(None, 0.0, "none", "empty question")
    qualified = bool(_QUALIFIERS.search(q))
    for fld, include, exclude, check_qualifier in _RULES:
        if not include.search(q):
            continue
        if exclude and exclude.search(q):
            continue
        if check_qualifier and qualified:
            return MatchResult(
                None, 0.0, "none",
                f"'{fld}' wording has a qualifier (alternate/parent/etc.); not guessing",
                ambiguous=True,
            )
        return MatchResult(fld, 0.95, "rule", f"deterministic rule for {fld}")
    if _FACTUAL.search(q):
        return MatchResult(None, 0.0, "none", "factual question without profile field", factual=True)
    if _OPEN_ENDED.search(q):
        return MatchResult(None, 0.0, "none", "open-ended question", open_ended=True)
    return MatchResult(None, 0.0, "none", "no deterministic match")
