from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class Freshness(str, Enum):
    FRESH = "fresh"
    STALE = "stale"
    MISSING = "missing"
    NEW = "new"


@dataclass(slots=True)
class Evidence:
    path: str
    hash: str | None = None
    current_hash: str | None = None
    status: Freshness = Freshness.FRESH
    symbol: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    excerpt: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


@dataclass(slots=True)
class TaskMemory:
    id: int
    normalized_intent: str
    question: str
    answer: str
    validation: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    score: float = 0.0
    evidence: list[Evidence] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["evidence"] = [e.to_dict() for e in self.evidence]
        return data


@dataclass(slots=True)
class ContextPacket:
    question: str
    status: Freshness
    token_budget: int
    estimated_tokens: int
    summary: str
    confidence: float = 0.0
    matched_task: TaskMemory | None = None
    evidence: list[Evidence] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    context: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "status": self.status.value,
            "token_budget": self.token_budget,
            "estimated_tokens": self.estimated_tokens,
            "summary": self.summary,
            "confidence": self.confidence,
            "matched_task": self.matched_task.to_dict() if self.matched_task else None,
            "evidence": [e.to_dict() for e in self.evidence],
            "warnings": list(self.warnings),
            "context": self.context,
        }
