from dataclasses import dataclass

from pydantic import BaseModel


@dataclass(frozen=True)
class Prompt:
    kind: str
    version: str
    system: str
    output: type[BaseModel]
    role: str
    max_tokens: int
