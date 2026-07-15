"""LLM access layer.

Design goals:
    * A tiny, mockable interface (`LLMClient`) so agents never talk to a concrete
      backend directly and can be unit-tested without a running model.
    * A real `LMStudioClient` that speaks the OpenAI-compatible REST API exposed
      by LM Studio (default http://localhost:1234/v1) using only the standard
      library, so the tool has zero third-party runtime dependencies.
    * A `FakeLLM` for tests / offline evals that returns scripted chat responses
      and deterministic, semantically-plausible embeddings (bag-of-words hashing),
      so recall ranking can be exercised without a model.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import urllib.error
import urllib.request
from typing import Callable, Dict, List, Optional, Protocol, Sequence


class LLMError(RuntimeError):
    """Raised when the LLM backend cannot be reached or returns an error."""


class LLMClient(Protocol):
    """Minimal interface every backend must implement."""

    def chat(self, messages: Sequence[Dict[str, str]], **kwargs) -> str:
        """Return the assistant message content for a chat completion."""
        ...

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        """Return one embedding vector per input text."""
        ...


# --------------------------------------------------------------------------- #
# Real backend: LM Studio (OpenAI-compatible)                                  #
# --------------------------------------------------------------------------- #
class LMStudioClient:
    """Talks to LM Studio's OpenAI-compatible server using stdlib urllib.

    Start LM Studio, load a chat model (and optionally an embedding model),
    and enable the local server (Developer tab). Defaults match LM Studio's
    out-of-the-box configuration.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:1234/v1",
        chat_model: str = "local-model",
        embedding_model: str = "text-embedding-nomic-embed-text-v1.5",
        api_key: str = "lm-studio",
        timeout: float = 120.0,
        temperature: float = 0.2,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.chat_model = chat_model
        self.embedding_model = embedding_model
        self.api_key = api_key
        self.timeout = timeout
        self.temperature = temperature

    def _post(self, path: str, payload: dict) -> dict:
        url = f"{self.base_url}{path}"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as e:  # connection refused, timeout, etc.
            raise LLMError(
                f"Could not reach LM Studio at {url}. Is the local server "
                f"running with a model loaded? Original error: {e}"
            ) from e

    def chat(self, messages: Sequence[Dict[str, str]], **kwargs) -> str:
        payload = {
            "model": kwargs.get("model", self.chat_model),
            "messages": list(messages),
            "temperature": kwargs.get("temperature", self.temperature),
        }
        if "max_tokens" in kwargs:
            payload["max_tokens"] = kwargs["max_tokens"]
        # Structured output. LM Studio requires response_format.type to be
        # "json_schema" or "text" (it rejects "json_object"). Passing an explicit
        # schema also dramatically improves reliability on small/local models,
        # which are otherwise loose with JSON.
        schema = kwargs.get("json_schema")
        if schema:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": kwargs.get("schema_name", "response"),
                    "strict": True,
                    "schema": schema,
                },
            }
        # `json_mode=True` with no schema intentionally sets nothing: we rely on
        # the prompt + the tolerant extract_json parser rather than a flag that
        # some servers reject.
        out = self._post("/chat/completions", payload)
        try:
            return out["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as e:
            raise LLMError(f"Unexpected chat response shape: {out}") from e

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        payload = {"model": self.embedding_model, "input": list(texts)}
        out = self._post("/embeddings", payload)
        try:
            return [item["embedding"] for item in out["data"]]
        except (KeyError, TypeError) as e:
            raise LLMError(f"Unexpected embeddings response shape: {out}") from e


# --------------------------------------------------------------------------- #
# Fake backend: deterministic, offline, dependency-free                        #
# --------------------------------------------------------------------------- #
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def hashing_embedding(text: str, dim: int = 128) -> List[float]:
    """Deterministic bag-of-words hashing embedding.

    Similar texts (shared tokens) produce similar vectors, so cosine recall is
    meaningful in tests and offline evals without needing a real embedding model.
    The vector is L2-normalized.
    """
    vec = [0.0] * dim
    tokens = _TOKEN_RE.findall(text.lower())
    for tok in tokens:
        h = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16)
        idx = h % dim
        sign = 1.0 if (h >> 8) % 2 == 0 else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


class FakeLLM:
    """Scripted chat + deterministic embeddings for tests and offline evals.

    Args:
        chat_responder: optional callable(messages) -> str. If omitted, returns
            an empty JSON object, which callers should handle gracefully.
        dim: embedding dimensionality.
    """

    def __init__(
        self,
        chat_responder: Optional[Callable[[Sequence[Dict[str, str]]], str]] = None,
        dim: int = 128,
    ) -> None:
        self.chat_responder = chat_responder
        self.dim = dim
        self.chat_calls: List[List[Dict[str, str]]] = []
        self.embed_calls: List[List[str]] = []

    def chat(self, messages: Sequence[Dict[str, str]], **kwargs) -> str:
        self.chat_calls.append(list(messages))
        if self.chat_responder is not None:
            return self.chat_responder(messages)
        return "{}"

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        self.embed_calls.append(list(texts))
        return [hashing_embedding(t, self.dim) for t in texts]
