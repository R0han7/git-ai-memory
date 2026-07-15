"""Data models for durable repository memories.

A MemoryRecord captures the *why* behind a repo: decisions, gotchas,
conventions, and dead-ends distilled from PRs and issues. Each record carries
provenance (where it came from) and a lifecycle status so stale knowledge can
be retired instead of poisoning future recall.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional


class MemoryType(str, Enum):
    """The kind of durable knowledge a record encodes."""

    DECISION = "decision"        # an architectural / product choice that was made
    GOTCHA = "gotcha"            # a non-obvious trap, bug, or constraint
    CONVENTION = "convention"    # a team rule / style / pattern to follow
    DEAD_END = "dead_end"        # something that was tried and rejected


class MemoryStatus(str, Enum):
    """Lifecycle state of a memory. Only ACTIVE memories are surfaced on recall."""

    ACTIVE = "active"            # current, trustworthy knowledge
    SUPERSEDED = "superseded"    # replaced by a newer memory (see superseded_by)
    RETRACTED = "retracted"      # proven wrong / no longer true (kept for audit)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _new_id() -> str:
    return "mem_" + uuid.uuid4().hex[:12]


@dataclass
class MemoryRecord:
    """A single durable memory.

    Attributes:
        claim:        the durable fact, stated concisely.
        reason:       why it is true / the rationale or evidence.
        type:         MemoryType.
        source:       provenance references, e.g. ["PR#231", "issue#198"].
        status:       lifecycle status.
        confidence:   model/curator confidence in the claim, 0..1.
        tags:         free-form topical tags used to aid recall & clustering.
        embedding:    optional dense vector for cosine recall.
        id:           stable identifier.
        created_at:   ISO timestamp of creation.
        updated_at:   ISO timestamp of last lifecycle change.
        supersedes:   id of a memory this one replaces (if any).
        superseded_by:id of the memory that replaced this one (if any).
        retraction_reason: why the memory was retracted (if any).
    """

    claim: str
    reason: str = ""
    type: MemoryType = MemoryType.DECISION
    source: List[str] = field(default_factory=list)
    status: MemoryStatus = MemoryStatus.ACTIVE
    confidence: float = 0.8
    tags: List[str] = field(default_factory=list)
    embedding: Optional[List[float]] = None
    id: str = field(default_factory=_new_id)
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    supersedes: Optional[str] = None
    superseded_by: Optional[str] = None
    retraction_reason: Optional[str] = None

    # ---- lifecycle helpers -------------------------------------------------

    def is_active(self) -> bool:
        return self.status == MemoryStatus.ACTIVE

    def mark_superseded(self, by_id: str) -> None:
        self.status = MemoryStatus.SUPERSEDED
        self.superseded_by = by_id
        self.updated_at = _now_iso()

    def mark_retracted(self, reason: str) -> None:
        self.status = MemoryStatus.RETRACTED
        self.retraction_reason = reason
        self.updated_at = _now_iso()

    # ---- serialization -----------------------------------------------------

    def to_dict(self, include_embedding: bool = True) -> Dict[str, Any]:
        d = asdict(self)
        d["type"] = self.type.value
        d["status"] = self.status.value
        if not include_embedding:
            d.pop("embedding", None)
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "MemoryRecord":
        d = dict(d)  # shallow copy; do not mutate caller's dict
        if "type" in d and d["type"] is not None:
            d["type"] = MemoryType(d["type"])
        if "status" in d and d["status"] is not None:
            d["status"] = MemoryStatus(d["status"])
        # Ignore unknown keys defensively so forward-compatible files still load.
        known = set(cls.__dataclass_fields__.keys())  # type: ignore[attr-defined]
        filtered = {k: v for k, v in d.items() if k in known}
        return cls(**filtered)

    def short(self) -> str:
        """One-line human summary, used in surfacing comments."""
        src = f" ({', '.join(self.source)})" if self.source else ""
        return f"[{self.type.value}] {self.claim}{src}"
