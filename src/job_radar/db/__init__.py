"""PostgreSQL access: schema, loading offers, and the radar query."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from importlib.resources import files

import psycopg
from pgvector.psycopg import register_vector

from ..models import Kind, Offer


def connect(url: str) -> psycopg.Connection:
    conn = psycopg.connect(url)
    try:
        register_vector(conn)
    except psycopg.ProgrammingError:
        conn.rollback()  # extension not created yet: init_schema() registers it afterwards
    return conn


def init_schema(conn: psycopg.Connection) -> None:
    sql = files("job_radar.db").joinpath("schema.sql").read_text(encoding="utf-8")
    conn.execute(sql)
    conn.commit()
    register_vector(conn)


def content_hash(offer: Offer) -> str:
    """Fingerprint of what the student sees: a change means the offer was edited."""
    fields = [
        offer.url,
        offer.title,
        offer.company,
        offer.description,
        sorted(offer.kinds),
        offer.employment_types,
        offer.location.city,
        offer.location.postal_code,
        offer.location.latitude,
        offer.location.longitude,
        offer.remote,
        offer.valid_through.isoformat() if offer.valid_through else None,
    ]
    return hashlib.sha256(json.dumps(fields, ensure_ascii=False).encode()).hexdigest()


@dataclass
class LoadReport:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0


_UPSERT = """
INSERT INTO offers (source, source_id, url, title, company_name, description, kinds,
                    employment_types, city, postal_code, country, location, remote,
                    published_at, valid_through, content_hash)
VALUES (%(source)s, %(source_id)s, %(url)s, %(title)s, %(company)s, %(description)s,
        %(kinds)s, %(employment_types)s, %(city)s, %(postal_code)s, %(country)s,
        CASE WHEN %(lon)s::float8 IS NULL OR %(lat)s::float8 IS NULL THEN NULL
             ELSE ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography END,
        %(remote)s, %(published_at)s, %(valid_through)s, %(hash)s)
ON CONFLICT (source, source_id) DO UPDATE SET
    url = EXCLUDED.url, title = EXCLUDED.title, company_name = EXCLUDED.company_name,
    description = EXCLUDED.description, kinds = EXCLUDED.kinds,
    employment_types = EXCLUDED.employment_types, city = EXCLUDED.city,
    postal_code = EXCLUDED.postal_code, country = EXCLUDED.country,
    location = EXCLUDED.location, remote = EXCLUDED.remote,
    published_at = EXCLUDED.published_at, valid_through = EXCLUDED.valid_through,
    content_hash = EXCLUDED.content_hash,
    -- an edited offer loses its embedding: it will be recomputed from the new text
    embedding = CASE WHEN offers.content_hash = EXCLUDED.content_hash
                     THEN offers.embedding END,
    last_seen = now()
"""


def upsert_offers(conn: psycopg.Connection, offers: Iterable[Offer]) -> LoadReport:
    """Insert new offers, update edited ones, and mark every offer as seen now."""
    latest: dict[tuple[str, str], Offer] = {}
    for offer in offers:  # a feed can list the same offer twice: keep the last version
        latest[(offer.source, offer.source_id)] = offer
    if not latest:
        return LoadReport()
    sources, ids = zip(*latest, strict=True)
    known = {
        (source, source_id): h
        for source, source_id, h in conn.execute(
            """
            SELECT o.source, o.source_id, o.content_hash
            FROM offers o JOIN unnest(%s::text[], %s::text[]) AS k(source, source_id)
              USING (source, source_id)
            """,
            (list(sources), list(ids)),
        )
    }
    report = LoadReport()
    params = []
    for key, offer in latest.items():
        h = content_hash(offer)
        if key not in known:
            report.inserted += 1
        elif known[key] == h:
            report.unchanged += 1
        else:
            report.updated += 1
        params.append(
            {
                "source": offer.source,
                "source_id": offer.source_id,
                "url": offer.url,
                "title": offer.title,
                "company": offer.company,
                "description": offer.description,
                "kinds": sorted(k.value for k in offer.kinds),
                "employment_types": offer.employment_types,
                "city": offer.location.city,
                "postal_code": offer.location.postal_code,
                "country": offer.location.country,
                "lat": offer.location.latitude,
                "lon": offer.location.longitude,
                "remote": offer.remote,
                "published_at": offer.published_at,
                "valid_through": offer.valid_through,
                "hash": h,
            }
        )
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(_UPSERT, params)
    return report


@dataclass(frozen=True)
class RadarOffer:
    id: int
    title: str
    company: str
    url: str
    city: str
    kinds: list[str]
    distance_km: float
    published_at: object


def offers_within(
    conn: psycopg.Connection,
    latitude: float,
    longitude: float,
    radius_km: float,
    kinds: Sequence[Kind] | None = None,
    limit: int = 200,
) -> list[RadarOffer]:
    """The radar: offers placed within `radius_km` of a point, closest first.

    ST_DWithin on geography uses the GiST index and measures real distances in metres.
    """
    rows = conn.execute(
        """
        WITH here AS (SELECT ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography AS p)
        SELECT o.id, o.title, o.company_name, o.url, o.city, o.kinds,
               ST_Distance(o.location, here.p) / 1000 AS distance_km, o.published_at
        FROM offers o, here
        WHERE ST_DWithin(o.location, here.p, %(radius_m)s)
          AND (%(kinds)s::text[] IS NULL OR o.kinds && %(kinds)s::text[])
        ORDER BY distance_km, o.published_at DESC NULLS LAST
        LIMIT %(limit)s
        """,
        {
            "lat": latitude,
            "lon": longitude,
            "radius_m": radius_km * 1000,
            "kinds": [k.value for k in kinds] if kinds else None,
            "limit": limit,
        },
    ).fetchall()
    return [RadarOffer(*row) for row in rows]
