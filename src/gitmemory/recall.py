"""Recall agent: surface relevant *active* memories for new activity.

Given the text of a new PR or issue, embed it, search the store (active-only),
and format a Markdown comment that a GitHub Action can post. This is where the
system visibly prevents repeated mistakes ("we tried this before...").
"""

from __future__ import annotations

from typing import List, Sequence, Tuple

from .llm import LLMClient
from .models import MemoryRecord
from .store import MemoryStore

_TYPE_EMOJI = {
    "decision": "📌",
    "gotcha": "⚠️",
    "convention": "📐",
    "dead_end": "🚫",
}


def recall(
    llm: LLMClient,
    store: MemoryStore,
    query_text: str,
    k: int = 5,
    min_score: float = 0.15,
) -> List[Tuple[MemoryRecord, float]]:
    """Return ranked (record, score) pairs relevant to query_text."""
    if not query_text.strip():
        return []
    query_vec = llm.embed([query_text])[0]
    return store.search(query_vec, k=k, min_score=min_score)


def format_comment(results: Sequence[Tuple[MemoryRecord, float]]) -> str:
    """Render recall results as a Markdown comment. Empty string if no hits."""
    if not results:
        return ""
    lines = [
        "### 🧠 Relevant project memory",
        "",
        "This activity relates to decisions and gotchas recorded earlier. "
        "Please review before proceeding:",
        "",
    ]
    for rec, score in results:
        emoji = _TYPE_EMOJI.get(rec.type.value, "•")
        src = f" _(source: {', '.join(rec.source)})_" if rec.source else ""
        lines.append(f"- {emoji} **{rec.claim}**{src}")
        if rec.reason:
            lines.append(f"  - _why:_ {rec.reason}")
        lines.append(f"  - _relevance:_ {score:.2f} · _id:_ `{rec.id}`")
    lines.append("")
    lines.append(
        "<sub>Posted by gitmemory. If a memory is outdated, it will be "
        "superseded or retracted on merge.</sub>"
    )
    return "\n".join(lines)
