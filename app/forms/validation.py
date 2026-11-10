import re
import math

from app.security import valid_email

QUESTION_TYPES = {"short_text", "long_text", "email", "number", "date", "choice", "checkbox"}


def validate_questions(raw):
    import json

    if len(raw) > 40000:
        raise ValueError("The form is too large.")
    try:
        questions = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError("The questions are not valid form data.") from error
    if not isinstance(questions, list) or not 1 <= len(questions) <= 30:
        raise ValueError("Add between 1 and 30 questions.")
    validated = []
    seen = set()
    for question in questions:
        if not isinstance(question, dict):
            raise ValueError("Each question must be an object.")
        question_id = question.get("id")
        title = question.get("title")
        kind = question.get("type")
        if (
            not isinstance(question_id, str)
            or not re.fullmatch(r"[a-zA-Z0-9_-]{1,40}", question_id)
            or question_id in seen
            or not isinstance(title, str)
            or not title.strip()
            or len(title) > 240
            or kind not in QUESTION_TYPES
        ):
            raise ValueError("A question has an invalid ID, title or type.")
        seen.add(question_id)
        options = question.get("options", [])
        if kind in {"choice", "checkbox"}:
            if not isinstance(options, list) or not 2 <= len(options) <= 20:
                raise ValueError("Choice questions need between 2 and 20 options.")
            if any(not isinstance(item, str) or not item.strip() or len(item) > 120 for item in options):
                raise ValueError("Choice options must contain 1–120 characters.")
            options = [item.strip() for item in options]
        else:
            options = []
        validated.append(
            {
                "id": question_id,
                "title": title.strip(),
                "type": kind,
                "required": question.get("required") is True,
                "options": options,
            }
        )
    return validated


def validate_answers(questions, submitted):
    answers = {}
    email_address = None
    for question in questions:
        raw = submitted.get(question["id"], "")
        if question["type"] == "checkbox":
            value = submitted.getlist(question["id"])
            valid = (
                all(item in question["options"] for item in value)
                and len(value) == len(set(value))
            )
            normalized = value
            is_empty = not value
        else:
            value = raw.strip() if isinstance(raw, str) else ""
            normalized = value
            max_length = (
                180 if question["id"] in {"certificate_name", "college_name"} else 5000
            )
            valid = len(value) <= max_length
            is_empty = not value
            if question["type"] == "email" and value:
                valid = valid and valid_email(value)
                if valid and email_address is None:
                    email_address = value.lower()
            if question["type"] == "number" and value:
                try:
                    valid_number = math.isfinite(float(value))
                except ValueError:
                    valid_number = False
                valid = valid and valid_number
            if question["type"] == "date" and value:
                import datetime

                try:
                    datetime.date.fromisoformat(value)
                except ValueError:
                    valid = False
            if question["type"] == "choice" and value:
                valid = value in question["options"]
        if question["required"] and is_empty:
            raise ValueError(f'"{question["title"]}" is required.')
        if not valid:
            raise ValueError(f'Check your answer for "{question["title"]}".')
        answers[question["id"]] = normalized
    return answers, email_address
