import re
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from preflight.core.errors import DomainError, NotFound
from preflight.db.models import Project, RequirementVersion

RESERVED_NAMES = frozenset(
    {"new", "login", "logout", "auth", "drill", "report", "ui", "export", "import", "search", "rules", "failures", "help", "project"}
)
NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")


def validate_name(name: str) -> str:
    if not NAME_PATTERN.match(name):
        raise DomainError("Project names use lowercase letters, digits, - and _, and start with a letter or digit.")
    if name in RESERVED_NAMES:
        raise DomainError(f"'{name}' is a reserved command name. Pick another project name.")
    return name


def create_project(db: Session, user_id: uuid.UUID, name: str, idea: str, goals: str, requirements: str) -> Project:
    validate_name(name)
    if db.scalar(select(Project.id).where(Project.user_id == user_id, Project.name == name)):
        raise DomainError(f"A project named '{name}' already exists.")
    project = Project(user_id=user_id, name=name, idea=idea, goals=goals)
    db.add(project)
    db.flush()
    if requirements.strip():
        save_requirements(db, project, requirements)
    return project


def find_project(db: Session, user_id: uuid.UUID, ref: str) -> Project:
    try:
        clause = Project.id == uuid.UUID(ref)
    except ValueError:
        clause = Project.name == ref
    project = db.scalar(select(Project).where(Project.user_id == user_id, clause))
    if project is None:
        raise NotFound(f"No project named '{ref}'.")
    return project


def latest_requirements(project: Project) -> RequirementVersion | None:
    return project.requirement_versions[-1] if project.requirement_versions else None


def save_requirements(db: Session, project: Project, text: str) -> RequirementVersion:
    """Each changed save becomes a new version. Saving identical text returns the current version."""
    current = latest_requirements(project)
    if current is not None and current.text == text:
        return current
    version = RequirementVersion(
        user_id=project.user_id, project_id=project.id, text=text, version=(current.version + 1) if current else 1
    )
    project.requirement_versions.append(version)
    db.flush()
    return version


def list_projects(db: Session, user_id: uuid.UUID) -> list[Project]:
    return list(db.scalars(select(Project).where(Project.user_id == user_id).order_by(Project.created_at)))

