"""Retract / supersede agent: keep memory fresh.

When new memories are ingested, they may contradict existing active memories.
This agent asks the LLM to compare a new memory against the most similar
existing active memories and propose lifecycle transitions:

    * supersede -> the new memory replaces the old one (same topic, newer truth)
    * retract   -> the old memory is simply wrong now, with no direct replacement

Proposals are returned as structured objects. `apply_conflicts` mutates the
store. In the GitHub Action these transitions are written to the memory file and
opened as a PR for human approval — memory is never silently rewritten.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Set

from .llm import LLMClient
from .models import MemoryRecord
from .prompts import CONFLICT_SCHEMA, CONFLICT_SYSTEM, CONFLICT_USER, extract_json
from .store import MemoryStore


@dataclass
class ConflictProposal:
    existing_id: str
    new_id: str
    action: str  # "supersede" | "retract"
    explanation: str


def _format_existing(records: Sequence[MemoryRecord]) -> str:
    return "\n".join(
        f"- {r.id} — {r.claim} — {r.reason}" for r in records
    ) or "(none)"


def detect_conflicts(
    llm: LLMClient,
    store: MemoryStore,
    new_record: MemoryRecord,
    candidate_k: int = 6,
    min_score: float = 0.2,
    exclude_ids: Optional[Set[str]] = None,
) -> List[ConflictProposal]:
    """Detect conflicts between a new record and similar existing active memories.

    `exclude_ids` are never treated as conflict candidates. This is used to skip
    sibling memories extracted from the same source in the same batch: memories
    distilled together are meant to coexist, not supersede one another. Conflict
    detection therefore only runs against *pre-existing* accumulated history.
    """
    if not new_record.embedding:
        return []
    skip = {new_record.id}
    if exclude_ids:
        skip |= set(exclude_ids)
    # Only compare against semantically related, pre-existing memories.
    neighbors = [
        rec
        for rec, _score in store.search(
            new_record.embedding, k=candidate_k + len(skip), min_score=min_score
        )
        if rec.id not in skip
    ][:candidate_k]
    if not neighbors:
        return []

    new_memory_str = f"{new_record.id} — {new_record.claim} — {new_record.reason}"
    messages = [
        {"role": "system", "content": CONFLICT_SYSTEM},
        {
            "role": "user",
            "content": CONFLICT_USER.format(
                new_memory=new_memory_str, existing=_format_existing(neighbors)
            ),
        },
    ]
    parsed = extract_json(llm.chat(messages, json_schema=CONFLICT_SCHEMA, schema_name="conflicts"))
    proposals: List[ConflictProposal] = []
    if not isinstance(parsed, dict):
        return proposals
    valid_ids = {r.id for r in neighbors}
    for item in parsed.get("conflicts", []) or []:
        if not isinstance(item, dict):
            continue
        existing_id = item.get("existing_id")
        action = (item.get("action") or "").strip().lower()
        if existing_id not in valid_ids or action not in ("supersede", "retract"):
            continue
        proposals.append(
            ConflictProposal(
                existing_id=existing_id,
                new_id=new_record.id,
                action=action,
                explanation=(item.get("explanation") or "").strip(),
            )
        )
    return proposals


def apply_conflicts(
    store: MemoryStore,
    new_record: MemoryRecord,
    proposals: Sequence[ConflictProposal],
) -> List[ConflictProposal]:
    """Apply proposed transitions to the store. Returns the applied proposals."""
    applied: List[ConflictProposal] = []
    for p in proposals:
        existing = store.get(p.existing_id)
        if existing is None or not existing.is_active():
            continue
        if p.action == "supersede":
            existing.mark_superseded(new_record.id)
            new_record.supersedes = existing.id
        elif p.action == "retract":
            reason = p.explanation or f"Retracted due to {new_record.id}"
            existing.mark_retracted(reason)
        applied.append(p)
    return applied


def reconcile(
    llm: LLMClient,
    store: MemoryStore,
    new_records: Sequence[MemoryRecord],
) -> List[ConflictProposal]:
    """Detect + apply conflicts for a batch of freshly-ingested records.

    Memories from the same batch are excluded from each other's conflict
    candidates, so a decision and the convention that supports it are never
    treated as contradictions.
    """
    all_applied: List[ConflictProposal] = []
    batch_ids = {r.id for r in new_records}
    for rec in new_records:
        proposals = detect_conflicts(llm, store, rec, exclude_ids=batch_ids)
        all_applied.extend(apply_conflicts(store, rec, proposals))
    return all_applied
