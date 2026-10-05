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
from ..sources.companies import Company
from ..sources.la_bonne_boite import HiringCompany


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
        offer.contact,
    ]
    return hashlib.sha256(json.dumps(fields, ensure_ascii=False).encode()).hexdigest()


@dataclass
class LoadReport:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0


_UPSERT = """
INSERT INTO offers (source, source_id, url, title, company_name, description, kinds,
                    employment_types, city, postal_code, country, location,
                    location_precision, remote, published_at, valid_through, contact,
                    content_hash)
VALUES (%(source)s, %(source_id)s, %(url)s, %(title)s, %(company)s, %(description)s,
        %(kinds)s, %(employment_types)s, %(city)s, %(postal_code)s, %(country)s,
        CASE WHEN %(lon)s::float8 IS NULL OR %(lat)s::float8 IS NULL THEN NULL
             ELSE ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography END,
        %(precision)s, %(remote)s, %(published_at)s, %(valid_through)s, %(contact)s,
        %(hash)s)
ON CONFLICT (source, source_id) DO UPDATE SET
    url = EXCLUDED.url, title = EXCLUDED.title, company_name = EXCLUDED.company_name,
    description = EXCLUDED.description, kinds = EXCLUDED.kinds,
    employment_types = EXCLUDED.employment_types, city = EXCLUDED.city,
    postal_code = EXCLUDED.postal_code, country = EXCLUDED.country,
    location = EXCLUDED.location, location_precision = EXCLUDED.location_precision,
    remote = EXCLUDED.remote,
    published_at = EXCLUDED.published_at, valid_through = EXCLUDED.valid_through,
    contact = EXCLUDED.contact,
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
                "precision": offer.location.precision if offer.location.has_point else "",
                "remote": offer.remote,
                "published_at": offer.published_at,
                "valid_through": offer.valid_through,
                "contact": offer.contact,
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
    precision: str
    contact: str


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
               ST_Distance(o.location, here.p) / 1000 AS distance_km, o.published_at,
               o.location_precision, o.contact
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


def directory_url(siren: str) -> str:
    """The company's public page in the official directory: the source we cite."""
    return f"https://annuaire-entreprises.data.gouv.fr/entreprise/{siren}"


def upsert_companies(conn: psycopg.Connection, companies: Iterable[Company]) -> int:
    """Save companies, their establishments and (small companies only) their officers."""
    count = 0
    with conn.transaction(), conn.cursor() as cur:
        for c in companies:
            if not c.siren:
                continue
            (company_id,) = cur.execute(
                """
                INSERT INTO companies (siren, name, naf_code, naf_section, headcount_range,
                                       category)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (siren) DO UPDATE SET
                    name = EXCLUDED.name, naf_code = EXCLUDED.naf_code,
                    naf_section = EXCLUDED.naf_section,
                    headcount_range = EXCLUDED.headcount_range,
                    category = EXCLUDED.category, updated_at = now()
                RETURNING id
                """,
                (c.siren, c.name, c.naf_code, c.naf_section, c.headcount_range, c.category),
            ).fetchone()
            cur.executemany(
                """
                INSERT INTO establishments (siret, company_id, address, postal_code, city,
                                            location, opened_on, is_head_office,
                                            headcount_range)
                VALUES (%(siret)s, %(company_id)s, %(address)s, %(postal_code)s, %(city)s,
                        CASE WHEN %(lon)s::float8 IS NULL OR %(lat)s::float8 IS NULL THEN NULL
                             ELSE ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography
                        END,
                        %(opened_on)s, %(is_head_office)s, %(headcount_range)s)
                ON CONFLICT (siret) DO UPDATE SET
                    address = EXCLUDED.address, postal_code = EXCLUDED.postal_code,
                    city = EXCLUDED.city, location = EXCLUDED.location,
                    opened_on = EXCLUDED.opened_on, is_head_office = EXCLUDED.is_head_office,
                    headcount_range = EXCLUDED.headcount_range
                """,
                [
                    {
                        "siret": e.siret,
                        "company_id": company_id,
                        "address": e.address,
                        "postal_code": e.postal_code,
                        "city": e.city,
                        "lat": e.latitude,
                        "lon": e.longitude,
                        "opened_on": e.opened_on,
                        "is_head_office": e.is_head_office,
                        "headcount_range": e.headcount_range,
                    }
                    for e in c.establishments
                    if e.siret
                ],
            )
            # Officers are replaced as a whole: someone who left the company must disappear.
            cur.execute(
                "DELETE FROM company_contacts WHERE company_id = %s AND kind = 'registry_officer'",
                (company_id,),
            )
            cur.executemany(
                """
                INSERT INTO company_contacts (company_id, kind, label, value, source_url)
                VALUES (%s, 'registry_officer', %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                [(company_id, o.role, o.name, directory_url(c.siren)) for o in c.officers],
            )
            count += 1
    return count


@dataclass(frozen=True)
class RadarCompany:
    id: int
    siren: str
    name: str
    naf_code: str
    headcount_range: str
    city: str
    distance_km: float
    officers: list[str]


def companies_within(
    conn: psycopg.Connection,
    latitude: float,
    longitude: float,
    radius_km: float,
    naf_prefixes: Sequence[str] | None = None,
    limit: int = 200,
) -> list[RadarCompany]:
    """Companies with an establishment within the radius, by their closest establishment."""
    rows = conn.execute(
        """
        WITH here AS (SELECT ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography AS p),
        nearest AS (
            SELECT DISTINCT ON (e.company_id) e.company_id, e.city,
                   ST_Distance(e.location, here.p) / 1000 AS distance_km
            FROM establishments e, here
            WHERE ST_DWithin(e.location, here.p, %(radius_m)s)
            ORDER BY e.company_id, ST_Distance(e.location, here.p)
        )
        SELECT c.id, c.siren, c.name, coalesce(c.naf_code, ''),
               coalesce(c.headcount_range, 'NN'), n.city, n.distance_km,
               coalesce(array_agg(k.value || ' (' || k.label || ')' ORDER BY k.id)
                        FILTER (WHERE k.id IS NOT NULL), '{}')
        FROM nearest n
        JOIN companies c ON c.id = n.company_id
        LEFT JOIN company_contacts k ON k.company_id = c.id AND k.kind = 'registry_officer'
        WHERE %(prefixes)s::text[] IS NULL
           OR c.naf_code LIKE ANY (SELECT p || '%%' FROM unnest(%(prefixes)s::text[]) p)
        GROUP BY c.id, n.city, n.distance_km
        ORDER BY n.distance_km
        LIMIT %(limit)s
        """,
        {
            "lat": latitude,
            "lon": longitude,
            "radius_m": radius_km * 1000,
            "prefixes": list(naf_prefixes) if naf_prefixes else None,
            "limit": limit,
        },
    ).fetchall()
    return [RadarCompany(*row) for row in rows]


def unplaced_offers(conn: psycopg.Connection, limit: int = 1000) -> list[tuple]:
    """Offers still without a position (geocoding was down or the town was unknown)."""
    return conn.execute(
        "SELECT id, city, postal_code, country FROM offers WHERE location IS NULL "
        "AND (city <> '' OR postal_code <> '') ORDER BY id LIMIT %s",
        (limit,),
    ).fetchall()


def set_location(conn: psycopg.Connection, offer_id: int, lat: float, lon: float) -> None:
    conn.execute(
        "UPDATE offers SET location = ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, "
        "location_precision = 'town' WHERE id = %s",
        (lon, lat, offer_id),
    )


def upsert_hiring(conn: psycopg.Connection, items: Iterable[HiringCompany]) -> int:
    """Save La Bonne Boîte results: the company, its establishment and the hiring potential.

    Companies already known from the directory keep their directory data; new ones are
    created with what La Bonne Boîte gives (name, activity).
    """
    count = 0
    with conn.transaction(), conn.cursor() as cur:
        for h in items:
            if not h.siret:
                continue
            (company_id,) = cur.execute(
                """
                INSERT INTO companies (siren, name, naf_code) VALUES (%s, %s, %s)
                ON CONFLICT (siren) DO UPDATE SET updated_at = now()
                RETURNING id
                """,
                (h.siren, h.company_name, h.naf_code),
            ).fetchone()
            cur.execute(
                """
                INSERT INTO establishments (siret, company_id, postal_code, city, location)
                VALUES (%(siret)s, %(company_id)s, %(postal_code)s, %(city)s,
                        CASE WHEN %(lon)s::float8 IS NULL OR %(lat)s::float8 IS NULL THEN NULL
                             ELSE ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography
                        END)
                ON CONFLICT (siret) DO UPDATE SET
                    location = coalesce(establishments.location, EXCLUDED.location),
                    city = coalesce(nullif(establishments.city, ''), EXCLUDED.city),
                    postal_code = coalesce(nullif(establishments.postal_code, ''),
                                           EXCLUDED.postal_code)
                """,
                {
                    "siret": h.siret,
                    "company_id": company_id,
                    "postal_code": h.postal_code,
                    "city": h.city,
                    "lat": h.latitude,
                    "lon": h.longitude,
                },
            )
            cur.execute(
                """
                INSERT INTO hiring_potential (siret, rome, score, is_high_potential, accepts_email)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (siret, rome) DO UPDATE SET
                    score = EXCLUDED.score, is_high_potential = EXCLUDED.is_high_potential,
                    accepts_email = EXCLUDED.accepts_email, updated_at = now()
                """,
                (h.siret, h.rome, h.hiring_potential, h.is_high_potential, h.accepts_email),
            )
            count += 1
    return count


@dataclass(frozen=True)
class LikelyEmployer:
    siret: str
    name: str
    naf_code: str
    city: str
    distance_km: float
    rome: str
    score: float
    is_high_potential: bool
    accepts_email: bool


def likely_employers(
    conn: psycopg.Connection,
    latitude: float,
    longitude: float,
    radius_km: float,
    romes: Sequence[str] | None = None,
    limit: int = 100,
) -> list[LikelyEmployer]:
    """Establishments in the radius ranked by hiring potential (best first)."""
    rows = conn.execute(
        """
        WITH here AS (SELECT ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography AS p)
        SELECT e.siret, c.name, coalesce(c.naf_code, ''), coalesce(e.city, ''),
               ST_Distance(e.location, here.p) / 1000, h.rome, h.score, h.is_high_potential,
               h.accepts_email
        FROM hiring_potential h
        JOIN establishments e ON e.siret = h.siret
        JOIN companies c ON c.id = e.company_id, here
        WHERE ST_DWithin(e.location, here.p, %(radius_m)s)
          AND (%(romes)s::text[] IS NULL OR h.rome = ANY(%(romes)s::text[]))
        ORDER BY h.score DESC
        LIMIT %(limit)s
        """,
        {
            "lat": latitude,
            "lon": longitude,
            "radius_m": radius_km * 1000,
            "romes": list(romes) if romes else None,
            "limit": limit,
        },
    ).fetchall()
    return [LikelyEmployer(*row) for row in rows]
