"""The step-by-step trace of one search, on the test database with fake models."""

from job_radar.explain import explain
from job_radar.rewrite import RewrittenQuery
from job_radar.sources.geo import Town
from tests.test_pipeline import FakeEmbedder, FakeReranker
from tests.test_search import MULHOUSE, indexed  # noqa: F401  (indexed is a fixture)


def test_every_stage_of_the_rag_is_shown(indexed):  # noqa: F811
    town = Town("Mulhouse", "68224", ("68100",), *MULHOUSE, 105000)
    rewritten = RewrittenQuery(
        semantic_query="Alternance data analyst SQL",
        keywords=["data", "SQL"],
        kinds=["apprenticeship", "job"],  # as the model returns them: plain strings
        town=None,
        radius_km=None,
    )
    report = explain(
        indexed, "data sql", town, 30, FakeEmbedder(), FakeReranker(),
        lambda request, profile: rewritten, "", None, "ollama:test",
    )  # fmt: skip
    for stage in ("## 1. Réécriture", "## 2. Recherche hybride", "## 3. Re-ranking",
                  "## 4. Confiance"):  # fmt: skip
        assert stage in report
    assert "ollama:test" in report and "Data analyst (H/F)" in report
    assert "| Rang sens | Rang mots | Score RRF |" in report
