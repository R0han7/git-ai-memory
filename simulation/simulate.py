"""End-to-end usability simulation on a REAL local git repository.

This is not a mock: it creates an actual git repo, makes real commits, writes the
memory file into git history, and (in the branch scene) performs a real git merge
using gitmemory's union merge driver. It demonstrates the full product loop the
GitHub Action would run in production — without needing GitHub.

Scenes:
    0. Bootstrap a git repo + install the union merge driver.
    1. Three PRs merge over time  -> memories are distilled and committed to git.
    2. A NEW PR opens              -> recall warns about a past decision.
    3. That PR reverses the decision -> reconcile SUPERSEDES the stale memory
                                        (committed as a normal diff).
    4. The NEW PR opens again      -> recall now shows the new decision only.
    5. Two branches each record a memory -> real git merge via the union driver
                                            resolves with NO conflict.
    6. Show `git log` of the memory file — memory is auditable git history.

Run:
    python simulation/simulate.py            # uses LM Studio if reachable
    python simulation/simulate.py --fake     # force offline deterministic mode
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from gitmemory.ingest import ingest  # noqa: E402
from gitmemory.llm import FakeLLM, LLMError, LMStudioClient  # noqa: E402
from gitmemory.recall import format_comment, recall  # noqa: E402
from gitmemory.retract import reconcile  # noqa: E402
from gitmemory.store import DEFAULT_STORE_PATH, MemoryStore  # noqa: E402

# --- pretty printing ------------------------------------------------------- #
A = {
    "reset": "\033[0m", "b": "\033[1m", "dim": "\033[2m", "cyan": "\033[36m",
    "green": "\033[32m", "yellow": "\033[33m", "mag": "\033[35m", "red": "\033[31m",
}


def col(s, *st):
    return "".join(A[x] for x in st) + s + A["reset"]


def scene(n, title):
    print("\n" + col(f"╔═ SCENE {n} ", "b", "cyan") +
          col("═" * max(2, 56 - len(title)), "dim"))
    print(col(f"║  {title}", "b"))
    print(col("╚" + "═" * 60, "dim"))


def say(msg):
    print(col("  » ", "dim") + msg)


# --- git helpers ----------------------------------------------------------- #
class Repo:
    def __init__(self, path):
        self.path = path
        self.store_path = os.path.join(path, DEFAULT_STORE_PATH)

    def git(self, *args, check=True, quiet=True):
        env = dict(os.environ)
        env["PYTHONPATH"] = os.path.join(ROOT, "src")
        r = subprocess.run(["git", *args], cwd=self.path, check=check,
                           capture_output=True, text=True, env=env)
        return r

    def commit_all(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def default_branch(self):
        return self.git("symbolic-ref", "--short", "HEAD").stdout.strip()

    def load_store(self):
        return MemoryStore.load(self.store_path)


# --- LLM wiring ------------------------------------------------------------ #
def build_llm(force_fake):
    if not force_fake:
        client = LMStudioClient(
            chat_model=os.environ.get("LMSTUDIO_CHAT_MODEL", "google/gemma-4-e4b"),
            embedding_model=os.environ.get("LMSTUDIO_EMBED_MODEL",
                                           "text-embedding-nomic-embed-text-v1.5"),
        )
        try:
            client.embed(["ping"])  # reachability probe
            return client, f"{client.chat_model} via LM Studio (live)"
        except (LLMError, Exception):
            pass
    return FakeLLM(chat_responder=_fake_responder), "FakeLLM (offline, deterministic)"


def _fake_responder(messages):
    content = messages[-1]["content"]
    scripts = {
        "Source: PR#101": {"memories": [{"type": "decision",
            "claim": "Order updates use optimistic locking (version column), not row locks.",
            "reason": "Row locks caused deadlocks and lock-wait timeouts under checkout load.",
            "tags": ["orders", "locking", "concurrency"], "confidence": 0.9}]},
        "Source: PR#102": {"memories": [{"type": "gotcha",
            "claim": "Inventory cache TTL must stay under 60 seconds.",
            "reason": "A long TTL caused stale counts and oversells during flash sales.",
            "tags": ["inventory", "cache", "ttl"], "confidence": 0.85}]},
        "Source: PR#103": {"memories": [{"type": "convention",
            "claim": "All timestamps are stored in UTC and converted only at display time.",
            "reason": "Mixed local timezones caused off-by-hours reporting bugs.",
            "tags": ["timestamps", "utc", "timezone"], "confidence": 0.8}]},
        "Source: PR#104": {"memories": [{"type": "decision",
            "claim": "Order updates now use SELECT FOR UPDATE row-level locking with a short timeout.",
            "reason": "Retry storms from optimistic locking hurt tail latency.",
            "tags": ["orders", "locking", "concurrency"], "confidence": 0.88}]},
        "Source: PR#900": {"memories": [{"type": "convention",
            "claim": "API error responses use RFC 7807 problem+json.",
            "reason": "Standardizes error handling across services.",
            "tags": ["api", "errors"], "confidence": 0.8}]},
        "Source: PR#901": {"memories": [{"type": "gotcha",
            "claim": "Webhook retries must be idempotent; dedupe on event id.",
            "reason": "Providers redeliver webhooks; non-idempotent handlers double-charged.",
            "tags": ["webhooks", "idempotency"], "confidence": 0.85}]},
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


# --- simulation core ------------------------------------------------------- #
def merge_pr(repo, llm, num, title, body, touch_file):
    """Simulate a PR merging: change code, distill memory, commit both to git."""
    src = f"PR#{num}"
    say(col(f"merge {src}: ", "b") + title)
    # a real code change so the commit is meaningful
    with open(os.path.join(repo.path, touch_file), "a", encoding="utf-8") as fh:
        fh.write(f"\n# change from {src}: {title}\n")
    # distill + reconcile against existing memory
    store = repo.load_store()
    before = store.stats()
    new = ingest(llm, store, source=src, content=f"{title}\n\n{body}")
    applied = reconcile(llm, store, new)
    store.save()
    for r in new:
        print("      " + col("+ ", "green") + col(f"[{r.type.value}] ", "yellow") + r.claim)
    for p in applied:
        print("      " + col("~ SUPERSEDE ", "mag", "b") +
              col(f"{p.existing_id[:10]} → {p.new_id[:10]}", "dim"))
        print("        " + col(p.explanation, "dim"))
    repo.commit_all(f"{src}: {title}\n\nUpdates project memory.")
    after = store.stats()
    say(col(f"memory: {before['active']}→{after['active']} active, "
            f"{after['superseded']} superseded (committed to git)", "dim"))


def open_pr(repo, llm, num, title, body):
    """Simulate a PR opening: recall relevant memory (what the Action would post)."""
    say(col(f"open PR#{num}: ", "b") + title)
    store = repo.load_store()
    results = recall(llm, store, f"{title}\n\n{body}", k=3, min_score=0.2)
    comment = format_comment(results)
    if comment:
        print()
        for line in comment.splitlines():
            print("      " + line)
        print()
    else:
        say(col("(no relevant memory — nothing to warn about)", "dim"))


def branch_merge_demo(repo, llm):
    """Two branches each record a memory; a real git merge unions them, no conflict."""
    default = repo.default_branch()

    say("branch 'feature-api' records a convention (PR#900)")
    repo.git("checkout", "-q", "-b", "feature-api")
    merge_pr(repo, llm, 900, "Adopt RFC 7807 error responses",
             "Standardize API errors.", "api.py")

    say(f"back on {default}, branch 'feature-webhooks' records a gotcha (PR#901)")
    repo.git("checkout", "-q", default)
    repo.git("checkout", "-q", "-b", "feature-webhooks")
    merge_pr(repo, llm, 901, "Make webhook handling idempotent",
             "Dedupe on event id.", "webhooks.py")

    say(f"merge both branches into {default} …")
    repo.git("checkout", "-q", default)
    r1 = repo.git("merge", "--no-edit", "feature-api", check=False)
    r2 = repo.git("merge", "--no-edit", "feature-webhooks", check=False)
    conflict = ("CONFLICT" in (r1.stdout + r1.stderr + r2.stdout + r2.stderr))
    if r1.returncode == 0 and r2.returncode == 0 and not conflict:
        say(col("✔ both merges resolved with NO conflict (union merge driver)", "green", "b"))
    else:
        say(col("✗ unexpected merge conflict", "red", "b"))
    final = repo.load_store()
    say(col(f"memory after branch merges: {final.stats()['active']} active records", "dim"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fake", action="store_true", help="force offline deterministic mode")
    ap.add_argument("--keep", action="store_true", help="keep the temp repo (print path)")
    args = ap.parse_args()

    llm, backend = build_llm(args.fake)
    tmp = tempfile.mkdtemp(prefix="gitmemory_sim_")
    repo = Repo(tmp)

    print(col("\n  gitmemory usability simulation", "b", "mag") +
          col("  (real local git repo)", "dim"))
    print(col(f"  backend: {backend}", "dim"))
    print(col(f"  repo:    {tmp}", "dim"))

    # Scene 0 — bootstrap
    scene(0, "Bootstrap a git repo + install the union merge driver")
    repo.git("init", "-q")
    repo.git("config", "user.email", "dev@example.com")
    repo.git("config", "user.name", "Dev")
    os.makedirs(os.path.join(tmp, ".gitmemory"), exist_ok=True)
    MemoryStore(repo.store_path).save()
    with open(os.path.join(tmp, "app.py"), "w") as fh:
        fh.write("# demo service\n")
    # install merge driver via the real CLI
    subprocess.run([sys.executable, "-m", "gitmemory.cli", "install-merge-driver"],
                   cwd=tmp, env={**os.environ, "PYTHONPATH": os.path.join(ROOT, "src")},
                   capture_output=True)
    repo.commit_all("chore: bootstrap gitmemory")
    say("git repo initialized, merge driver registered, empty memory committed")

    # Scene 1 — history accumulates
    scene(1, "Three PRs merge over time — memories are distilled & committed")
    merge_pr(repo, llm, 101, "Adopt optimistic locking for order updates",
             "Row locks caused deadlocks under load; use a version column instead.", "orders.py")
    merge_pr(repo, llm, 102, "Cap inventory cache TTL",
             "Long TTL caused oversells during flash sales; keep it under 60s.", "cache.py")
    merge_pr(repo, llm, 103, "Store all timestamps in UTC",
             "Mixed timezones caused off-by-hours bugs; store UTC, convert at display.", "time.py")

    # Scene 2 — recall prevents a repeat mistake
    scene(2, "A new PR opens — recall warns about a past decision")
    open_pr(repo, llm, 104, "Add SELECT FOR UPDATE row locks to orders",
            "Two workers updating the same order row cause a race; add row locks.")

    # Scene 3 — the decision is reversed -> supersede
    scene(3, "That PR reverses the decision — reconcile SUPERSEDES the stale memory")
    merge_pr(repo, llm, 104,
             "Switch orders to row-level locking (reverses PR#101)",
             "We are reversing the earlier decision: order updates now use SELECT FOR "
             "UPDATE row-level locking with a short statement timeout. Optimistic locking "
             "caused retry storms that hurt tail latency, so optimistic version-column "
             "locking is no longer used for orders.", "orders.py")

    # Scene 4 — recall now reflects the new truth
    scene(4, "The new PR opens again — recall shows the new decision, not the stale one")
    open_pr(repo, llm, 105, "Questions about the order locking approach",
            "What is our current strategy for concurrent order updates and locking?")

    # Scene 5 — branch merge with the union driver
    scene(5, "Two branches record memories — real git merge, no conflict")
    branch_merge_demo(repo, llm)

    # Scene 6 — audit trail
    scene(6, "Memory is auditable git history")
    log = repo.git("log", "--oneline", "--", DEFAULT_STORE_PATH).stdout.strip()
    for line in log.splitlines():
        print("      " + col(line, "dim"))
    final = repo.load_store()
    print()
    say(col("final memory: ", "b") +
        col(f"{final.stats()['active']} active, {final.stats()['superseded']} superseded, "
            f"{final.stats()['total']} total", "green"))

    if args.keep:
        print(col(f"\n  repo kept at: {tmp}", "yellow"))
        print(col(f"  inspect: git -C {tmp} log -p -- {DEFAULT_STORE_PATH}", "dim"))
    else:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    print()


if __name__ == "__main__":
    raise SystemExit(main())
