import os

from gitmemory.models import MemoryRecord, MemoryStatus, MemoryType
from gitmemory.store import MemoryStore, merge_records, merge_stores


def _rec(id_, status=MemoryStatus.ACTIVE, updated="2026-01-01T00:00:00Z",
         source=None, claim="c"):
    r = MemoryRecord(claim=claim, id=id_, source=source or [])
    r.status = status
    r.updated_at = updated
    return r


def _store(*recs):
    s = MemoryStore(path=os.devnull)
    for r in recs:
        s.add(r)
    return s


def test_disjoint_records_are_all_kept():
    a = _store(_rec("mem_a"), _rec("mem_b"))
    b = _store(_rec("mem_c"))
    merged = merge_stores([a, b])
    assert set(r.id for r in merged.all()) == {"mem_a", "mem_b", "mem_c"}


def test_status_precedence_retracted_beats_active():
    active = _rec("mem_x", status=MemoryStatus.ACTIVE, updated="2026-01-01T00:00:00Z")
    retracted = _rec("mem_x", status=MemoryStatus.RETRACTED, updated="2026-01-01T00:00:00Z")
    # Even though timestamps are equal, retracted wins (never revert a retraction).
    out = merge_records(active, retracted)
    assert out.status == MemoryStatus.RETRACTED
    assert merge_records(retracted, active).status == MemoryStatus.RETRACTED


def test_updated_at_tiebreak_when_same_status():
    older = _rec("mem_y", status=MemoryStatus.ACTIVE, updated="2026-01-01T00:00:00Z", claim="old")
    newer = _rec("mem_y", status=MemoryStatus.ACTIVE, updated="2026-06-01T00:00:00Z", claim="new")
    assert merge_records(older, newer).claim == "new"
    assert merge_records(newer, older).claim == "new"


def test_source_union_is_sorted_and_deduped():
    a = _rec("mem_z", source=["PR#41", "PR#10"])
    b = _rec("mem_z", source=["PR#10", "issue#3"])
    out = merge_records(a, b)
    assert out.source == sorted({"PR#41", "PR#10", "issue#3"})


def test_merge_is_commutative():
    a = _store(_rec("m1", status=MemoryStatus.SUPERSEDED, updated="2026-02-01T00:00:00Z"),
               _rec("m2", source=["PR#1"]))
    b = _store(_rec("m1", status=MemoryStatus.ACTIVE, updated="2026-05-01T00:00:00Z"),
               _rec("m2", source=["PR#2"]),
               _rec("m3"))

    ab = merge_stores([a, b])
    ba = merge_stores([b, a])

    def snapshot(store):
        return sorted(
            (r.id, r.status.value, r.updated_at, tuple(r.source)) for r in store.all()
        )

    assert snapshot(ab) == snapshot(ba)


def test_merge_is_associative():
    a = _store(_rec("m1", status=MemoryStatus.ACTIVE, updated="2026-01-01T00:00:00Z"))
    b = _store(_rec("m1", status=MemoryStatus.SUPERSEDED, updated="2026-02-01T00:00:00Z"))
    c = _store(_rec("m1", status=MemoryStatus.ACTIVE, updated="2026-09-01T00:00:00Z"))

    left = merge_stores([merge_stores([a, b]), c])
    right = merge_stores([a, merge_stores([b, c])])

    def snap(s):
        r = s.get("m1")
        return (r.status.value, r.updated_at)

    # superseded (rank 1) beats both active states regardless of grouping
    assert snap(left) == snap(right)
    assert left.get("m1").status == MemoryStatus.SUPERSEDED


def test_merge_never_deletes():
    a = _store(_rec("m1"), _rec("m2"), _rec("m3"))
    b = _store(_rec("m2"))
    merged = merge_stores([a, b])
    assert len(merged) == 3


def test_merge_does_not_mutate_inputs():
    a = _store(_rec("m1", source=["PR#1"]))
    b = _store(_rec("m1", source=["PR#2"]))
    merge_stores([a, b])
    assert a.get("m1").source == ["PR#1"]
    assert b.get("m1").source == ["PR#2"]
