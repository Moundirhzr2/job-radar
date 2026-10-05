"""Re-ranking: reread each (request, offer) pair with a cross-encoder and reorder.

The hybrid search compares one query vector with offer vectors computed separately; it is fast
but coarse. A cross-encoder reads the request and the full offer together and scores how well
they match, which is slower, so it only reorders the hybrid search's top candidates.

Model: jina-reranker-v2-base-multilingual (French and English, local, licence CC BY-NC 4.0:
fine for this non-commercial project, to revisit for any commercial use).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import replace

from .embed import KIND_WORDS, local_model_dir
from .search import Candidate

RERANK_MODEL = "jinaai/jina-reranker-v2-base-multilingual"
MAX_OFFER_CHARS = 2500


def candidate_text(c: Candidate) -> str:
    kinds = ", ".join(KIND_WORDS.get(k, k) for k in c.kinds)
    head = " — ".join(p for p in (c.title, c.company, c.city) if p)
    return f"{head}\n{kinds}\n{c.description}"[:MAX_OFFER_CHARS]


def sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


class Reranker:
    def __init__(self, model: str = RERANK_MODEL, batch_size: int = 8):
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        (description,) = [
            m for m in TextCrossEncoder.list_supported_models() if m["model"] == model
        ]
        self.batch_size = batch_size
        self._model = TextCrossEncoder(model, specific_model_path=str(local_model_dir(description)))

    def scores(self, query: str, documents: Sequence[str]) -> list[float]:
        """Relevance between 0 and 1 (the model's logits through a sigmoid)."""
        raw = self._model.rerank(query, list(documents), batch_size=self.batch_size)
        return [sigmoid(float(s)) for s in raw]


def rerank(
    candidates: Sequence[Candidate], query: str, reranker: Reranker, top_k: int = 50
) -> list[Candidate]:
    """The first `top_k` candidates reordered by cross-encoder relevance (kept in `extra`)."""
    head = list(candidates[:top_k])
    if not head:
        return []
    scores = reranker.scores(query, [candidate_text(c) for c in head])
    rescored = [
        replace(c, extra={**c.extra, "rerank": s, "hybrid_rank": i + 1})
        for i, (c, s) in enumerate(zip(head, scores, strict=True))
    ]
    return sorted(rescored, key=lambda c: -c.extra["rerank"])
