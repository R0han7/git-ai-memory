"""Ingest agent: distill durable memories from PR / issue text.

Flow:  raw text --(LLM)--> structured memory candidates --(embed)--> records
added to the store. Embeddings are computed from claim + reason + tags so
recall matches on the substance of the memory.
"""

from __future__ import annotations

from typing import List, Optional, Sequence

from .llm import LLMClient
from .models import MemoryRecord, MemoryType
from .prompts import INGEST_SCHEMA, INGEST_SYSTEM, INGEST_USER, extract_json
from .store import MemoryStore


def _embedding_text(claim: str, reason: str, tags: Sequence[str]) -> str:
    return " ".join([claim, reason, " ".join(tags)]).strip()


def distill_memories(
    llm: LLMClient, source: str, content: str
) -> List[MemoryRecord]:
    """Run the LLM to extract memory candidates (not yet embedded or stored)."""
    messages = [
        {"role": "system", "content": INGEST_SYSTEM},
        {"role": "user", "content": INGEST_USER.format(source=source, content=content)},
    ]
    raw = llm.chat(messages, json_schema=INGEST_SCHEMA, schema_name="memories")
    parsed = extract_json(raw)
    records: List[MemoryRecord] = []
    if not isinstance(parsed, dict):
        return records
    for item in parsed.get("memories", []) or []:
        if not isinstance(item, dict):
            continue
        claim = (item.get("claim") or "").strip()
        if not claim:
            continue
        try:
            mem_type = MemoryType(item.get("type", "decision"))
        except ValueError:
            mem_type = MemoryType.DECISION
        tags = item.get("tags") or []
        if not isinstance(tags, list):
            tags = []
        confidence = item.get("confidence", 0.8)
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = 0.8
        records.append(
            MemoryRecord(
                claim=claim,
                reason=(item.get("reason") or "").strip(),
                type=mem_type,
                source=[source] if source else [],
                tags=[str(t) for t in tags],
                confidence=max(0.0, min(1.0, confidence)),
            )
        )
    return records


def embed_records(llm: LLMClient, records: Sequence[MemoryRecord]) -> None:
    """Attach embeddings to records in-place."""
    if not records:
        return
    texts = [_embedding_text(r.claim, r.reason, r.tags) for r in records]
    vectors = llm.embed(texts)
    for rec, vec in zip(records, vectors):
        rec.embedding = list(vec)


def ingest(
    llm: LLMClient,
    store: MemoryStore,
    source: str,
    content: str,
) -> List[MemoryRecord]:
    """Distill, embed, and add memories to the store. Returns the new records."""
    records = distill_memories(llm, source, content)
    embed_records(llm, records)
    for rec in records:
        store.add(rec)
    return records
