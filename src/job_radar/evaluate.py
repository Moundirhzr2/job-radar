"""Measure retrieval quality before trusting it.

Method (standard in information retrieval):
1. A few real requests (eval/queries.json).
2. Pooling: for each request, the top results of every pipeline configuration are merged into
   one pool; only pooled offers can be judged, so a configuration that finds new offers adds
   them to the pool (judge them too before comparing).
3. Judgments by the student himself (relevant / not relevant / unsure), keyed by the offer's
   identifier at its source, so they survive a database reload.
4. Per configuration, averaged over requests:
   - nDCG@10: are the relevant offers at the top? (1 = perfect order)
   - MRR@10: how high is the first relevant offer? (1 = first, 0.5 = second...)
   - P@10: share of relevant offers in the first 10
   - Recall@50: share of all known relevant offers found in the first 50 (what re-ranking can
     still promote)
   Offers judged "unsure" are left out of every measure.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .pipeline import find

# name -> (mode, rewrite, rerank)
CONFIGS: dict[str, tuple[str, bool, bool]] = {
    "mots-clés seuls": ("fulltext", False, False),
    "vecteurs seuls": ("vector", False, False),
    "hybride": ("hybrid", False, False),
    "hybride + re-ranking": ("hybrid", False, True),
    "réécriture + hybride": ("hybrid", True, False),
    "réécriture + hybride + re-ranking": ("hybrid", True, True),
}


@dataclass(frozen=True)
class EvalQuery:
    id: str
    request: str
    town: str
    radius_km: float
    label: str = ""  # short name shown to the judge


def load_queries(path: Path) -> list[EvalQuery]:
    return [EvalQuery(**q) for q in json.loads(path.read_text(encoding="utf-8"))]


def item_key(query_id: str, source: str, source_id: str) -> str:
    """Stable id of a (request, offer) pair, usable as a document id."""
    return re.sub(r"[^A-Za-z0-9_-]+", "-", f"{query_id}__{source}__{source_id}")


def ranked_keys(conn, query: EvalQuery, results) -> list[str]:
    ids = [c.id for c in results.candidates]
    if not ids:
        return []
    rows = dict(
        (i, (s, sid))
        for i, s, sid in conn.execute(
            "SELECT id, source, source_id FROM offers WHERE id = ANY(%s)", (ids,)
        )
    )
    return [item_key(query.id, *rows[i]) for i in ids if i in rows]


def run_config(
    conn, query: EvalQuery, name: str, town, embedder, reranker, rewriter, profile: str, depth: int
):
    mode, use_rewrite, use_rerank = CONFIGS[name]
    results = find(
        conn,
        query.request,
        town.latitude,
        town.longitude,
        query.radius_km,
        embedder,
        profile=profile,
        rewriter=rewriter if use_rewrite else None,
        reranker=reranker if use_rerank else None,
        mode=mode,
        limit=depth,
    )
    return results


# --- measures ------------------------------------------------------------------------------


def judged(ranking: Sequence[str], labels: dict[str, str]) -> list[str]:
    """The ranking without unsure / unjudged offers (they count neither for nor against)."""
    return [k for k in ranking if labels.get(k) in ("yes", "no")]


def ndcg_at(ranking: Sequence[str], labels: dict[str, str], relevant_total: int, k: int = 10):
    gains = [1.0 if labels[key] == "yes" else 0.0 for key in judged(ranking, labels)[:k]]
    dcg = sum(g / math.log2(i + 2) for i, g in enumerate(gains))
    ideal = sum(1 / math.log2(i + 2) for i in range(min(relevant_total, k)))
    return dcg / ideal if ideal else None


def mrr_at(ranking: Sequence[str], labels: dict[str, str], k: int = 10) -> float:
    for i, key in enumerate(judged(ranking, labels)[:k], 1):
        if labels[key] == "yes":
            return 1 / i
    return 0.0


def precision_at(ranking: Sequence[str], labels: dict[str, str], k: int = 10) -> float | None:
    top = judged(ranking, labels)[:k]
    return sum(labels[key] == "yes" for key in top) / len(top) if top else None


def recall_at(ranking: Sequence[str], relevant: set[str], k: int = 50) -> float | None:
    return len(relevant & set(ranking[:k])) / len(relevant) if relevant else None


@dataclass
class Row:
    config: str
    ndcg10: float | None
    mrr10: float | None
    p10: float | None
    recall50: float | None
    queries: int


def _mean(values) -> float | None:
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def score(rankings: dict[str, dict[str, list[str]]], labels: dict[str, str]) -> list[Row]:
    """`rankings[config][query_id]` is a ranked list of item keys (top 50)."""
    rows = []
    for config, per_query in rankings.items():
        n, m, p, r = [], [], [], []
        for query_id, ranking in per_query.items():
            prefix = f"{query_id}__"
            relevant = {k for k, v in labels.items() if k.startswith(prefix) and v == "yes"}
            if not relevant:
                continue  # a request with no relevant offer cannot rank anything
            n.append(ndcg_at(ranking, labels, len(relevant)))
            m.append(mrr_at(ranking, labels))
            p.append(precision_at(ranking, labels))
            r.append(recall_at(ranking, relevant))
        rows.append(Row(config, _mean(n), _mean(m), _mean(p), _mean(r), len(n)))
    return rows


def to_markdown(rows: Sequence[Row]) -> str:
    def f(v):
        return "–" if v is None else f"{v:.2f}"

    lines = [
        "| Configuration | nDCG@10 | MRR@10 | P@10 | Rappel@50 |",
        "|---|---:|---:|---:|---:|",
    ]
    lines += [
        f"| {r.config} | {f(r.ndcg10)} | {f(r.mrr10)} | {f(r.p10)} | {f(r.recall50)} |"
        for r in rows
    ]
    return "\n".join(lines)


Rewriter = Callable[[str, str], object]


# --- running the configurations --------------------------------------------------------------


class CachedRewriter:
    """Claude's rewrites saved to a file: the evaluation is reproducible and pays only once."""

    def __init__(self, path: Path, rewrite_fn):
        self.path = path
        self.rewrite_fn = rewrite_fn
        self.cache = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def __call__(self, request: str, profile: str):
        from .rewrite import RewrittenQuery

        if request not in self.cache:
            self.cache[request] = self.rewrite_fn(request, profile).model_dump()
            self.path.write_text(
                json.dumps(self.cache, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
            )
        return RewrittenQuery(**self.cache[request])


def collect(
    conn,
    queries: Sequence[EvalQuery],
    towns: dict,
    embedder,
    reranker,
    rewriter,
    profile: str,
    configs: Sequence[str],
    depth: int = 50,
) -> tuple[dict[str, dict[str, list[str]]], dict[str, dict]]:
    """Rankings per configuration and request, and what each ranked offer looks like."""
    rankings: dict[str, dict[str, list[str]]] = {c: {} for c in configs}
    items: dict[str, dict] = {}
    for q in queries:
        for name in configs:
            res = run_config(
                conn, q, name, towns[q.town], embedder, reranker, rewriter, profile, depth
            )
            keys = ranked_keys(conn, q, res)
            rankings[name][q.id] = keys
            for key, c in zip(keys, res.candidates, strict=True):
                items.setdefault(
                    key,
                    {
                        "key": key,
                        "query_id": q.id,
                        "request": q.request,
                        "title": c.title,
                        "company": c.company,
                        "city": c.city,
                        "kinds": c.kinds,
                        "distance_km": round(c.distance_km, 1),
                        "excerpt": c.description[:600],
                        "url": c.url,
                        "found_by": [],
                    },
                )["found_by"].append(name)
    return rankings, items


def pool(
    rankings: dict[str, dict[str, list[str]]], items: dict[str, dict], depth: int = 10
) -> list[dict]:
    """The offers to judge: the union of every configuration's top `depth` per request."""
    keys: list[str] = []
    for per_query in rankings.values():
        for ranking in per_query.values():
            keys.extend(ranking[:depth])
    return [items[k] for k in dict.fromkeys(keys)]


def sheets(queries: Sequence[EvalQuery], to_judge: Sequence[dict]) -> dict[str, dict]:
    """One judging sheet per request, made blind: no configuration names, and the offers in a
    fixed pseudo-random order, so the judge cannot tell which method found which offer."""
    hidden = {"found_by", "query_id", "request"}
    out = {}
    for position, q in enumerate(queries):
        items = [
            {k: v for k, v in it.items() if k not in hidden}
            for it in to_judge
            if it["query_id"] == q.id
        ]
        items.sort(key=lambda it: hashlib.sha256(it["key"].encode()).hexdigest())
        out[q.id] = {
            "label": q.label or q.id,
            "request": q.request,
            "town": q.town,
            "radius_km": q.radius_km,
            "position": position,
            "items": items,
        }
    return out
