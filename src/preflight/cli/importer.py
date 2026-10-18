import csv
import io
import json
import uuid
from dataclasses import dataclass, field
from datetime import date

from preflight.db.models import FailureCategory, Severity

NAMESPACE = uuid.UUID("2f0c1d3e-9b8a-4e57-9d6c-5a4b3c2d1e0f")
TEXT_FIELDS = ("what_happened", "guessed_cause", "root_cause", "fix", "prevention")
CATEGORIES = {c.value for c in FailureCategory}
SEVERITIES = {s.value for s in Severity}


@dataclass(frozen=True)
class Row:
    project: str
    body: dict


@dataclass
class Parsed:
    rows: list[Row] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def failure_id(project: str, title: str, day: str) -> str:
    """Stable per (project, title, date), so importing the same file twice adds nothing."""
    return str(uuid.uuid5(NAMESPACE, f"{project}\0{title}\0{day}"))


def _parse_row(raw: dict) -> Row:
    project = str(raw.get("project") or "").strip()
    title = str(raw.get("title") or "").strip()
    day = str(raw.get("date") or "").strip()
    if not project or not title or not day:
        raise ValueError("needs project, title, and date")
    try:
        date.fromisoformat(day)
    except ValueError:
        raise ValueError(f"date '{day}' is not YYYY-MM-DD") from None
    body = {"id": failure_id(project, title, day), "title": title, "occurred_at": f"{day}T12:00:00Z"}
    for key in TEXT_FIELDS:
        if raw.get(key):
            body[key] = str(raw[key]).strip()
    if raw.get("category"):
        if raw["category"] not in CATEGORIES:
            raise ValueError(f"unknown category '{raw['category']}'")
        body["category"] = raw["category"]
    if raw.get("severity"):
        if raw["severity"] not in SEVERITIES:
            raise ValueError(f"unknown severity '{raw['severity']}'")
        body["severity"] = raw["severity"]
    if raw.get("minutes_lost") not in (None, ""):
        try:
            body["minutes_lost"] = int(raw["minutes_lost"])
        except (TypeError, ValueError):
            raise ValueError(f"minutes_lost '{raw['minutes_lost']}' is not a number") from None
    guess = raw.get("guess_correct")
    if guess not in (None, ""):
        if str(guess).lower() not in ("true", "false", "yes", "no", "1", "0"):
            raise ValueError(f"guess_correct '{guess}' is not true or false")
        body["guess_correct"] = str(guess).lower() in ("true", "yes", "1")
    return Row(project, body)


def parse(text: str, filename: str) -> Parsed:
    """Rows from a JSON list or a CSV file with a header. Each bad row is reported, the rest still import."""
    if filename.endswith(".json"):
        records = json.loads(text)
        if not isinstance(records, list):
            raise ValueError("A JSON import must be a list of failures.")
        numbered = enumerate(records, start=1)
    else:
        numbered = enumerate(csv.DictReader(io.StringIO(text.lstrip("﻿"))), start=2)
    parsed = Parsed()
    for line, raw in numbered:
        try:
            parsed.rows.append(_parse_row(raw if isinstance(raw, dict) else {}))
        except ValueError as err:
            parsed.errors.append(f"row {line}: {err}")
    return parsed
