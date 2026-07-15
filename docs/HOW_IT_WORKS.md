# How gitmemory works — a plain-English guide

This document explains what `gitmemory` is, the problem it solves, and exactly how
it behaves — with real examples captured from a live run on a local model
(`google/gemma-4-e4b` in LM Studio).

If you only read one section, read [The idea in one minute](#the-idea-in-one-minute).

---

## The idea in one minute

Every software project builds up **"why" knowledge**:

- *"We chose X over Y because Y caused bug Z."*
- *"Don't raise this cache timeout — it caused oversells."*
- *"We already tried server-side rendering; it was too slow."*

This knowledge lives in people's heads and in old PR/issue comments. Months later
nobody remembers it, so teams **repeat mistakes they already learned from**.

`gitmemory` is an AI that:

1. **Learns** the durable lessons from your PRs and issues,
2. **Reminds** you of the relevant ones when you open new work, and
3. **Forgets** a lesson automatically once a newer decision makes it outdated.

That third part — a *retraction lifecycle* — is what makes it different from a normal
"chat with your docs" tool, which keeps resurfacing stale, wrong advice forever.

It runs entirely on a **local model** (via LM Studio), so nothing leaves your machine.

---

## The problem, concretely

Imagine this timeline on a real team:

| When | What happened |
| --- | --- |
| January | PR #12: "Use optimistic locking for orders. Row locks caused deadlocks." |
| March | A new engineer opens a PR adding `SELECT ... FOR UPDATE` row locks. |
| March | Nobody remembers PR #12. The deadlock bug comes back. |

The knowledge to prevent this **already existed** in PR #12 — it was just buried.

`gitmemory` would have caught it in March by commenting on the new PR:
*"Heads up: PR #12 deliberately rejected row locks because they caused deadlocks."*

---

## The four things it does

### 1. Ingest — learn durable memories from a PR/issue

When a PR or issue closes, `gitmemory` reads the text and distills short **memory
records**. There are four kinds:

| Type | Meaning | Example |
| --- | --- | --- |
| `decision` | a choice that was made | "Orders use optimistic locking, not row locks." |
| `gotcha` | a non-obvious trap | "Inventory cache TTL must stay under 60 seconds." |
| `convention` | a rule to follow | "All timestamps are stored in UTC." |
| `dead_end` | something tried and rejected | "SSR for the dashboard is a dead end." |

**Real output** (from ingesting the sample history on gemma):

```
$ gitmemory ingest --source PR#12 --file PR-12-optimistic-locking.md
Ingested 2 memory(ies) from 'PR#12'.
  + [decision]   Orders use optimistic locking (version column), not row locks.
  + [convention] All future order writes must go through OrderRepo.save().
```

Each memory records **where it came from** (`PR#12`) so the reminder can cite its source.

### 2. Recall — surface relevant memories on new work

When new work opens, `gitmemory` finds the most relevant *active* memories and posts a
comment. It uses semantic search (embeddings), so it matches on **meaning**, not keywords.

**Real output** — a new PR asks about order deadlocks, and gitmemory recalls PR #12:

```
### 🧠 Relevant project memory

- 📌 Optimistic locking was chosen to handle concurrent order updates.  (PR#12)
     why: row locks caused lock-wait timeouts and deadlocks under load
     relevance: 0.80
- 📐 All future order writes must use OrderRepo.save().  (PR#12)
     relevance: 0.64
```

The `relevance` score (0–1) is cosine similarity between the new text and the memory.

### 3. Reconcile — supersede/retract outdated memories (the key part)

When a new memory **contradicts** an old one, gitmemory transitions the old memory's
status so it stops being recalled:

```
active  ──supersede──▶  superseded     (a newer decision replaced it)
   │
   └────retract──────▶  retracted      (proven wrong, no direct replacement)
```

**Real output** — the team reverses course and switches to row locks. gitmemory
detects the conflict and supersedes the old decision:

```
$ gitmemory ingest --source PR#41   # row locks replace optimistic locking
  + [decision] Orders now use SELECT FOR UPDATE row-level locking.
  + [dead_end] Optimistic version-column locking is no longer used for orders.

  ~ SUPERSEDE mem_50ed60 → mem_f953d9
    "Row-level locking replaces the earlier optimistic-locking decision."
  ~ SUPERSEDE mem_f3dd93 → mem_eb00aa
    "OrderRepo.save() version-check requirement is now obsolete."
```

Notice the model even superseded the *supporting convention* (`OrderRepo.save()`),
because it only mattered while optimistic locking was in use. That's the lifecycle
doing real work.

### 4. Recall again — the stale memory is gone

After the reversal, asking the same question surfaces the **new** decision. The old,
now-wrong one never comes back:

```
### 🧠 Relevant project memory

- 📌 Orders now use SELECT FOR UPDATE row-level locking.  (PR#41)   relevance: 0.79
- 🚫 Optimistic version-column locking is no longer used.  (PR#41)  relevance: 0.75
```

This is the whole point: **stale knowledge is structurally excluded from recall.**

---

## Why "git-native" and "local model" matter

**Git-native storage.** Memories are stored inside your repo as a single JSON file
(`.gitmemory/memories.json`). This means:

- Every change (add / supersede / retract) is a normal **git diff** you can review.
- "Forgetting" is a real, reversible **git commit** — fully auditable.
- No external database needed to start.

**Local model (LM Studio).** All AI runs on your own machine:

- **Private** — your PR/issue text and code context never leave your network.
- **Free** — no per-token cloud API bills.
- **Portable** — the client uses only the Python standard library.

---

## How it plugs into GitHub

`gitmemory` ships as a **GitHub Action** with two modes:

- On a **new PR/issue** → `recall` mode posts the "relevant memory" comment.
- On a **PR merge** → `ingest` mode distills new memories, reconciles conflicts, and
  opens a **pull request** updating the memory file (so a human approves every change —
  memory is never silently rewritten).

Because the model is local, the Action runs on a **self-hosted runner** that has LM
Studio running. See [`examples/workflows/gitmemory.yml`](../examples/workflows/gitmemory.yml).

### Safety guardrails
- Never edits your code — only writes memory files and comments.
- Supersede/retract always goes through a PR for human review.
- Scoped token permissions and a cost cap per run.

---

## How we know it works (evaluation)

`gitmemory` ships with an eval harness (`eval/run_eval.py`) that measures the metrics
that matter for a memory system. Live results on `google/gemma-4-e4b`:

| Metric | Meaning | Result |
| --- | --- | --- |
| `recall_top1_accuracy` | is the top-ranked memory the right one? | **1.000** |
| `recall_recall@3` | did we find all relevant memories? | 1.000 |
| `staleness_rate` | did we ever surface a stale memory? (want 0) | **0.000** |
| `conflict_detection_acc` | did we correctly supersede/retract? | 1.000 |

The harness **fails CI if `staleness_rate > 0`**, turning the core guarantee into an
automated check.

---

## Try it yourself

```bash
# Offline — no model needed (uses a deterministic fake backend)
python sample/live_demo.py --fake

# Live — against your local model in LM Studio
lms server start
lms load google/gemma-4-e4b            # any chat model
# also load an embedding model, e.g. text-embedding-nomic-embed-text-v1.5
python sample/live_demo.py
```

You'll see the full loop: ingest → recall → supersede → recall (stale memory gone).

---

## The one-sentence pitch

> *gitmemory is an AI memory layer for GitHub that captures why decisions were made,
> reminds you before you repeat old mistakes, and automatically retires knowledge once
> it's outdated — running entirely on a local model.*

---

## Glossary

- **Memory record** — a short, durable fact distilled from a PR/issue, with its source.
- **Embedding** — a numeric vector representing text meaning, used for semantic search.
- **Recall** — retrieving the memories most relevant to some new text.
- **Supersede** — mark an old memory as replaced by a newer one.
- **Retract** — mark a memory as simply wrong / no longer true.
- **Reconcile** — the step that detects conflicts and applies supersede/retract.
- **Ingest** — read a PR/issue and create memory records from it.
