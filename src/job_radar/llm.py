"""Which language model answers: Claude (API) or a local model served by Ollama.

The model name chooses: RADAR_LLM=ollama:qwen3.5:4b sends every call to Ollama on this
computer, with no key and nothing leaving it; otherwise Claude (RADAR_CLAUDE_MODEL, key in
RADAR_ANTHROPIC_API_KEY). Both answer in the same validated Pydantic structure: Ollama is
given the model's JSON schema as the required format, and its answer is validated like
Claude's. Analyses are cached per model name, so their answers never mix.
"""

from __future__ import annotations

import os

import httpx
from pydantic import BaseModel

MODEL = os.environ.get("RADAR_LLM") or os.environ.get("RADAR_CLAUDE_MODEL", "claude-opus-5-5")
OLLAMA = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
LOCAL = "ollama:"


def is_local(model: str) -> bool:
    return model.startswith(LOCAL)


def ollama_parse(
    model: str, output: type[BaseModel], system: str, content: str, client: httpx.Client | None
) -> BaseModel:
    """One answer from a local model, held to `output`'s JSON schema, then validated."""
    body = {
        "model": model.removeprefix(LOCAL),
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
        "format": output.model_json_schema(),
        "stream": False,
        "think": False,  # a short structured task: reasoning tokens only add latency
        # 8k tokens hold the CV and an offer; a smaller context leaves room on a 4 GB card.
        "options": {"temperature": 0, "num_ctx": 8192},
        # Unloaded right after answering: on a 4 GB card the re-ranker comes next and needs the
        # room (both loaded, re-ranking 50 offers took 2 min instead of 6 s). A reload from the
        # disk cache costs a few seconds.
        "keep_alive": os.environ.get("RADAR_LLM_KEEP_ALIVE", "0"),
    }
    # A cold model loads from disk first: allow minutes, not seconds.
    http = client or httpx.Client(timeout=300)
    resp = http.post(f"{OLLAMA}/api/chat", json=body)
    resp.raise_for_status()
    return output.model_validate_json(resp.json()["message"]["content"])
