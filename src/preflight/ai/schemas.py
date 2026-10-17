from typing import Literal

from pydantic import BaseModel

from preflight.db.models import FailureCategory

Category = Literal[tuple(c.value for c in FailureCategory)]
Level = Literal["high", "medium", "low"]


class Question(BaseModel):
    category: Category
    question: str


class Gap(BaseModel):
    text: str
    why: str
    source_ref: str


class DraftItem(BaseModel):
    text: str
    why: str
    source_ref: str
    priority: Level


class ReviewOutput(BaseModel):
    questions: list[Question]
    gaps: list[Gap]
    checklist_items: list[DraftItem]


class ClassifyOutput(BaseModel):
    category: Category
    root_cause: str
    prevention: str


class RuleOutput(BaseModel):
    checklist_text: str


class GradeOutput(BaseModel):
    score: Literal[0, 1, 2, 3, 4, 5]
    feedback: str


def strict_schema(model: type[BaseModel]) -> dict:
    """Pydantic's JSON schema with refs inlined, every property required, and no extra keys allowed."""
    raw = model.model_json_schema()
    defs = raw.pop("$defs", {})

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(defs[node["$ref"].split("/")[-1]])
            node = {k: walk(v) for k, v in node.items() if k not in ("title", "default")}
            if node.get("type") == "object":
                node["additionalProperties"] = False
                node["required"] = list(node.get("properties", {}))
            return node
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(raw)
