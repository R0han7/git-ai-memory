import json
import os

from gitmemory.ingest import embed_records
from gitmemory.llm import FakeLLM
from gitmemory.models import MemoryRecord, MemoryStatus, MemoryType
from gitmemory.retract import apply_conflicts, detect_conflicts, reconcile
from gitmemory.store import MemoryStore


def _seed(store, llm):
    old = MemoryRecord(
        claim="Order updates use optimistic locking, not row locks.",
        reason="row locks deadlocked",
        type=MemoryType.DECISION,
        tags=["orders", "locking"],
    )
    embed_records(llm, [old])
    store.add(old)
    return old


def test_detect_conflicts_supersede():
    llm = FakeLLM()
    store = MemoryStore(path=os.devnull)
    old = _seed(store, llm)

    new = MemoryRecord(
        claim="Order updates now use row-level locking with a timeout.",
        reason="retry storms hurt latency",
        type=MemoryType.DECISION,
        tags=["orders", "locking"],
    )
    embed_records(llm, [new])
    store.add(new)

    def responder(messages):
        content = messages[-1]["content"]
        if "NEW memory:" in content and old.id in content:
            return json.dumps({"conflicts": [
                {"existing_id": old.id, "action": "supersede", "explanation": "replaced"}
            ]})
        return "{}"

    llm.chat_responder = responder
    proposals = detect_conflicts(llm, store, new)
    assert len(proposals) == 1
    assert proposals[0].existing_id == old.id
    assert proposals[0].action == "supersede"


def test_detect_conflicts_filters_invalid_ids_and_actions():
    llm = FakeLLM()
    store = MemoryStore(path=os.devnull)
    old = _seed(store, llm)
    new = MemoryRecord(claim="row locking now for orders", tags=["orders", "locking"])
    embed_records(llm, [new])
    store.add(new)

    def responder(messages):
        return json.dumps({"conflicts": [
            {"existing_id": "mem_doesnotexist", "action": "supersede", "explanation": "x"},
            {"existing_id": old.id, "action": "explode", "explanation": "bad action"},
        ]})

    llm.chat_responder = responder
    assert detect_conflicts(llm, store, new) == []


def test_apply_conflicts_supersede_and_retract():
    llm = FakeLLM()
    store = MemoryStore(path=os.devnull)
    old = _seed(store, llm)
    new = MemoryRecord(claim="new orders decision", tags=["orders"])
    store.add(new)

    from gitmemory.retract import ConflictProposal

    applied = apply_conflicts(store, new, [
        ConflictProposal(existing_id=old.id, new_id=new.id, action="supersede", explanation="r")
    ])
    assert len(applied) == 1
    assert old.status == MemoryStatus.SUPERSEDED
    assert old.superseded_by == new.id
    assert new.supersedes == old.id


def test_apply_conflicts_skips_inactive():
    llm = FakeLLM()
    store = MemoryStore(path=os.devnull)
    old = _seed(store, llm)
    old.mark_retracted("already gone")
    new = MemoryRecord(claim="n")
    store.add(new)
    from gitmemory.retract import ConflictProposal

    applied = apply_conflicts(store, new, [
        ConflictProposal(existing_id=old.id, new_id=new.id, action="supersede", explanation="r")
    ])
    assert applied == []  # inactive memories are not transitioned again


def test_reconcile_excludes_batch_siblings():
    """Memories from the same ingest batch must not supersede each other.

    Regression test for a bug found in live testing: a decision and the
    convention supporting it (same PR) were circularly superseding each other.
    """
    llm = FakeLLM()
    store = MemoryStore(path=os.devnull)

    a = MemoryRecord(claim="Orders use optimistic locking.", tags=["orders", "locking"])
    b = MemoryRecord(claim="All order writes go through OrderRepo.save().",
                     tags=["orders", "locking"])
    embed_records(llm, [a, b])
    store.add(a)
    store.add(b)

    # A responder that would (wrongly) mark any presented existing memory as
    # superseded — if batch siblings were compared, this would fire.
    def responder(messages):
        import re
        content = messages[-1]["content"]
        m = re.search(r"(mem_[0-9a-f]+) — ", content)
        if m and "NEW memory:" in content:
            return json.dumps({"conflicts": [
                {"existing_id": m.group(1), "action": "supersede", "explanation": "x"}
            ]})
        return "{}"

    llm.chat_responder = responder
    applied = reconcile(llm, store, [a, b])
    assert applied == []                       # no sibling was superseded
    assert a.status == MemoryStatus.ACTIVE
    assert b.status == MemoryStatus.ACTIVE


def test_detect_conflicts_respects_exclude_ids():
    llm = FakeLLM()
    store = MemoryStore(path=os.devnull)
    old = _seed(store, llm)
    new = MemoryRecord(claim="orders row locking now", tags=["orders", "locking"])
    embed_records(llm, [new])
    store.add(new)

    def responder(messages):
        return json.dumps({"conflicts": [
            {"existing_id": old.id, "action": "supersede", "explanation": "x"}
        ]})

    llm.chat_responder = responder
    # excluding old.id means it is never offered as a candidate -> no proposals
    assert detect_conflicts(llm, store, new, exclude_ids={old.id}) == []
    llm = FakeLLM()
    store = MemoryStore(path=os.devnull)
    old = _seed(store, llm)
    new = MemoryRecord(claim="Order updates use row-level locking now.",
                       tags=["orders", "locking"])
    embed_records(llm, [new])
    store.add(new)

    def responder(messages):
        content = messages[-1]["content"]
        if old.id in content:
            return json.dumps({"conflicts": [
                {"existing_id": old.id, "action": "supersede", "explanation": "r"}
            ]})
        return "{}"

    llm.chat_responder = responder
    applied = reconcile(llm, store, [new])
    assert len(applied) == 1
    assert old.status == MemoryStatus.SUPERSEDED
