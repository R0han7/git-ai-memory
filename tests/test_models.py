from gitmemory.models import MemoryRecord, MemoryStatus, MemoryType


def test_defaults_and_ids_unique():
    a = MemoryRecord(claim="x")
    b = MemoryRecord(claim="y")
    assert a.id != b.id
    assert a.id.startswith("mem_")
    assert a.status == MemoryStatus.ACTIVE
    assert a.type == MemoryType.DECISION
    assert a.is_active()


def test_roundtrip_serialization():
    rec = MemoryRecord(
        claim="Use UTC everywhere",
        reason="tz bugs",
        type=MemoryType.CONVENTION,
        source=["PR#1"],
        tags=["time"],
        embedding=[0.1, 0.2, 0.3],
        confidence=0.7,
    )
    d = rec.to_dict()
    assert d["type"] == "convention"
    assert d["status"] == "active"
    rec2 = MemoryRecord.from_dict(d)
    assert rec2.claim == rec.claim
    assert rec2.type == MemoryType.CONVENTION
    assert rec2.embedding == [0.1, 0.2, 0.3]
    assert rec2.id == rec.id


def test_to_dict_can_drop_embedding():
    rec = MemoryRecord(claim="c", embedding=[1.0, 2.0])
    d = rec.to_dict(include_embedding=False)
    assert "embedding" not in d


def test_from_dict_ignores_unknown_keys():
    rec = MemoryRecord.from_dict(
        {"claim": "c", "type": "gotcha", "status": "active", "future_field": 123}
    )
    assert rec.claim == "c"
    assert rec.type == MemoryType.GOTCHA


def test_lifecycle_transitions():
    rec = MemoryRecord(claim="old decision")
    rec.mark_superseded("mem_new")
    assert rec.status == MemoryStatus.SUPERSEDED
    assert rec.superseded_by == "mem_new"
    assert not rec.is_active()

    rec2 = MemoryRecord(claim="wrong thing")
    rec2.mark_retracted("no longer true")
    assert rec2.status == MemoryStatus.RETRACTED
    assert rec2.retraction_reason == "no longer true"
    assert not rec2.is_active()
