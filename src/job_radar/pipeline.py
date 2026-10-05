"""The full retrieval pipeline: rewrite -> hybrid search -> re-rank -> confidence check.

Each stage can be switched off, which is what the evaluation uses to measure what each one
brings (ablation).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import psycopg

from .models import Kind
from .rerank import Reranker, rerank
from .rewrite import RewrittenQuery
from .search import Candidate, Mode, SearchQuery, keywords_of, search

# Below this re-ranking score for the best candidate, the radar says it found nothing
# convincing instead of presenting weak matches as answers. To tune on the evaluation set.
CONFIDENCE = 0.5


@dataclass
class Results:
    query: SearchQuery
    rewritten: RewrittenQuery | None
    candidates: list[Candidate]
    confident: bool
    best_score: float | None


def find(
    conn: psycopg.Connection,
    request: str,
    latitude: float,
    longitude: float,
    radius_km: float,
    embedder,
    kinds: Sequence[Kind] = (),
    profile: str = "",
    rewriter: Callable[[str, str], RewrittenQuery] | None = None,
    reranker: Reranker | None = None,
    mode: Mode = "hybrid",
    pool: int = 100,
    top_k: int = 50,
    limit: int = 20,
) -> Results:
    rewritten = rewriter(request, profile) if rewriter else None
    text = rewritten.semantic_query if rewritten else request
    keywords = tuple(rewritten.keywords) if rewritten else keywords_of(request)
    if not kinds and rewritten:
        kinds = tuple(Kind(k) for k in rewritten.kinds)
    query = SearchQuery(text, latitude, longitude, radius_km, keywords, tuple(kinds))
    vector = embedder.query(text) if mode != "fulltext" else None
    candidates = search(conn, query, vector, mode=mode, pool=pool, limit=top_k)
    best = None
    if reranker and candidates:
        candidates = rerank(candidates, text, reranker, top_k)
        best = candidates[0].extra["rerank"]
    confident = bool(candidates) and (best is None or best >= CONFIDENCE)
    return Results(query, rewritten, candidates[:limit], confident, best)
