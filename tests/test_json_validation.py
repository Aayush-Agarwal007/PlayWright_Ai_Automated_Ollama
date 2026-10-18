from services.validation_service import (
    parse_json_loose, validate_answers, validate_classifications, validate_form_url,
    validate_json_text, validate_profile_dict, verify_value, parse_confidence,
)


def test_json_text():
    assert validate_json_text('{"a": 1}')[0]
    ok, err = validate_json_text('{"a": ')
    assert not ok and "Invalid JSON" in err
    assert not validate_json_text("[1,2]")[0]


def test_profile_dict():
    assert validate_profile_dict({"a": 1}) == []
    assert validate_profile_dict({"": 1})
    assert validate_profile_dict([1])


def test_loose_json():
    assert parse_json_loose('```json\n{"a":1}\n```') == {"a": 1}
    assert parse_json_loose('Sure! {"a":2} done') == {"a": 2}
    import pytest
    with pytest.raises(ValueError):
        parse_json_loose("no json here")


def test_urls():
    assert validate_form_url("https://docs.google.com/forms/d/e/abc/viewform")[0]
    assert validate_form_url("https://forms.gle/abc123")[0]
    assert not validate_form_url("http://docs.google.com/forms/d/e/abc")[0]
    assert not validate_form_url("https://evil.com/forms/")[0]
    assert not validate_form_url("")[0]


def test_confidence():
    assert parse_confidence("high") == 0.9 and parse_confidence(0.5) == 0.5
    assert parse_confidence("??") == 0.0 and parse_confidence(5) == 1.0


def test_answers_invalid_ids_actions_and_options():
    raw = {"answers": [
        {"id": 1, "action": "text", "answer": "hello", "draft": True, "confidence": "high"},
        {"id": 99, "action": "text", "answer": "ghost"},            # unknown id
        {"id": 2, "action": "explode", "answer": "x"},              # bad action
        {"id": 3, "action": "one", "answer": "Maybe"},              # not a real option
        {"id": 4, "action": "one", "answer": "yes"},                # case-insensitive real option
        "garbage",
    ]}
    valid, missing = validate_answers(raw, {1, 2, 3, 4, 5}, {3: ["Yes", "No"], 4: ["Yes", "No"]})
    assert valid[1]["answer"] == "hello" and valid[1]["draft"]
    assert 99 not in valid
    assert valid[3]["action"] == "skip"
    assert valid[4]["answer"] == "Yes"
    assert missing == [2, 5]


def test_answers_malformed():
    assert validate_answers({"nope": 1}, {1}, {}) == ({}, [1])
    assert validate_answers([], {1}, {}) == ({}, [1])


def test_classifications_validate_field_whitelist():
    raw = {"classifications": [
        {"id": 1, "canonical_field": "registered_email", "confidence": "high"},
        {"id": 2, "canonical_field": "bank_password", "confidence": "high"},   # not allowed
    ]}
    valid, missing = validate_classifications(raw, {1, 2}, ["registered_email"])
    assert 1 in valid and 2 not in valid and missing == [2]


def test_verify_value():
    assert verify_value("a  b", "a b") and not verify_value("a", "b")
