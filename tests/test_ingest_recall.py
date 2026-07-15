import json
import os

from gitmemory.ingest import distill_memories, ingest
from gitmemory.llm import FakeLLM, hashing_embedding
from gitmemory.models import MemoryType
from gitmemory.prompts import extract_json
from gitmemory.recall import format_comment, recall
from gitmemory.store import MemoryStore


# ---- extract_json --------------------------------------------------------- #
def test_extract_json_direct():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_fenced():
    text = "Sure!\n```json\n{\"memories\": []}\n```\nDone."
    assert extract_json(text) == {"memories": []}


def test_extract_json_embedded_span():
    text = 'blah blah {"memories": [{"claim": "x"}]} trailing text'
    parsed = extract_json(text)
    assert parsed["memories"][0]["claim"] == "x"


def test_extract_json_none_on_garbage():
    assert extract_json("no json here") is None
    assert extract_json("") is None
    assert extract_json(None) is None


# ---- embeddings ----------------------------------------------------------- #
def test_hashing_embedding_similar_texts_closer():
    from gitmemory.store import cosine_similarity

    a = hashing_embedding("order locking deadlock database")
    b = hashing_embedding("database deadlock on order locking")
    c = hashing_embedding("inventory cache redis ttl flash sale")
    assert cosine_similarity(a, b) > cosine_similarity(a, c)


# ---- ingest --------------------------------------------------------------- #
def _responder_with(payload):
    return lambda messages: json.dumps(payload)


def test_distill_parses_and_defaults():
    payload = {
        "memories": [
            {"type": "gotcha", "claim": "watch the TTL", "reason": "oversell",
             "tags": ["cache"], "confidence": 0.9},
            {"claim": "no type given"},          # should default to decision
            {"reason": "no claim -> skipped"},   # skipped (empty claim)
            {"type": "bogus", "claim": "bad type -> decision"},
        ]
    }
    llm = FakeLLM(chat_responder=_responder_with(payload))
    recs = distill_memories(llm, "PR#1", "content")
    assert len(recs) == 3
    assert recs[0].type == MemoryType.GOTCHA
    assert recs[1].type == MemoryType.DECISION
    assert recs[2].type == MemoryType.DECISION
    assert all(r.source == ["PR#1"] for r in recs)


def test_ingest_embeds_and_stores():
    payload = {"memories": [{"type": "decision", "claim": "use utc", "tags": ["time"]}]}
    llm = FakeLLM(chat_responder=_responder_with(payload))
    store = MemoryStore(path=os.devnull)
    recs = ingest(llm, store, "PR#2", "some text")
    assert len(recs) == 1
    assert recs[0].embedding is not None
    assert len(store) == 1


def test_ingest_empty_when_no_memories():
    llm = FakeLLM(chat_responder=_responder_with({"memories": []}))
    store = MemoryStore(path=os.devnull)
    assert ingest(llm, store, "PR#3", "nothing durable here") == []


# ---- recall --------------------------------------------------------------- #
def test_recall_ranks_relevant_first():
    llm = FakeLLM()
    store = MemoryStore(path=os.devnull)
    payloads = {
        "orders": {"memories": [{"type": "decision",
                    "claim": "orders use optimistic locking to avoid deadlocks",
                    "tags": ["orders", "locking"]}]},
        "cache": {"memories": [{"type": "gotcha",
                    "claim": "inventory cache ttl under 60 seconds",
                    "tags": ["cache", "inventory"]}]},
    }
    for src, pl in payloads.items():
        llm.chat_responder = _responder_with(pl)
        ingest(llm, store, src, "x")

    results = recall(llm, store, "deadlock when locking order rows", k=2, min_score=0.05)
    assert results
    assert "locking" in results[0][0].claim


def test_recall_empty_query():
    llm = FakeLLM()
    store = MemoryStore(path=os.devnull)
    assert recall(llm, store, "   ") == []


def test_format_comment_empty_and_nonempty():
    assert format_comment([]) == ""
    llm = FakeLLM()
    store = MemoryStore(path=os.devnull)
    llm.chat_responder = _responder_with(
        {"memories": [{"type": "decision", "claim": "use utc timestamps", "tags": ["time"]}]}
    )
    ingest(llm, store, "PR#7", "x")
    results = recall(llm, store, "timestamp timezone bug", k=1, min_score=0.0)
    comment = format_comment(results)
    assert "Relevant project memory" in comment
    assert "use utc timestamps" in comment
