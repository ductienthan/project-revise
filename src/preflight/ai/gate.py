"""The only module allowed to import the Claude SDK. Sends approved items only."""

from dataclasses import dataclass


@dataclass(frozen=True)
class GateItem:
    id: str
    kind: str
    text: str
    sensitive: bool = False


def offerable(items: list[GateItem]) -> list[GateItem]:
    return [item for item in items if not item.sensitive]


def build_payload(items: list[GateItem], approved_ids: set[str]) -> list[GateItem]:
    return [item for item in offerable(items) if item.id in approved_ids]
