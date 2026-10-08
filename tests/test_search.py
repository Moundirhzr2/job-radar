"""Hybrid search against a real PostgreSQL + PostGIS + pgvector."""

import numpy as np
import pytest

from job_radar import db
from job_radar.models import Kind, Location, Offer
from job_radar.search import RRF_K, SearchQuery, search

MULHOUSE = (47.7508, 7.3359)
STRASBOURG = (48.5734, 7.7521)


def axis(i: int) -> np.ndarray:
    v = np.zeros(1024, dtype=np.float32)
    v[i] = 1.0
    return v


def blend(a: int, b: int, weight: float) -> np.ndarray:
    return (weight * axis(a) + (1 - weight) * axis(b)).astype(np.float32)


OFFERS = [
    # source_id, title, description, place, kinds, vector
    ("dev", "Alternance Développeur Python", "Développement d'API en Python.", MULHOUSE,
     (Kind.APPRENTICESHIP,), blend(0, 1, 0.9)),
    ("data", "Data analyst (H/F)", "SQL et Power BI, analyse de données.", MULHOUSE,
     (Kind.JOB,), blend(0, 1, 0.6)),
    ("cook", "Cuisinier", "Restauration traditionnelle.", MULHOUSE, (Kind.JOB,), axis(2)),
    ("far", "Developpeur Python", "Même métier, mais à Strasbourg.", STRASBOURG,
     (Kind.APPRENTICESHIP,), blend(0, 1, 0.95)),
]  # fmt: skip


@pytest.fixture
def indexed(conn):
    offers = [
        Offer(
            "test",
            sid,
            f"https://example.org/{sid}",
            title,
            description=desc,
            kinds=frozenset(kinds),
            location=Location(latitude=lat, longitude=lon, precision="exact"),
        )
        for sid, title, desc, (lat, lon), kinds, _ in OFFERS
    ]
    db.upsert_offers(conn, offers)
    ids = dict(conn.execute("SELECT source_id, id FROM offers").fetchall())
    db.set_embeddings(conn, [(ids[sid], vec) for sid, *_, vec in OFFERS])
    return conn


def query(**kw):
    defaults = dict(text="développeur", latitude=MULHOUSE[0], longitude=MULHOUSE[1], radius_km=30)
    return SearchQuery(**(defaults | kw))


def titles(results):
    return [r.title for r in results]


def test_fulltext_ignores_accents_and_or_combines_keywords(indexed):
    found = search(indexed, query(keywords=("developpeur",)), None, mode="fulltext")
    assert titles(found) == ["Alternance Développeur Python"]  # Strasbourg is outside 30 km
    found = search(indexed, query(keywords=("power bi", "python")), None, mode="fulltext")
    assert set(titles(found)) == {"Alternance Développeur Python", "Data analyst (H/F)"}


def test_vector_ranks_by_meaning_within_the_radius(indexed):
    found = search(indexed, query(), axis(0), mode="vector")
    assert titles(found) == ["Alternance Développeur Python", "Data analyst (H/F)", "Cuisinier"]
    assert [r.vector_rank for r in found] == [1, 2, 3]
    assert all(r.text_rank is None for r in found)


def test_hybrid_fuses_both_rankings(indexed):
    found = search(indexed, query(keywords=("sql",)), axis(0), mode="hybrid")
    by_title = {r.title: r for r in found}
    data = by_title["Data analyst (H/F)"]
    # Second by meaning, first by words: 1/(60+2) + 1/(60+1).
    assert data.score == pytest.approx(1 / (RRF_K + 2) + 1 / (RRF_K + 1))
    assert titles(found)[0] == "Data analyst (H/F)"
    assert by_title["Cuisinier"].text_rank is None


def test_kinds_and_radius_filters(indexed):
    found = search(indexed, query(kinds=(Kind.APPRENTICESHIP,)), axis(0), mode="vector")
    assert titles(found) == ["Alternance Développeur Python"]
    wide = search(indexed, query(radius_km=150, kinds=(Kind.APPRENTICESHIP,)), axis(0), "vector")
    assert titles(wide) == ["Developpeur Python", "Alternance Développeur Python"]
    assert wide[0].distance_km > 90


def test_no_keywords_means_vector_only_and_missing_vector_is_an_error(indexed):
    assert search(indexed, query(), None, mode="fulltext") == []
    with pytest.raises(ValueError):
        search(indexed, query(), None, mode="hybrid")


def test_withdrawn_offers_are_not_searched(indexed):
    db.close_offers(indexed, "test", [sid for sid, title, *_ in OFFERS if title == "Cuisinier"])
    found = search(indexed, query(), axis(0), mode="vector")
    assert titles(found) == ["Alternance Développeur Python", "Data analyst (H/F)"]
