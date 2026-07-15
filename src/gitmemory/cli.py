"""Command-line interface for gitmemory.

Subcommands:
    init       create an empty memory store
    ingest     distill memories from PR/issue text and (optionally) reconcile
    recall     surface relevant active memories for new text (prints a comment)
    reconcile  re-run conflict detection across the store's most recent memories
    stats      print store statistics

The same entrypoint is invoked by the GitHub Action (see action.yml). Text can
be supplied via --text, --file, or stdin so it composes with `gh` / event JSON.
Use --fake to run fully offline (deterministic embeddings, no model needed).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from typing import Optional

from .ingest import embed_records, ingest
from .llm import FakeLLM, LLMClient, LMStudioClient
from .recall import format_comment, recall
from .retract import reconcile
from .store import DEFAULT_STORE_PATH, MemoryStore, merge_stores


def _build_llm(args) -> LLMClient:
    if args.fake:
        return FakeLLM()
    return LMStudioClient(
        base_url=args.base_url,
        chat_model=args.chat_model,
        embedding_model=args.embedding_model,
    )


def _read_text(args) -> str:
    if getattr(args, "text", None):
        return args.text
    if getattr(args, "file", None):
        with open(args.file, "r", encoding="utf-8") as fh:
            return fh.read()
    if not sys.stdin.isatty():
        return sys.stdin.read()
    return ""


def _write_output(text: str, path: Optional[str]) -> None:
    """Write to a file if given, else stdout. Also append to GITHUB_OUTPUT."""
    if path:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text + ("\n" if not text.endswith("\n") else ""))


def cmd_init(args) -> int:
    store = MemoryStore.load(args.store)
    store.save()
    print(f"Initialized memory store at {args.store} ({len(store)} records).")
    return 0


def cmd_ingest(args) -> int:
    llm = _build_llm(args)
    store = MemoryStore.load(args.store)
    content = _read_text(args)
    if not content.strip():
        print("No content provided to ingest.", file=sys.stderr)
        return 2
    new_records = ingest(llm, store, source=args.source, content=content)
    applied = []
    if not args.no_reconcile and new_records:
        applied = reconcile(llm, store, new_records)
    store.save()
    print(f"Ingested {len(new_records)} memory(ies) from {args.source!r}.")
    for r in new_records:
        print(f"  + {r.id} {r.short()}")
    if applied:
        print(f"Applied {len(applied)} lifecycle transition(s):")
        for p in applied:
            print(f"  ~ {p.action}: {p.existing_id} (by {p.new_id}) — {p.explanation}")
    return 0


def cmd_recall(args) -> int:
    llm = _build_llm(args)
    store = MemoryStore.load(args.store)
    query = _read_text(args)
    if not query.strip():
        print("No query text provided.", file=sys.stderr)
        return 2
    results = recall(llm, store, query, k=args.k, min_score=args.min_score)
    comment = format_comment(results)
    if not comment:
        comment = ""  # nothing relevant; Action can skip posting
        print("No relevant memories found.", file=sys.stderr)
    _write_output(comment, args.output)
    return 0


def cmd_reconcile(args) -> int:
    llm = _build_llm(args)
    store = MemoryStore.load(args.store)
    # Ensure embeddings exist for records that lack them (e.g. hand-seeded).
    missing = [r for r in store.all() if not r.embedding]
    if missing:
        embed_records(llm, missing)
    applied = reconcile(llm, store, store.active())
    store.save()
    print(f"Reconcile complete. Applied {len(applied)} transition(s).")
    for p in applied:
        print(f"  ~ {p.action}: {p.existing_id} (by {p.new_id}) — {p.explanation}")
    return 0


def cmd_stats(args) -> int:
    store = MemoryStore.load(args.store)
    for key, val in store.stats().items():
        print(f"{key:12} {val}")
    return 0


def cmd_merge(args) -> int:
    """Union-merge memory files. Used both manually and as a git merge driver.

    As a merge driver git invokes: `gitmemory merge %O %A %B -o %A`
    (ancestor, ours, theirs -> write result over ours). Merge is commutative, so
    argument order does not matter. Missing inputs (e.g. a base with no memory
    file yet) are skipped.
    """
    stores = [MemoryStore.load(p) for p in args.inputs if os.path.exists(p)]
    if not stores:
        print("No input memory files found to merge.", file=sys.stderr)
        return 2
    out_path = args.output or args.inputs[0]
    merged = merge_stores(stores, path=out_path)
    merged.save()
    print(
        f"Merged {len(stores)} file(s) -> {out_path} "
        f"({len(merged)} records; {merged.stats()['active']} active)."
    )
    return 0


def cmd_install_merge_driver(args) -> int:
    """Register the union merge driver in the current git repo + .gitattributes.

    After this, local merges/rebases that touch the memory file resolve
    automatically instead of raising conflicts.
    """
    driver_cmd = "gitmemory merge %O %A %B -o %A"
    try:
        subprocess.run(
            ["git", "config", "merge.gitmemory.name", "gitmemory union merge"],
            check=True,
        )
        subprocess.run(
            ["git", "config", "merge.gitmemory.driver", driver_cmd], check=True
        )
    except FileNotFoundError:
        print("git not found on PATH.", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as e:
        print(f"Failed to configure git merge driver: {e}", file=sys.stderr)
        return 1

    attr_line = f"{args.store} merge=gitmemory"
    attr_path = ".gitattributes"
    existing = ""
    if os.path.exists(attr_path):
        with open(attr_path, "r", encoding="utf-8") as fh:
            existing = fh.read()
    if attr_line not in existing.splitlines():
        with open(attr_path, "a", encoding="utf-8") as fh:
            if existing and not existing.endswith("\n"):
                fh.write("\n")
            fh.write(attr_line + "\n")
        print(f"Added to {attr_path}: {attr_line}")
    print("Installed gitmemory union merge driver "
          "(merge.gitmemory.driver). Local merges of the memory file are now "
          "conflict-free.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gitmemory", description=__doc__)
    p.add_argument("--store", default=os.environ.get("GITMEMORY_STORE", DEFAULT_STORE_PATH),
                   help="path to the memory JSON file")
    p.add_argument("--fake", action="store_true",
                   help="use the offline deterministic FakeLLM (no model needed)")
    p.add_argument("--base-url", default=os.environ.get("LMSTUDIO_BASE_URL", "http://localhost:1234/v1"))
    p.add_argument("--chat-model", default=os.environ.get("LMSTUDIO_CHAT_MODEL", "local-model"))
    p.add_argument("--embedding-model",
                   default=os.environ.get("LMSTUDIO_EMBED_MODEL", "text-embedding-nomic-embed-text-v1.5"))

    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("init", help="create an empty memory store")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("ingest", help="distill memories from PR/issue text")
    sp.add_argument("--source", required=True, help="provenance label, e.g. PR#231")
    sp.add_argument("--text", help="content inline")
    sp.add_argument("--file", help="read content from file")
    sp.add_argument("--no-reconcile", action="store_true",
                    help="skip conflict detection after ingest")
    sp.set_defaults(func=cmd_ingest)

    sp = sub.add_parser("recall", help="surface relevant active memories")
    sp.add_argument("--text", help="query content inline")
    sp.add_argument("--file", help="read query from file")
    sp.add_argument("--k", type=int, default=5, help="max memories to surface")
    sp.add_argument("--min-score", type=float, default=0.15,
                    help="minimum cosine similarity to surface")
    sp.add_argument("--output", help="write the Markdown comment to this file")
    sp.set_defaults(func=cmd_recall)

    sp = sub.add_parser("reconcile", help="re-run conflict detection across the store")
    sp.set_defaults(func=cmd_reconcile)

    sp = sub.add_parser("stats", help="print store statistics")
    sp.set_defaults(func=cmd_stats)

    sp = sub.add_parser(
        "merge",
        help="union-merge memory files (also usable as a git merge driver)",
    )
    sp.add_argument("inputs", nargs="+",
                    help="memory JSON files to union (missing ones are skipped)")
    sp.add_argument("-o", "--output",
                    help="output path (default: first input, i.e. in-place)")
    sp.set_defaults(func=cmd_merge)

    sp = sub.add_parser(
        "install-merge-driver",
        help="register the union merge driver in this git repo + .gitattributes",
    )
    sp.set_defaults(func=cmd_install_merge_driver)

    return p


def main(argv: Optional[list] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
