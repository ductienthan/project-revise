import csv
import enum
import io
import json
import uuid
import zipfile
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from preflight.core.checklist import to_markdown
from preflight.core.projects import latest_requirements
from preflight.db import models

TABLES = [
    models.Project, models.RequirementVersion, models.Link, models.Snapshot, models.Review, models.ReviewQuestion,
    models.ChecklistItem, models.Failure, models.Pattern, models.Rule, models.Drill, models.DrillReview,
    models.Prediction, models.Win, models.Retrospective, models.GateOverride, models.AiCall,
]


def _plain(value):
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, enum.Enum):
        return value.value
    return value


def _columns(model) -> list[str]:
    return [c.name for c in model.__table__.columns]


def rows_for(db: Session, user_id: uuid.UUID, model) -> list[dict]:
    columns = _columns(model)
    return [{c: _plain(getattr(r, c)) for c in columns} for r in db.scalars(select(model).where(model.user_id == user_id))]


def rows(db: Session, user_id: uuid.UUID) -> dict[str, list[dict]]:
    """Every table a user owns, as plain values. Auth tables are never exported."""
    return {model.__tablename__: rows_for(db, user_id, model) for model in TABLES}


def as_json(db: Session, user_id: uuid.UUID) -> str:
    return json.dumps(rows(db, user_id), indent=2)


def as_csv_zip(db: Session, user_id: uuid.UUID) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for model in TABLES:
            out = io.StringIO()
            writer = csv.DictWriter(out, fieldnames=_columns(model))
            writer.writeheader()
            writer.writerows(rows_for(db, user_id, model))
            archive.writestr(f"{model.__tablename__}.csv", out.getvalue())
    return buffer.getvalue()


def as_markdown(db: Session, user_id: uuid.UUID) -> str:
    projects = db.scalars(select(models.Project).where(models.Project.user_id == user_id).order_by(models.Project.created_at))
    parts = ["# Preflight export", ""]
    for p in projects:
        parts += [f"## {p.name} ({p.status})", ""]
        if p.idea:
            parts += [f"Idea: {p.idea}", ""]
        current = latest_requirements(p)
        if current:
            parts += [f"### Requirements v{current.version}", "", current.text.strip(), ""]
        if p.checklist_items:
            parts += [to_markdown(p).replace("# Checklist", "### Checklist", 1)]
        if p.failures:
            parts += ["### Failures", ""]
            for f in sorted(p.failures, key=lambda f: f.occurred_at):
                cause = f" Root cause: {f.root_cause}." if f.root_cause else ""
                parts.append(f"- {f.occurred_at.date()} **{f.title}** ({f.category or 'uncategorized'}, {f.severity}, {f.minutes_lost} min).{cause}")
            parts.append("")
        if p.wins:
            parts += ["### Wins", ""] + [f"- {w.text}" for w in p.wins] + [""]
        for r in p.retrospectives:
            parts += ["### Retrospective", "", r.summary, ""]
    return "\n".join(parts)
