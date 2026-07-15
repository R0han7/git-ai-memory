"""Read-only analysis of a real repository with gitmemory.

Fetches merged PRs and closed issues from a repo, distills durable memories from
them on a local model (LM Studio), reconciles conflicts (supersede/retract), and
prints what gitmemory learned. Posts NOTHING and modifies NOTHING on GitHub.

Usage:
    python simulation/analyze_repo.py --repo owner/name [--limit 20] [--fake]
    python simulation/analyze_repo.py --repo owner/name --query "how do we handle auth?"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from gitmemory.ingest import ingest  # noqa: E402
from gitmemory.llm import FakeLLM, LMStudioClient  # noqa: E402
from gitmemory.recall import format_comment, recall  # noqa: E402
from gitmemory.retract import reconcile  # noqa: E402
from gitmemory.store import MemoryStore  # noqa: E402

TOKEN_FILE = os.path.expanduser("~/.gm_token")
API = "https://api.github.com"


def gh(path, token, params=None):
    url = f"{API}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "gitmemory-analyzer",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"GitHub API GET {path} failed ({e.code}): {e.read().decode()[:200]}")


def fetch_history(repo, token, limit):
    """Return list of (source_label, text) for merged PRs and closed issues."""
    items = []
    # Merged PRs (most recent first)
    prs = gh(f"/repos/{repo}/pulls", token, {"state": "closed", "per_page": limit, "sort": "updated", "direction": "desc"})
    for pr in prs:
        if not pr.get("merged_at"):
            continue
        body = pr.get("body") or ""
        items.append((f"PR#{pr['number']}", f"{pr['title']}\n\n{body}"))
    # Closed issues (the /issues endpoint also returns PRs, so skip those)
    issues = gh(f"/repos/{repo}/issues", token, {"state": "closed", "per_page": limit, "sort": "updated", "direction": "desc"})
    for it in issues:
        if "pull_request" in it:
            continue
        body = it.get("body") or ""
        items.append((f"issue#{it['number']}", f"{it['title']}\n\n{body}"))
    return items


# Commit-message noise we skip before ingesting (bots, boilerplate, empty).
_NOISE = ("update readme", "initial commit", "assistant checkpoint",
          "checkpoint before assistant", "your commit message here",
          "merge branch", "merge pull request")


def _is_noise(msg: str) -> bool:
    m = msg.strip().lower()
    if len(m) < 12 or m in ('**', '""', "''"):
        return True
    return any(m.startswith(n) or m == n for n in _NOISE)


def fetch_commit_history(repo, token, limit):
    """Return a single (source, document) built from meaningful commit messages.

    Solo/commit-driven repos have no PRs, but their commit log often records real
    decisions. We filter obvious noise and hand the rest to the model as one
    document to distill durable memories from.
    """
    commits = gh(f"/repos/{repo}/commits", token, {"per_page": min(limit, 100)})
    lines = []
    for c in commits if isinstance(commits, list) else []:
        msg = c["commit"]["message"].strip()
        first = msg.splitlines()[0]
        if _is_noise(first):
            continue
        sha = c["sha"][:7]
        lines.append(f"- {sha}: {msg.strip()}")
    if not lines:
        return []
    doc = "Commit history (most recent first):\n" + "\n".join(lines)
    return [(f"{repo}@git-log", doc)]


def build_llm(force_fake):
    if not force_fake:
        c = LMStudioClient(
            chat_model=os.environ.get("LMSTUDIO_CHAT_MODEL", "google/gemma-4-e4b"),
            embedding_model=os.environ.get("LMSTUDIO_EMBED_MODEL",
                                           "text-embedding-nomic-embed-text-v1.5"),
        )
        try:
            c.embed(["ping"])
            return c, f"{c.chat_model} via LM Studio"
        except Exception:
            pass
    return FakeLLM(), "FakeLLM (offline — no memories will be distilled)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="owner/name")
    ap.add_argument("--source", choices=["prs-issues", "commits"], default="prs-issues",
                    help="what to analyze: merged PRs + closed issues, or commit messages")
    ap.add_argument("--limit", type=int, default=20, help="max items to scan")
    ap.add_argument("--query", help="optional recall query to run against the learned memory")
    ap.add_argument("--fake", action="store_true")
    ap.add_argument("--save", help="write the learned memory to this JSON file")
    args = ap.parse_args()

    if not os.path.exists(TOKEN_FILE):
        raise SystemExit(f"Token file not found: {TOKEN_FILE}")
    token = open(TOKEN_FILE).read().strip()

    llm, backend = build_llm(args.fake)
    print(f"backend: {backend}")
    print(f"analyzing: {args.repo}  (read-only)\n")

    history = (fetch_commit_history(args.repo, token, args.limit)
               if args.source == "commits"
               else fetch_history(args.repo, token, args.limit))
    if not history:
        print(f"No usable {args.source} history found to analyze.")
        if args.source == "prs-issues":
            print("Tip: this repo may commit straight to main. Try --source commits.")
        return 0
    label = ("commit log" if args.source == "commits"
             else f"{len(history)} merged PRs / closed issues")
    print(f"source: {label}\n")

    store = MemoryStore(path=args.save or os.devnull)
    all_new = []
    for src, text in history:
        new = ingest(llm, store, source=src, content=text)
        all_new.extend(new)
        for r in new:
            print(f"  + [{r.type.value}] ({src}) {r.claim}")
    reconcile(llm, store, all_new)

    print("\n" + "=" * 64)
    print(f"LEARNED MEMORY for {args.repo}")
    print("=" * 64)
    st = store.stats()
    print(f"{st['active']} active · {st['superseded']} superseded · "
          f"{st['retracted']} retracted · {st['total']} total\n")
    by_type = {}
    for r in store.active():
        by_type.setdefault(r.type.value, []).append(r)
    for t, recs in by_type.items():
        print(f"  {t.upper()} ({len(recs)}):")
        for r in recs:
            print(f"    • {r.claim}  ({', '.join(r.source)})")
        print()

    if args.query:
        print("=" * 64)
        print(f"RECALL for query: {args.query!r}")
        print("=" * 64)
        results = recall(llm, store, args.query, k=5, min_score=0.2)
        print(format_comment(results) or "(no relevant memory)")

    if args.save:
        store.save()
        print(f"\nSaved learned memory to {args.save}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
