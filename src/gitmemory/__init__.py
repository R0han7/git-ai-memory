"""gitmemory: git-native AI memory for GitHub with a retraction lifecycle.

Core pieces:
    - models:  MemoryRecord + lifecycle status (active / superseded / retracted)
    - llm:     mockable LLM interface + LM Studio (OpenAI-compatible) client
    - store:   in-repo JSON memory store with embedding-based cosine recall
    - ingest:  distill durable memories from PR / issue text
    - recall:  retrieve relevant *active* memories and format a surfacing comment
    - retract: detect conflicts and propose lifecycle transitions
"""

from .models import MemoryRecord, MemoryStatus, MemoryType
from .store import MemoryStore
from .llm import LLMClient, LMStudioClient, FakeLLM

__all__ = [
    "MemoryRecord",
    "MemoryStatus",
    "MemoryType",
    "MemoryStore",
    "LLMClient",
    "LMStudioClient",
    "FakeLLM",
]

__version__ = "0.1.0"
