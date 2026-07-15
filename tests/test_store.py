import os

from gitmemory.models import MemoryRecord, MemoryStatus, MemoryType
from gitmemory.store import MemoryStore, cosine_similarity


def test_cosine_similarity_basic():
    assert cosine_similarity([1, 0], [1, 0]) == 1.0
    assert cosine_similarity([1, 0], [0, 1]) == 0.0
    assert cosine_similarity([], [1]) == 0.0
    assert cosine_similarity([0, 0], [0, 0]) == 0.0


def test_add_get_active():
    store = MemoryStore(path=os.devnull)
    a = store.add(MemoryRecord(claim="a"))
    b = store.add(MemoryRecord(claim="b"))
    b.mark_retracted("nope")
    assert store.get(a.id) is a
    assert len(store) == 2
    active = store.active()
    assert a in active and b not in active


def test_supersede_and_retract():
    store = MemoryStore(path=os.devnull)
    old = store.add(MemoryRecord(claim="old"))
    new = MemoryRecord(claim="new")
    store.supersede(old.id, new)
    assert old.status == MemoryStatus.SUPERSEDED
    assert old.superseded_by == new.id
    assert new.supersedes == old.id
    assert new.is_active()

    r = store.add(MemoryRecord(claim="bad"))
    store.retract(r.id, "wrong")
    assert r.status == MemoryStatus.RETRACTED


def test_supersede_unknown_raises():
    store = MemoryStore(path=os.devnull)
    try:
        store.supersede("mem_missing", MemoryRecord(claim="x"))
        assert False, "expected KeyError"
    except KeyError:
        pass


def test_search_is_active_only_and_ranked():
    store = MemoryStore(path=os.devnull)
    # exact-match vector should rank first
    target = store.add(MemoryRecord(claim="target", embedding=[1.0, 0.0, 0.0]))
    other = store.add(MemoryRecord(claim="other", embedding=[0.0, 1.0, 0.0]))
    stale = store.add(MemoryRecord(claim="stale", embedding=[1.0, 0.0, 0.0]))
    stale.mark_retracted("gone")

    results = store.search([1.0, 0.0, 0.0], k=5)
    ids = [r.id for r, _ in results]
    assert target.id in ids
    assert stale.id not in ids            # active-only guarantee
    assert ids[0] == target.id            # best match ranked first
    assert results[0][1] > results[-1][1] if len(results) > 1 else True


def test_search_can_include_inactive_when_asked():
    store = MemoryStore(path=os.devnull)
    stale = store.add(MemoryRecord(claim="stale", embedding=[1.0, 0.0]))
    stale.mark_superseded("mem_x")
    results = store.search([1.0, 0.0], k=5, include_inactive=True)
    assert stale.id in [r.id for r, _ in results]


def test_persistence_roundtrip(tmp_path):
    path = str(tmp_path / "mem.json")
    store = MemoryStore(path=path)
    store.add(MemoryRecord(claim="keep me", type=MemoryType.GOTCHA,
                           embedding=[0.5, 0.5], source=["PR#9"]))
    store.save()
    assert os.path.exists(path)

    reloaded = MemoryStore.load(path)
    assert len(reloaded) == 1
    rec = reloaded.all()[0]
    assert rec.claim == "keep me"
    assert rec.type == MemoryType.GOTCHA
    assert rec.embedding == [0.5, 0.5]


def test_stats():
    store = MemoryStore(path=os.devnull)
    store.add(MemoryRecord(claim="a"))
    b = store.add(MemoryRecord(claim="b"))
    b.mark_retracted("x")
    s = store.stats()
    assert s["total"] == 2
    assert s["active"] == 1
    assert s["retracted"] == 1
