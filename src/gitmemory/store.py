"""Git-native memory store.

Memories live *inside the repo* as a single reviewable JSON file (default
`.gitmemory/memories.json`). This makes the memory:
    * version-controlled  -> every change is a diff in git history
    * auditable           -> retraction / supersede is a real commit
    * portable            -> no external database required to get started

Recall uses cosine similarity over embeddings stored alongside each record.
Only ACTIVE memories are returned by search, which is what keeps stale
(superseded / retracted) knowledge from poisoning results.
"""

from __future__ import annotations

import copy
import json
import math
import os
from typing import Dict, List, Optional, Sequence, Tuple

from .models import MemoryRecord, MemoryStatus

DEFAULT_STORE_PATH = ".gitmemory/memories.json"

# Lifecycle precedence for conflict-free merges: once a memory has advanced to a
# more "final" state on any branch, a merge must not revert it. retracted beats
# superseded beats active.
_STATUS_RANK = {
    MemoryStatus.ACTIVE: 0,
    MemoryStatus.SUPERSEDED: 1,
    MemoryStatus.RETRACTED: 2,
}


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity of two equal-length vectors. Pure python (no numpy)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


class MemoryStore:
    """In-memory collection of MemoryRecords backed by an on-disk JSON file."""

    def __init__(self, path: str = DEFAULT_STORE_PATH) -> None:
        self.path = path
        self._records: Dict[str, MemoryRecord] = {}

    # ---- persistence -------------------------------------------------------

    @classmethod
    def load(cls, path: str = DEFAULT_STORE_PATH) -> "MemoryStore":
        store = cls(path)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            for item in data.get("memories", []):
                rec = MemoryRecord.from_dict(item)
                store._records[rec.id] = rec
        return store

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        # Deterministic ordering (by creation time then id) keeps git diffs clean.
        ordered = sorted(
            self._records.values(), key=lambda r: (r.created_at, r.id)
        )
        payload = {
            "version": 1,
            "memories": [r.to_dict(include_embedding=True) for r in ordered],
        }
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
            fh.write("\n")

    # ---- basic access ------------------------------------------------------

    def __len__(self) -> int:
        return len(self._records)

    def get(self, mem_id: str) -> Optional[MemoryRecord]:
        return self._records.get(mem_id)

    def all(self) -> List[MemoryRecord]:
        return list(self._records.values())

    def active(self) -> List[MemoryRecord]:
        return [r for r in self._records.values() if r.is_active()]

    def add(self, record: MemoryRecord) -> MemoryRecord:
        self._records[record.id] = record
        return record

    # ---- lifecycle transitions --------------------------------------------

    def supersede(self, old_id: str, new_record: MemoryRecord) -> MemoryRecord:
        """Add `new_record` and mark `old_id` as superseded by it.

        Raises KeyError if old_id is unknown.
        """
        if old_id not in self._records:
            raise KeyError(f"unknown memory id: {old_id}")
        self.add(new_record)
        new_record.supersedes = old_id
        self._records[old_id].mark_superseded(new_record.id)
        return new_record

    def retract(self, mem_id: str, reason: str) -> MemoryRecord:
        """Mark a memory as retracted. Raises KeyError if unknown."""
        if mem_id not in self._records:
            raise KeyError(f"unknown memory id: {mem_id}")
        rec = self._records[mem_id]
        rec.mark_retracted(reason)
        return rec

    # ---- recall ------------------------------------------------------------

    def search(
        self,
        query_embedding: Sequence[float],
        k: int = 5,
        min_score: float = 0.0,
        include_inactive: bool = False,
    ) -> List[Tuple[MemoryRecord, float]]:
        """Return up to k (record, score) pairs ranked by cosine similarity.

        By default only ACTIVE memories are considered — this is the mechanism
        that prevents stale knowledge from resurfacing.
        """
        candidates = self.all() if include_inactive else self.active()
        scored: List[Tuple[MemoryRecord, float]] = []
        for rec in candidates:
            if not rec.embedding:
                continue
            score = cosine_similarity(query_embedding, rec.embedding)
            if score >= min_score:
                scored.append((rec, score))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:k]

    # ---- stats -------------------------------------------------------------

    def stats(self) -> Dict[str, int]:
        counts = {s.value: 0 for s in MemoryStatus}
        for rec in self._records.values():
            counts[rec.status.value] += 1
        counts["total"] = len(self._records)
        return counts


# --------------------------------------------------------------------------- #
# Conflict-free union merge                                                    #
# --------------------------------------------------------------------------- #
def _rank_key(rec: MemoryRecord) -> Tuple[int, str, str]:
    """Total order used to pick a winner when the same id appears twice.

    Ordered by (lifecycle rank, updated_at, id). Because this is a *total order*
    and merging takes the maximum, the merge is commutative and associative:
    merging branches in any order yields the identical result — exactly what a
    git merge driver needs.
    """
    return (_STATUS_RANK[rec.status], rec.updated_at, rec.id)


def merge_records(a: MemoryRecord, b: MemoryRecord) -> MemoryRecord:
    """Merge two records that share an id into one deterministic winner.

    The winner is the max under `_rank_key`; provenance (`source`) from both is
    unioned and sorted so the result is independent of argument order.
    """
    winner, loser = (a, b) if _rank_key(a) >= _rank_key(b) else (b, a)
    out = copy.deepcopy(winner)
    out.source = sorted(set(list(winner.source or []) + list(loser.source or [])))
    return out


def merge_stores(
    stores: Sequence["MemoryStore"], path: str = DEFAULT_STORE_PATH
) -> "MemoryStore":
    """Union any number of stores by id into a new store.

    Records unique to one store are kept as-is; records present in several are
    resolved via `merge_records`. Never deletes records (memories are retracted,
    not removed), so a 3-way merge can safely ignore the common ancestor.
    """
    out = MemoryStore(path)
    for st in stores:
        for rec in st.all():
            existing = out.get(rec.id)
            if existing is None:
                out._records[rec.id] = copy.deepcopy(rec)
            else:
                out._records[rec.id] = merge_records(existing, rec)
    return out
