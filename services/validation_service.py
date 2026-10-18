"""Validation helpers for JSON, URLs and (untrusted) AI responses."""
from __future__ import annotations

import json
import re
from typing import Any, Iterable
from urllib.parse import urlparse

ALLOWED_ACTIONS = {"text", "one", "many", "skip"}
SPECIAL_CLASSES = {"open_ended", "unknown_factual", "none"}
_CONF_WORDS = {"high": 0.9, "medium": 0.7, "low": 0.4}


def normalize_ws(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value if value is not None else "")).strip()


def parse_confidence(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return max(0.0, min(1.0, float(value)))
    if isinstance(value, str):
        v = value.strip().lower()
        if v in _CONF_WORDS:
            return _CONF_WORDS[v]
        try:
            return max(0.0, min(1.0, float(v)))
        except ValueError:
            return default
    return default


def parse_json_loose(text: Any) -> Any:
    """Parse JSON, tolerating code fences or surrounding prose."""
    if isinstance(text, (dict, list)):
        return text
    t = str(text).strip()
    t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.M).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    start, end = t.find("{"), t.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(t[start : end + 1])
        except json.JSONDecodeError:
            pass
    raise ValueError("Response is not valid JSON")


def validate_json_text(text: str) -> tuple[bool, Any]:
    """Return (True, dict) or (False, error message)."""
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        return False, f"Invalid JSON: {exc.msg} (line {exc.lineno}, column {exc.colno})"
    if not isinstance(obj, dict):
        return False, "The JSON must be an object ({...})."
    return True, obj


def validate_profile_dict(obj: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(obj, dict):
        return ["Profile must be a JSON object."]
    for key, value in obj.items():
        if not isinstance(key, str) or not key.strip():
            errors.append(f"Invalid field name: {key!r}")
            continue
        try:
            json.dumps(value)
        except (TypeError, ValueError):
            errors.append(f"Value of '{key}' is not JSON-serializable.")
    return errors


def validate_form_url(url: str) -> tuple[bool, str]:
    try:
        p = urlparse((url or "").strip())
    except Exception:
        return False, "Could not parse the URL."
    if p.scheme != "https":
        return False, "The URL must start with https://"
    host = (p.hostname or "").lower()
    if host == "forms.gle" and p.path.strip("/"):
        return True, ""
    if host == "docs.google.com" and p.path.startswith("/forms/"):
        return True, ""
    return False, "Not a Google Forms URL (expected docs.google.com/forms/... or forms.gle/...)."


def _to_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def validate_answers(
    raw: Any, expected_ids: Iterable[int], option_map: dict[int, list[str]]
) -> tuple[dict[int, dict], list[int]]:
    """Validate an AI 'answers' response.

    Returns (valid_by_id, missing_ids). Unknown IDs, bad actions, and choice
    answers that are not real options are never trusted.
    """
    expected = set(expected_ids)
    items = raw.get("answers") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        return {}, sorted(expected)
    valid: dict[int, dict] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        qid = _to_int(item.get("id"))
        if qid is None or qid not in expected or qid in valid:
            continue
        action = str(item.get("action", "")).strip().lower()
        if action not in ALLOWED_ACTIONS:
            continue
        entry = {
            "id": qid,
            "action": action,
            "answer": "",
            "draft": bool(item.get("draft", False)),
            "confidence": parse_confidence(item.get("confidence")),
            "reason": normalize_ws(item.get("reason", ""))[:300],
        }
        ans = item.get("answer", "")
        if action == "text":
            if isinstance(ans, list):
                ans = ", ".join(map(str, ans))
            ans = str(ans or "").strip()
            entry["answer"] = ans
            if not ans:
                entry["action"] = "skip"
        elif action in ("one", "many"):
            by_cf = {normalize_ws(o).casefold(): o for o in option_map.get(qid, [])}
            labels = ans if isinstance(ans, list) else [ans]
            resolved = [by_cf.get(normalize_ws(l).casefold()) for l in labels if normalize_ws(l)]
            if not resolved or any(r is None for r in resolved) or (
                action == "one" and len(resolved) != 1
            ):
                entry.update(action="skip", answer="", reason="AI answer did not match a real option")
            else:
                entry["answer"] = resolved[0] if action == "one" else resolved
        valid[qid] = entry
    return valid, sorted(expected - set(valid))


def validate_classifications(
    raw: Any, expected_ids: Iterable[int], allowed_fields: Iterable[str]
) -> tuple[dict[int, dict], list[int]]:
    expected = set(expected_ids)
    allowed = set(allowed_fields) | SPECIAL_CLASSES
    items = raw.get("classifications") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        return {}, sorted(expected)
    valid: dict[int, dict] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        qid = _to_int(item.get("id"))
        fld = str(item.get("canonical_field", "")).strip().lower()
        if qid is None or qid not in expected or qid in valid or fld not in allowed:
            continue
        valid[qid] = {
            "id": qid,
            "canonical_field": fld,
            "confidence": parse_confidence(item.get("confidence")),
            "reason": normalize_ws(item.get("reason", ""))[:300],
        }
    return valid, sorted(expected - set(valid))


def verify_value(expected: Any, actual: Any) -> bool:
    """Compare what we meant to enter with what the page reports."""
    return normalize_ws(expected) == normalize_ws(actual)
