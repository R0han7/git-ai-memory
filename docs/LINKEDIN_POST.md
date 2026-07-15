# LinkedIn post draft

Copy/paste and tweak. Two versions: a longer story post and a short punchy one.
Add a screenshot of the live demo issue and/or the demo GIF for reach.

---

## Version A — the story (recommended)

Teams don't lose knowledge because it was never written down.
They lose it because it's buried — and worse, the *outdated* notes keep resurfacing.

So I built **gitmemory**: an AI memory layer for GitHub that runs on a **local model**.

What it does:
🧠 Learns the durable "why" from your PRs and issues (decisions, gotchas, conventions, dead-ends)
⚠️ Reminds you on new PRs before you repeat a past mistake
♻️ And — the part nobody else does — automatically **retires** a memory once a newer decision overrides it, so stale knowledge never comes back

The interesting engineering bits:
• Runs entirely on a local model via LM Studio (gemma-4-e4b) — private, no cloud API, zero runtime dependencies
• Memory is stored *inside the repo* as versioned JSON, so every add/supersede is an auditable git diff
• A commutative union merge driver means multiple branches can edit memory with zero conflicts
• Ships with an evaluation harness: on the local model it hits top-1 recall accuracy 1.0 and a 0.0 staleness rate (the guarantee that stale memory is never surfaced is enforced as a CI check)

I ran it live on a real GitHub repo: it caught a PR proposing a change that contradicted an earlier decision, then — after that decision was reversed — superseded the old memory and updated its own recommendation. All on a model running on my laptop.

Repo (code + docs + demo): https://github.com/R0han7/git-ai-memory

Built with Python. Feedback welcome 🙌

#AI #Agents #LLM #GitHub #DeveloperTools #LocalLLM #MachineLearning #SoftwareEngineering

---

## Version B — short

Most "AI for your codebase" tools keep resurfacing outdated advice.

I built gitmemory to fix that: it learns the "why" behind your PRs, reminds you before
you repeat a past mistake, and — uniquely — **retires** knowledge once a newer decision
overrides it. Runs 100% on a local model (LM Studio), stores memory as auditable git
history, and posts recall comments straight onto GitHub issues/PRs.

Live-tested on a real repo. Code: https://github.com/R0han7/git-ai-memory

#AI #LLM #Agents #GitHub #LocalLLM #DeveloperTools

---

## Posting tips
- Lead with the screenshot of the live demo issue (docs/live-demo.png) or the demo GIF.
- The first two lines are what people see before "…more" — keep the hook tight.
- Pin a first comment with the repo link (LinkedIn suppresses reach on posts with links
  in the body; putting the link in a comment sometimes helps).
