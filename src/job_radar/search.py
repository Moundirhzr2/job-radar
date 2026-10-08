"""Hybrid retrieval over the offers on the radar.

One SQL query:
1. keeps the offers within the radius and of the requested kinds (PostGIS, GIN on kinds);
2. ranks them by meaning (pgvector cosine distance to the query embedding) and by words
   (French full-text, accent-insensitive, keywords OR-ed so that a question need not contain
   every word of the offer);
3. fuses the two rankings with Reciprocal Rank Fusion (score = sum of 1 / (60 + rank)).

The vector ranking is exact inside the radius rather than an approximate index scan: the area
holds a few thousand offers at most, and a filtered approximate search can silently miss good
results.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import psycopg

from .models import Kind

Mode = Literal["hybrid", "vector", "fulltext"]
RRF_K = 60


@dataclass(frozen=True)
class SearchQuery:
    text: str  # what the student is looking for, in plain words (embedded)
    latitude: float
    longitude: float
    radius_km: float
    keywords: tuple[str, ...] = ()  # exact terms for full-text: "SQL", "Power BI", "alternance"
    kinds: tuple[Kind, ...] = ()


@dataclass(frozen=True)
class Candidate:
    id: int
    title: str
    company: str
    city: str
    url: str
    kinds: list[str]
    distance_km: float
    description: str
    vector_rank: int | None  # rank in the meaning-based list, None if absent from it
    text_rank: int | None  # rank in the word-based list
    score: float  # RRF score (or the single list's score in vector / fulltext mode)
    weekly_hours: float | None = None
    extra: dict = field(default_factory=dict, compare=False)


_SQL = """
WITH here AS (SELECT ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography AS p),
area AS (
    SELECT o.id FROM offers o, here
    WHERE ST_DWithin(o.location, here.p, %(radius_m)s) AND o.closed_at IS NULL
      AND (%(kinds)s::text[] IS NULL OR o.kinds && %(kinds)s::text[])
),
-- One tsquery from all keywords: words of a keyword are AND-ed, keywords are OR-ed.
words AS (
    SELECT coalesce(string_agg('(' || q::text || ')', ' | '), '')::tsquery AS q
    FROM unnest(%(keywords)s::text[]) AS k, plainto_tsquery('fr_unaccent', k) AS q
    WHERE q::text <> ''
),
vec AS (
    SELECT id, row_number() OVER (ORDER BY dist, id) AS rank
    FROM (
        SELECT o.id, o.embedding <=> %(qvec)s AS dist
        FROM offers o JOIN area USING (id)
        WHERE %(use_vector)s AND o.embedding IS NOT NULL
        ORDER BY dist, o.id
        LIMIT %(pool)s
    ) nearest
),
fts AS (
    SELECT id, row_number() OVER (ORDER BY score DESC, id) AS rank
    FROM (
        SELECT o.id, ts_rank_cd(o.tsv, words.q, 1) AS score
        FROM offers o JOIN area USING (id), words
        WHERE %(use_text)s AND o.tsv @@ words.q
        ORDER BY score DESC, o.id
        LIMIT %(pool)s
    ) matching
),
fused AS (
    SELECT coalesce(v.id, t.id) AS id, v.rank AS vector_rank, t.rank AS text_rank,
           coalesce(1.0::float8 / (%(rrf_k)s + v.rank), 0)
         + coalesce(1.0::float8 / (%(rrf_k)s + t.rank), 0) AS score
    FROM vec v FULL OUTER JOIN fts t ON v.id = t.id
)
SELECT o.id, o.title, o.company_name, o.city, o.url, o.kinds,
       ST_Distance(o.location, here.p) / 1000, o.description,
       f.vector_rank, f.text_rank, f.score, o.weekly_hours
FROM fused f JOIN offers o USING (id), here
ORDER BY f.score DESC, o.id
LIMIT %(limit)s
"""


def search(
    conn: psycopg.Connection,
    query: SearchQuery,
    query_vector: np.ndarray | None,
    mode: Mode = "hybrid",
    pool: int = 100,
    limit: int = 50,
) -> list[Candidate]:
    use_vector = mode in ("hybrid", "vector")
    use_text = mode in ("hybrid", "fulltext") and bool(query.keywords)
    if use_vector and query_vector is None:
        raise ValueError(f"{mode} search needs a query embedding")
    rows = conn.execute(
        _SQL,
        {
            "lat": query.latitude,
            "lon": query.longitude,
            "radius_m": query.radius_km * 1000,
            "kinds": [k.value for k in query.kinds] or None,
            "keywords": list(query.keywords),
            "qvec": query_vector if use_vector else np.zeros(1024, dtype=np.float32),
            "use_vector": use_vector,
            "use_text": use_text,
            "pool": pool,
            "limit": limit,
            "rrf_k": RRF_K,
        },
    ).fetchall()
    return [Candidate(*row) for row in rows]


def keywords_of(text: str) -> tuple[str, ...]:
    """Without query rewriting, the request itself is the only keyword."""
    return (text,) if text.strip() else ()


def kinds_of(values: Sequence[str] | None) -> tuple[Kind, ...]:
    return tuple(Kind(v) for v in values or ())
