"""Rewrite -> hybrid search -> re-rank -> confidence, with fake models (fast, deterministic)."""

from job_radar.models import Kind
from job_radar.pipeline import CONFIDENCE, find
from job_radar.rerank import candidate_text, rerank
from job_radar.rewrite import RewrittenQuery
from job_radar.search import Candidate
from tests.test_search import MULHOUSE, axis, indexed  # noqa: F401  (indexed is a fixture)


class FakeEmbedder:
    def query(self, text):
        return axis(0)


class FakeReranker:
    """Scores an offer by the share of the query's words found in it."""

    def __init__(self):
        self.calls = []

    def scores(self, query, documents):
        self.calls.append((query, list(documents)))
        words = query.lower().split()
        return [sum(w in d.lower() for w in words) / len(words) for d in documents]


def candidate(i, title, description=""):
    return Candidate(i, title, "Acme", "Mulhouse", "u", ["job"], 1.0, description, i, None, 0.1)


def test_rerank_reorders_and_keeps_the_hybrid_rank():
    cands = [candidate(1, "Cuisinier"), candidate(2, "Data analyst SQL"), candidate(3, "Vendeur")]
    out = rerank(cands, "data sql", FakeReranker(), top_k=2)
    assert [c.title for c in out] == ["Data analyst SQL", "Cuisinier"]  # only the top 2 kept
    assert out[0].extra == {"rerank": 1.0, "hybrid_rank": 2}


def test_candidate_text_includes_kind_words():
    text = candidate_text(candidate(1, "Développeur", "Python"))
    assert text.splitlines() == ["Développeur — Acme — Mulhouse", "emploi", "Python"]


def test_rewritten_query_drives_the_search(indexed):  # noqa: F811
    rewritten = RewrittenQuery(
        semantic_query="alternance développement python",
        keywords=["python"],
        kinds=["apprenticeship"],
        town=None,
        radius_km=None,
    )
    reranker = FakeReranker()
    res = find(
        indexed, "dev", *MULHOUSE, 30, FakeEmbedder(),
        rewriter=lambda request, profile: rewritten, reranker=reranker,
    )  # fmt: skip
    assert res.query.keywords == ("python",)
    assert res.query.kinds == (Kind.APPRENTICESHIP,)
    assert [c.title for c in res.candidates] == ["Alternance Développeur Python"]
    assert reranker.calls[0][0] == "alternance développement python"  # reranks on the rewrite
    assert res.confident


def test_low_scores_are_reported_instead_of_answered(indexed):  # noqa: F811
    res = find(
        indexed, "plombier chauffagiste", *MULHOUSE, 30, FakeEmbedder(), reranker=FakeReranker()
    )
    assert res.candidates  # the search always returns something nearby...
    assert res.best_score < CONFIDENCE
    assert not res.confident  # ...but the radar says it is not convincing


def test_without_reranker_the_hybrid_order_is_kept(indexed):  # noqa: F811
    res = find(indexed, "sql", *MULHOUSE, 30, FakeEmbedder())
    assert res.best_score is None and res.confident
    assert res.candidates[0].title == "Data analyst (H/F)"
