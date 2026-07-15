"""Evaluation harness for gitmemory.

Metrics (the numbers you publish in the README and cite in interviews):

    * recall_precision@k / recall_recall@k
        Are the memories we surface actually relevant? Uses labeled golden
        queries. Deterministic with --fake (measures the retrieval pipeline);
        with a real LM Studio embedding model it measures true retrieval quality.

    * staleness_rate
        Fraction of surfaced memories that are superseded/retracted. The store
        searches active-only, so this should be exactly 0.0 — the harness proves
        the guarantee holds rather than assuming it.

    * conflict_detection_accuracy
        On labeled (new vs existing) pairs, does the retract/supersede agent pick
        the right existing memory and action? Requires an LLM; with --fake it is a
        plumbing check, with a real model it measures model quality.

Usage:
    python eval/run_eval.py --fake                 # offline, deterministic
    python eval/run_eval.py                         # against LM Studio
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from gitmemory.ingest import embed_records  # noqa: E402
from gitmemory.llm import FakeLLM, LMStudioClient  # noqa: E402
from gitmemory.models import MemoryRecord, MemoryStatus, MemoryType  # noqa: E402
from gitmemory.recall import recall  # noqa: E402
from gitmemory.retract import detect_conflicts  # noqa: E402
from gitmemory.store import MemoryStore  # noqa: E402

HERE = os.path.dirname(__file__)
GOLDEN = os.path.join(HERE, "golden_cases.json")


def build_store(llm, cases) -> tuple:
    """Seed a store from golden memories. Returns (store, key->id map)."""
    store = MemoryStore(path=os.devnull)
    key_to_id = {}
    records = []
    for m in cases["seed_memories"]:
        rec = MemoryRecord(
            claim=m["claim"],
            reason=m.get("reason", ""),
            type=MemoryType(m["type"]),
            tags=m.get("tags", []),
            status=MemoryStatus(m.get("status", "active")),
        )
        key_to_id[m["key"]] = rec.id
        records.append(rec)
    embed_records(llm, records)
    for rec in records:
        store.add(rec)
    return store, key_to_id


def eval_recall(llm, store, key_to_id, cases, k=3, min_score=0.1):
    precisions, recalls, top1s = [], [], []
    for case in cases["recall_cases"]:
        relevant_ids = {key_to_id[k_] for k_ in case["relevant_keys"]}
        results = recall(llm, store, case["query"], k=k, min_score=min_score)
        retrieved_ids = [rec.id for rec, _ in results]
        hits = sum(1 for rid in retrieved_ids if rid in relevant_ids)
        precision = hits / len(retrieved_ids) if retrieved_ids else 0.0
        rec_score = hits / len(relevant_ids) if relevant_ids else 0.0
        top1 = 1.0 if retrieved_ids and retrieved_ids[0] in relevant_ids else 0.0
        precisions.append(precision)
        recalls.append(rec_score)
        top1s.append(top1)
    n = len(cases["recall_cases"]) or 1
    return sum(precisions) / n, sum(recalls) / n, sum(top1s) / n


def eval_staleness(llm, store, key_to_id, cases, k=5, min_score=0.0):
    surfaced_stale = 0
    total_surfaced = 0
    violations = []
    for case in cases["staleness_cases"]:
        results = recall(llm, store, case["query"], k=k, min_score=min_score)
        for rec, _ in results:
            total_surfaced += 1
            if not rec.is_active():
                surfaced_stale += 1
                violations.append(rec.id)
    rate = (surfaced_stale / total_surfaced) if total_surfaced else 0.0
    return rate, violations


def eval_conflicts(llm, store, key_to_id, cases):
    correct = 0
    total = len(cases["conflict_cases"])
    for case in cases["conflict_cases"]:
        m = case["new_memory"]
        new_rec = MemoryRecord(
            claim=m["claim"], reason=m.get("reason", ""),
            type=MemoryType(m["type"]), tags=m.get("tags", []),
        )
        embed_records(llm, [new_rec])
        store.add(new_rec)
        proposals = detect_conflicts(llm, store, new_rec)
        expected_id = key_to_id[case["expected_existing_key"]]
        ok = any(
            p.existing_id == expected_id and p.action == case["expected_action"]
            for p in proposals
        )
        correct += 1 if ok else 0
        # remove the probe record so it doesn't pollute later cases
        store._records.pop(new_rec.id, None)
    return (correct / total) if total else 0.0


def main() -> int:
    ap = argparse.ArgumentParser(description="gitmemory eval harness")
    ap.add_argument("--fake", action="store_true", help="offline deterministic run")
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--golden", default=GOLDEN)
    args = ap.parse_args()

    with open(args.golden, "r", encoding="utf-8") as fh:
        cases = json.load(fh)

    if args.fake:
        llm = FakeLLM(chat_responder=_fake_conflict_responder(cases))
    else:
        llm = LMStudioClient()

    store, key_to_id = build_store(llm, cases)

    prec, rec, top1 = eval_recall(llm, store, key_to_id, cases, k=args.k)
    staleness, violations = eval_staleness(llm, store, key_to_id, cases)
    conflict_acc = eval_conflicts(llm, store, key_to_id, cases)

    print("=" * 56)
    print("gitmemory eval  (backend: {})".format("FakeLLM" if args.fake else "LM Studio"))
    print("=" * 56)
    print(f"recall_top1_accuracy     : {top1:.3f}")
    print(f"recall_precision@{args.k}     : {prec:.3f}")
    print(f"recall_recall@{args.k}        : {rec:.3f}")
    print(f"staleness_rate           : {staleness:.3f}  (target 0.000)")
    if violations:
        print(f"  !! stale surfaced        : {violations}")
    print(f"conflict_detection_acc   : {conflict_acc:.3f}")
    print("=" * 56)

    # Non-zero exit if the hard guarantee (no stale recall) is violated.
    return 1 if staleness > 0.0 else 0


def _fake_conflict_responder(cases):
    """Scripted responder so the offline run exercises the conflict plumbing."""
    import re

    def responder(messages):
        content = messages[-1]["content"]
        if "NEW memory:" in content:
            # supersede whichever existing 'optimistic locking' memory is present
            m = re.search(r"(mem_[0-9a-f]+) — Order updates use optimistic locking", content)
            if m:
                return json.dumps({
                    "conflicts": [
                        {"existing_id": m.group(1), "action": "supersede",
                         "explanation": "row locks replace optimistic locking"}
                    ]
                })
        return "{}"

    return responder


if __name__ == "__main__":
    raise SystemExit(main())
