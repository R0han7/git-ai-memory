"""Offline end-to-end demo — no model or network required.

Runs the full loop with the deterministic FakeLLM:

    1. Ingest three historical PRs/issues  -> memories are distilled + embedded.
    2. A new PR arrives ("add row-level locking to orders").
         a. RECALL surfaces the related past decision (optimistic locking).
         b. INGEST + RECONCILE detects the conflict and SUPERSEDES the old
            decision with the new one.
    3. Show that recall now returns the *new* decision and no longer the stale
       one — demonstrating the retraction/supersede lifecycle.

Run:  python sample/demo.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from gitmemory.ingest import ingest  # noqa: E402
from gitmemory.llm import FakeLLM  # noqa: E402
from gitmemory.recall import format_comment, recall  # noqa: E402
from gitmemory.retract import reconcile  # noqa: E402
from gitmemory.store import MemoryStore  # noqa: E402

# --- scripted "model" output keyed by source label ------------------------- #
SCRIPT = {
    "PR#12": {
        "memories": [
            {
                "type": "decision",
                "claim": "Order updates use optimistic locking via a version column, not row locks.",
                "reason": "Row locks (SELECT FOR UPDATE) caused deadlocks and lock-wait timeouts under checkout load.",
                "tags": ["orders", "locking", "concurrency", "database"],
                "confidence": 0.9,
            }
        ]
    },
    "issue#33": {
        "memories": [
            {
                "type": "gotcha",
                "claim": "Inventory cache TTL must stay under 60 seconds.",
                "reason": "A 300s TTL caused stale counts and oversells during flash sales.",
                "tags": ["inventory", "redis", "cache", "ttl"],
                "confidence": 0.85,
            }
        ]
    },
    "PR#25": {
        "memories": [
            {
                "type": "dead_end",
                "claim": "Server-side rendering for the analytics dashboard is a dead end.",
                "reason": "SSR added 400-700ms latency due to uncacheable per-user aggregation queries; reverted.",
                "tags": ["dashboard", "ssr", "rendering", "performance"],
                "confidence": 0.8,
            }
        ]
    },
    # The new, conflicting PR.
    "PR#41": {
        "memories": [
            {
                "type": "decision",
                "claim": "Order updates now use row-level locking (SELECT FOR UPDATE) with a short statement timeout.",
                "reason": "A new retry storm from optimistic locking hurt tail latency; targeted row locks + timeout proved more predictable.",
                "tags": ["orders", "locking", "concurrency", "database"],
                "confidence": 0.88,
            }
        ]
    },
}

CONFLICT_SCRIPT = {
    # When reconciling PR#41's new decision, mark the optimistic-locking one superseded.
    "supersede_orders": True,
}


def make_llm() -> FakeLLM:
    def responder(messages):
        content = messages[-1]["content"]
        # Ingest calls include "Source: <label>"
        for label, payload in SCRIPT.items():
            if f"Source: {label}" in content:
                return json.dumps(payload)
        # Conflict-detection calls include "NEW memory:" and the neighbor list.
        if "NEW memory:" in content and "row-level locking" in content:
            # find the existing optimistic-locking memory id from the prompt
            import re

            m = re.search(r"(mem_[0-9a-f]+) — Order updates use optimistic locking", content)
            if m:
                return json.dumps(
                    {
                        "conflicts": [
                            {
                                "existing_id": m.group(1),
                                "action": "supersede",
                                "explanation": "Row-level locking replaces the earlier optimistic-locking decision for order updates.",
                            }
                        ]
                    }
                )
        return "{}"

    return FakeLLM(chat_responder=responder)


def banner(title: str) -> None:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def main() -> int:
    llm = make_llm()
    tmp = tempfile.mkdtemp(prefix="gitmemory_demo_")
    store_path = os.path.join(tmp, "memories.json")
    store = MemoryStore(store_path)

    banner("1) Ingest historical PRs/issues")
    for label, content in [
        ("PR#12", "optimistic locking for orders"),
        ("issue#33", "inventory cache ttl"),
        ("PR#25", "ssr dashboard dead end"),
    ]:
        recs = ingest(llm, store, source=label, content=content)
        for r in recs:
            print(f"  + {r.id} {r.short()}")
    store.save()

    banner("2a) A new PR opens -> RECALL surfaces related memory")
    new_pr = (
        "Add row-level locking to order updates to fix a race condition where "
        "two workers update the same order concurrently."
    )
    results = recall(llm, store, new_pr, k=3, min_score=0.1)
    print(format_comment(results) or "(no relevant memories)")

    banner("2b) The PR merges -> INGEST + RECONCILE (supersede)")
    new_recs = ingest(llm, store, source="PR#41", content="row-level locking for orders")
    applied = reconcile(llm, store, new_recs)
    for p in applied:
        print(f"  ~ {p.action}: {p.existing_id} superseded by {p.new_id}")
        print(f"    reason: {p.explanation}")
    store.save()

    banner("3) Recall again -> stale decision is gone, new one surfaces")
    results = recall(llm, store, new_pr, k=3, min_score=0.1)
    print(format_comment(results) or "(no relevant memories)")

    banner("Store stats")
    for k, v in store.stats().items():
        print(f"  {k:12} {v}")

    print(f"\nMemory file written to: {store_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
