"""Against a real PostgreSQL + PostGIS + pgvector (docker compose up -d)."""

from dataclasses import replace

import numpy as np

from job_radar import db
from job_radar.models import Kind, Location, Offer

MULHOUSE = (47.7508, 7.3359)
PLACES = {
    "Mulhouse": MULHOUSE,
    "Bâle": (47.5596, 7.5886),  # ~28 km
    "Colmar": (48.0794, 7.3585),  # ~37 km
    "Strasbourg": (48.5734, 7.7521),  # ~97 km
}


def offer(source_id, city, kinds=(Kind.JOB,), **kw):
    lat, lon = PLACES.get(city, (None, None))
    return Offer(
        source="test",
        source_id=source_id,
        url=f"https://example.org/{source_id}",
        title=f"Offre {source_id}",
        kinds=frozenset(kinds),
        location=Location(city=city, latitude=lat, longitude=lon),
        **kw,
    )


def test_upsert_counts_new_edited_and_unchanged(conn):
    first = [offer("a", "Mulhouse"), offer("b", "Colmar"), offer("b", "Colmar")]
    r = db.upsert_offers(conn, first)
    assert (r.inserted, r.updated, r.unchanged) == (2, 0, 0)  # duplicate "b" counted once

    edited = replace(first[0], title="Offre a (modifiée)")
    r = db.upsert_offers(conn, [edited, first[1]])
    assert (r.inserted, r.updated, r.unchanged) == (0, 1, 1)
    (title,) = conn.execute("SELECT title FROM offers WHERE source_id = 'a'").fetchone()
    assert title == "Offre a (modifiée)"


def test_edited_offer_loses_its_embedding(conn):
    o = offer("a", "Mulhouse")
    db.upsert_offers(conn, [o])
    conn.execute("UPDATE offers SET embedding = %s", (np.ones(1024, dtype=np.float32),))
    conn.commit()

    db.upsert_offers(conn, [o])  # unchanged: embedding kept
    assert conn.execute("SELECT embedding IS NOT NULL FROM offers").fetchone()[0]
    db.upsert_offers(conn, [replace(o, description="Nouvelle description")])
    assert conn.execute("SELECT embedding IS NULL FROM offers").fetchone()[0]


def test_radar_radius_and_kinds(conn):
    db.upsert_offers(
        conn,
        [
            offer("mul", "Mulhouse", (Kind.INTERNSHIP,)),
            offer("bale", "Bâle", (Kind.JOB, Kind.STUDENT_JOB)),
            offer("col", "Colmar", (Kind.INTERNSHIP, Kind.APPRENTICESHIP)),
            offer("stras", "Strasbourg", (Kind.INTERNSHIP,)),
            offer("nowhere", "Inconnu"),  # no coordinates: never on the radar
        ],
    )
    lat, lon = MULHOUSE

    within_30 = db.offers_within(conn, lat, lon, 30)
    assert [o.city for o in within_30] == ["Mulhouse", "Bâle"]
    assert within_30[0].distance_km < 0.1
    assert 25 < within_30[1].distance_km < 30

    within_50 = db.offers_within(conn, lat, lon, 50, kinds=[Kind.INTERNSHIP])
    assert [o.city for o in within_50] == ["Mulhouse", "Colmar"]

    wider = db.offers_within(conn, lat, lon, 100, kinds=[Kind.APPRENTICESHIP, Kind.STUDENT_JOB])
    assert [o.city for o in wider] == ["Bâle", "Colmar"]


def test_radar_uses_the_spatial_index(conn):
    conn.execute("SET enable_seqscan = off")
    plan = "\n".join(
        row[0]
        for row in conn.execute(
            "EXPLAIN SELECT id FROM offers WHERE ST_DWithin(location, "
            "ST_SetSRID(ST_MakePoint(7.33, 47.75), 4326)::geography, 30000)"
        )
    )
    assert "offers_location" in plan


def test_writes_are_visible_from_another_connection(conn):
    """A write must be committed, not left in an open transaction (seen by its own
    connection only, then lost when the program exits)."""
    from tests.conftest import TEST_DATABASE_URL

    db.upsert_offers(conn, [offer("a", "Mulhouse")])
    (offer_id,) = conn.execute("SELECT id FROM offers").fetchone()
    db.set_embeddings(conn, [(offer_id, np.ones(1024, dtype=np.float32))])
    with db.connect(TEST_DATABASE_URL) as other:
        row = other.execute("SELECT count(*), count(embedding) FROM offers").fetchone()
    assert row == (1, 1)


def test_withdrawn_offers_leave_the_radar(conn):
    db.upsert_offers(conn, [offer("a", "Mulhouse", weekly_hours=24.0), offer("b", "Mulhouse")])
    assert db.open_offer_ids(conn, "test") == ["a", "b"]
    assert db.close_offers(conn, "test", ["b", "unknown"]) == 1
    rows = db.offers_within(conn, *MULHOUSE, 10)
    assert [(r.title, r.weekly_hours) for r in rows] == [("Offre a", 24.0)]
    assert db.open_offer_ids(conn, "test") == ["a"]

    db.upsert_offers(conn, [offer("b", "Mulhouse")])  # published again
    assert len(db.offers_within(conn, *MULHOUSE, 10)) == 2
