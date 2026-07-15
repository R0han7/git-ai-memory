"""Prompt templates and a tolerant JSON extractor for local-model output.

Local / small models are less reliable at strict JSON than frontier models, so
`extract_json` is deliberately forgiving: it strips markdown code fences and
falls back to locating the first balanced JSON object/array in the text.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

# --------------------------------------------------------------------------- #
# Ingest                                                                       #
# --------------------------------------------------------------------------- #
INGEST_SYSTEM = """You are a meticulous engineering historian. \
From a GitHub pull request or issue, extract only DURABLE memories worth \
remembering months from now: decisions, gotchas, conventions, and dead-ends. \
Ignore transient chatter, greetings, and status updates.

Return STRICT JSON with this shape:
{
  "memories": [
    {
      "type": "decision|gotcha|convention|dead_end",
      "claim": "one concise sentence stating the durable fact",
      "reason": "why it is true / the rationale or evidence",
      "tags": ["short", "topical", "tags"],
      "confidence": 0.0
    }
  ]
}
If nothing is worth remembering, return {"memories": []}. \
Output JSON only, no prose."""

INGEST_USER = """Source: {source}

--- CONTENT START ---
{content}
--- CONTENT END ---

Extract durable memories as JSON."""


# JSON schema passed to the model for structured output (LM Studio json_schema).
INGEST_SCHEMA = {
    "type": "object",
    "properties": {
        "memories": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["decision", "gotcha", "convention", "dead_end"],
                    },
                    "claim": {"type": "string"},
                    "reason": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "confidence": {"type": "number"},
                },
                "required": ["type", "claim", "reason", "tags", "confidence"],
            },
        }
    },
    "required": ["memories"],
}


# --------------------------------------------------------------------------- #
# Conflict detection (retract / supersede)                                     #
# --------------------------------------------------------------------------- #
CONFLICT_SYSTEM = """You compare a NEW engineering memory against EXISTING \
active memories and decide whether the new one makes any existing memory \
OUTDATED or WRONG.

Report a conflict ONLY when the new memory genuinely contradicts or replaces an \
existing one. Choose exactly one action:
  - "supersede": the new memory changes/replaces the existing decision on the \
SAME topic (e.g. "we now use X instead of Y").
  - "retract":   the existing memory is simply false now, with no direct \
replacement.

Do NOT report a conflict when memories merely:
  - reinforce, confirm, support, or elaborate on each other,
  - are about related but different topics,
  - or could both be true at the same time.
When in doubt, report NO conflict.

Return STRICT JSON:
{
  "conflicts": [
    {"existing_id": "mem_xxx", "action": "supersede|retract", "explanation": "why"}
  ]
}
If there are no genuine conflicts, return {"conflicts": []}. \
Output JSON only, no prose."""

CONFLICT_USER = """NEW memory:
{new_memory}

EXISTING active memories (id — claim — reason):
{existing}

Return conflicts as JSON."""


CONFLICT_SCHEMA = {
    "type": "object",
    "properties": {
        "conflicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "existing_id": {"type": "string"},
                    "action": {"type": "string", "enum": ["supersede", "retract"]},
                    "explanation": {"type": "string"},
                },
                "required": ["existing_id", "action", "explanation"],
            },
        }
    },
    "required": ["conflicts"],
}


# --------------------------------------------------------------------------- #
# Tolerant JSON extraction                                                     #
# --------------------------------------------------------------------------- #
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_json(text: str) -> Optional[Any]:
    """Best-effort parse of a JSON object/array from possibly-noisy model text.

    Returns the parsed value, or None if nothing parseable is found.
    """
    if text is None:
        return None
    text = text.strip()
    if not text:
        return None

    # 1) direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 2) fenced code block
    m = _FENCE_RE.search(text)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass

    # 3) first balanced {...} or [...] span
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = text.find(open_ch)
        while start != -1:
            depth = 0
            for i in range(start, len(text)):
                c = text[i]
                if c == open_ch:
                    depth += 1
                elif c == close_ch:
                    depth -= 1
                    if depth == 0:
                        candidate = text[start : i + 1]
                        try:
                            return json.loads(candidate)
                        except json.JSONDecodeError:
                            break
            start = text.find(open_ch, start + 1)
    return None
