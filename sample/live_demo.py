"""Live end-to-end demo against a local model in LM Studio.

Shows the full gitmemory lifecycle on a real model:

    1. Ingest three historical PRs/issues -> durable memories are distilled.
    2. A new PR opens -> RECALL surfaces the relevant past decision.
    3. The PR reverses that decision -> INGEST + RECONCILE SUPERSEDES the stale one.
    4. RECALL again -> the stale decision is gone; the new one surfaces.

Prerequisites:
    lms server start
    lms load google/gemma-4-e4b            # (any chat model)
    # plus an embedding model, e.g. text-embedding-nomic-embed-text-v1.5

Run:
    python sample/live_demo.py
    python sample/live_demo.py --fake       # offline, no model needed
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from gitmemory.ingest import ingest  # noqa: E402
from gitmemory.llm import FakeLLM, LMStudioClient  # noqa: E402
from gitmemory.recall import format_comment, recall  # noqa: E402
from gitmemory.retract import reconcile  # noqa: E402
from gitmemory.store import MemoryStore  # noqa: E402

# --- tiny ANSI helpers ----------------------------------------------------- #
C = {
    "reset": "\033[0m", "bold": "\033[1m", "dim": "\033[2m",
    "cyan": "\033[36m", "green": "\033[32m", "yellow": "\033[33m",
    "magenta": "\033[35m", "red": "\033[31m", "blue": "\033[34m",
}


def c(s, *styles):
    return "".join(C[x] for x in styles) + s + C["reset"]


def step(n, title):
    print()
    print(c(f"━━ STEP {n} ", "bold", "cyan") + c("─" * (58 - len(title) - len(str(n))), "dim"))
    print(c(f"   {title}", "bold"))
    print()


def run_cmd(label):
    print(c("  $ ", "green") + c(label, "bold", "green"))


HISTORY = [
    ("PR#12", "sample/history/PR-12-optimistic-locking.md"),
    ("issue#33", "sample/history/issue-33-inventory-cache.md"),
    ("PR#25", "sample/history/PR-25-ssr-dead-end.md"),
]

NEW_PR_QUERY = (
    "We keep hitting deadlocks when two workers update the same order row. "
    "Should we switch to SELECT FOR UPDATE row locks?"
)

REVERSAL = (
    "We are reversing the earlier decision: order updates now use SELECT FOR "
    "UPDATE row-level locking with a short statement timeout. Optimistic locking "
    "caused retry storms that hurt tail latency, so we no longer use optimistic "
    "version-column locking for orders."
)


def _build_llm(fake):
    if fake:
        return FakeLLM(chat_responder=_fake_responder)
    return LMStudioClient(
        chat_model=os.environ.get("LMSTUDIO_CHAT_MODEL", "google/gemma-4-e4b"),
        embedding_model=os.environ.get("LMSTUDIO_EMBED_MODEL",
                                       "text-embedding-nomic-embed-text-v1.5"),
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fake", action="store_true", help="offline, no model")
    args = ap.parse_args()

    llm = _build_llm(args.fake)
    backend = "FakeLLM (offline)" if args.fake else \
        os.environ.get("LMSTUDIO_CHAT_MODEL", "google/gemma-4-e4b") + " via LM Studio"

    print(c("\n  gitmemory", "bold", "magenta") +
          c("  — git-native AI memory for GitHub", "dim"))
    print(c(f"  backend: {backend}", "dim"))

    tmp = tempfile.mkdtemp(prefix="gitmemory_demo_")
    store = MemoryStore(os.path.join(tmp, "memories.json"))

    # 1. Ingest history --------------------------------------------------- #
    step(1, "Ingest project history (PRs & issues)")
    for src, path in HISTORY:
        run_cmd(f"gitmemory ingest --source {src} --file {os.path.basename(path)}")
        content = open(os.path.join(os.path.dirname(__file__), "..", path)).read()
        recs = ingest(llm, store, source=src, content=content)
        reconcile(llm, store, recs)
        for r in recs:
            print("    " + c("+ ", "green") + c(f"[{r.type.value}] ", "yellow") + r.claim)
        time.sleep(0.2)
    s = store.stats()
    print()
    print(c(f"  → store: {s['active']} active, {s['superseded']} superseded, "
            f"{s['retracted']} retracted", "dim"))

    # 2. Recall ----------------------------------------------------------- #
    step(2, "A new PR opens — recall surfaces relevant memory")
    run_cmd('echo "add row locks to fix order deadlocks" | gitmemory recall')
    results = recall(llm, store, NEW_PR_QUERY, k=3, min_score=0.2)
    print()
    print(format_comment(results))

    # 3. Reversal + supersede -------------------------------------------- #
    step(3, "The PR reverses that decision — reconcile SUPERSEDES the stale memory")
    run_cmd("gitmemory ingest --source PR#41   # (row locks replace optimistic locking)")
    new_recs = ingest(llm, store, source="PR#41", content=REVERSAL)
    for r in new_recs:
        print("    " + c("+ ", "green") + c(f"[{r.type.value}] ", "yellow") + r.claim)
    applied = reconcile(llm, store, new_recs)
    print()
    for p in applied:
        print("    " + c("~ SUPERSEDE ", "magenta", "bold") +
              c(p.existing_id, "dim") + c(" → ", "dim") + c(p.new_id, "dim"))
        print("      " + c(p.explanation, "dim"))
    if not applied:
        print(c("    (no conflict detected this run)", "dim"))

    # 4. Recall again ----------------------------------------------------- #
    step(4, "Recall again — the stale decision is gone, the new one surfaces")
    run_cmd('echo "order locking approach?" | gitmemory recall')
    results = recall(llm, store, NEW_PR_QUERY, k=3, min_score=0.2)
    print()
    print(format_comment(results))

    s = store.stats()
    print()
    print(c("  ✔ done. ", "green", "bold") +
          c(f"store: {s['active']} active, {s['superseded']} superseded, "
            f"{s['retracted']} retracted — stale memory is never recalled.", "dim"))
    print()


# --- offline scripted responses (mirrors real gemma behavior) -------------- #
def _fake_responder(messages):
    import json
    import re
    content = messages[-1]["content"]
    scripts = {
        "Source: PR#12": {"memories": [{"type": "decision",
            "claim": "Order updates use optimistic locking (version column), not SELECT FOR UPDATE row locks.",
            "reason": "Row locks caused deadlocks and lock-wait timeouts under checkout load.",
            "tags": ["orders", "locking", "concurrency"], "confidence": 0.9}]},
        "Source: issue#33": {"memories": [{"type": "gotcha",
            "claim": "Inventory cache TTL must stay under 60 seconds.",
            "reason": "A 300s TTL caused stale counts and oversells during flash sales.",
            "tags": ["inventory", "cache", "ttl"], "confidence": 0.85}]},
        "Source: PR#25": {"memories": [{"type": "dead_end",
            "claim": "Server-side rendering for the analytics dashboard is a dead end.",
            "reason": "SSR added 400-700ms latency from uncacheable per-user aggregation queries.",
            "tags": ["dashboard", "ssr", "performance"], "confidence": 0.8}]},
        "Source: PR#41": {"memories": [{"type": "decision",
            "claim": "Order updates now use SELECT FOR UPDATE row-level locking with a short timeout.",
            "reason": "Retry storms from optimistic locking hurt tail latency.",
            "tags": ["orders", "locking", "concurrency"], "confidence": 0.88}]},
    }
    for key, payload in scripts.items():
        if key in content:
            return json.dumps(payload)
    if "NEW memory:" in content and "row-level locking" in content:
        m = re.search(r"(mem_[0-9a-f]+) — Order updates use optimistic locking", content)
        if m:
            return json.dumps({"conflicts": [{"existing_id": m.group(1),
                "action": "supersede",
                "explanation": "Row-level locking replaces the earlier optimistic-locking decision."}]})
    return "{}"


if __name__ == "__main__":
    raise SystemExit(main())
