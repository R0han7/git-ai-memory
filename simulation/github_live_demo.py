"""LIVE GitHub demo — posts real recall comments on a real GitHub issue.

This is the production loop without a self-hosted runner: memory is built locally
(gemma via LM Studio), and the recall comment is posted to GitHub through the REST
API using a fine-grained token read from ~/.gm_token.

What it does on the target repo:
  1. Create a demo issue ("should we add row locks to orders?").
  2. Post a RECALL comment that surfaces the relevant past decision.
  3. Ingest a reversal decision -> SUPERSEDE the old memory.
  4. Post a second comment showing recall now reflects the new decision.

Usage:
    python simulation/github_live_demo.py --repo R0han7/git-ai-memory
    python simulation/github_live_demo.py --repo R0han7/git-ai-memory --fake
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from gitmemory.ingest import ingest  # noqa: E402
from gitmemory.llm import FakeLLM, LLMError, LMStudioClient  # noqa: E402
from gitmemory.recall import format_comment, recall  # noqa: E402
from gitmemory.retract import reconcile  # noqa: E402
from gitmemory.store import MemoryStore  # noqa: E402

TOKEN_FILE = os.path.expanduser("~/.gm_token")
API = "https://api.github.com"


def gh(method, path, token, payload=None):
    url = f"{API}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "gitmemory-demo",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        raise SystemExit(f"GitHub API {method} {path} failed ({e.code}): {body}")


def build_llm(force_fake):
    if not force_fake:
        c = LMStudioClient(
            chat_model=os.environ.get("LMSTUDIO_CHAT_MODEL", "google/gemma-4-e4b"),
            embedding_model=os.environ.get(
                "LMSTUDIO_EMBED_MODEL", "text-embedding-nomic-embed-text-v1.5"),
        )
        try:
            c.embed(["ping"])
            return c, f"{c.chat_model} via LM Studio"
        except Exception:
            pass
    return FakeLLM(chat_responder=_fake_responder), "FakeLLM (offline)"


def _fake_responder(messages):
    content = messages[-1]["content"]
    scripts = {
        "Source: PR#12": {"memories": [{"type": "decision",
            "claim": "Order updates use optimistic locking (version column), not row locks.",
            "reason": "Row locks caused deadlocks and lock-wait timeouts under checkout load.",
            "tags": ["orders", "locking", "concurrency"], "confidence": 0.9}]},
        "Source: issue#33": {"memories": [{"type": "gotcha",
            "claim": "Inventory cache TTL must stay under 60 seconds.",
            "reason": "A long TTL caused oversells during flash sales.",
            "tags": ["inventory", "cache", "ttl"], "confidence": 0.85}]},
        "Source: PR#41": {"memories": [{"type": "decision",
            "claim": "Order updates now use SELECT FOR UPDATE row-level locking with a short timeout.",
            "reason": "Retry storms from optimistic locking hurt tail latency.",
            "tags": ["orders", "locking", "concurrency"], "confidence": 0.88}]},
    }
    for k, v in scripts.items():
        if k in content:
            return json.dumps(v)
    if "NEW memory:" in content and "row-level locking" in content:
        m = re.search(r"(mem_[0-9a-f]+) — Order updates use optimistic locking", content)
        if m:
            return json.dumps({"conflicts": [{"existing_id": m.group(1),
                "action": "supersede",
                "explanation": "Row-level locking replaces the earlier optimistic-locking decision."}]})
    return "{}"


FOOTER = "\n\n---\n<sub>🤖 Demo of [gitmemory](https://github.com/R0han7/git-ai-memory). Safe to delete this issue.</sub>"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="owner/name")
    ap.add_argument("--fake", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(TOKEN_FILE):
        raise SystemExit(f"Token file not found: {TOKEN_FILE}")
    token = open(TOKEN_FILE).read().strip()

    llm, backend = build_llm(args.fake)
    print(f"backend: {backend}")
    print(f"repo:    {args.repo}")

    # 1) Build memory from history (local)
    store = MemoryStore(path=os.devnull)
    hist = [
        ("PR#12", "PR-12-optimistic-locking.md"),
        ("issue#33", "issue-33-inventory-cache.md"),
    ]
    for src, fname in hist:
        content = open(os.path.join(ROOT, "sample", "history", fname)).read()
        ingest(llm, store, source=src, content=content)
    print(f"seeded memory: {store.stats()['active']} active records")

    # 2) Create a real issue
    title = "Add SELECT FOR UPDATE row locks to fix order deadlocks?"
    body = ("Two workers updating the same order row cause a race condition. "
            "Proposing to add `SELECT ... FOR UPDATE` row-level locking to serialize them.")
    issue = gh("POST", f"/repos/{args.repo}/issues", token,
               {"title": "[gitmemory demo] " + title, "body": body})
    num, url = issue["number"], issue["html_url"]
    print(f"created issue #{num}: {url}")

    # 3) Recall + post comment
    results = recall(llm, store, f"{title}\n\n{body}", k=3, min_score=0.2)
    comment = format_comment(results) or "_(no relevant memory found)_"
    gh("POST", f"/repos/{args.repo}/issues/{num}/comments", token,
       {"body": comment + FOOTER})
    print(f"posted RECALL comment -> surfaced {len(results)} memory(ies)")

    # 4) Ingest a reversal, supersede, and post the updated recall
    reversal = ("We are reversing the earlier decision: orders now use SELECT FOR UPDATE "
                "row-level locking with a short timeout. Optimistic version-column locking "
                "is no longer used for orders because it caused retry storms.")
    new = ingest(llm, store, source="PR#41", content=reversal)
    applied = reconcile(llm, store, new)
    results2 = recall(llm, store, f"{title}\n\n{body}", k=3, min_score=0.2)
    followup = (
        f"**Update:** a later decision (PR#41) reversed this. gitmemory "
        f"**superseded** {len(applied)} memory(ies); recall now shows:\n\n"
        + (format_comment(results2) or "_(none)_")
    )
    gh("POST", f"/repos/{args.repo}/issues/{num}/comments", token,
       {"body": followup + FOOTER})
    print(f"posted SUPERSEDE follow-up -> {len(applied)} transition(s)")

    print(f"\n✔ Live demo complete. View it: {url}")
    print("  (delete the issue when done — it's clearly labeled as a demo.)")


if __name__ == "__main__":
    raise SystemExit(main())
