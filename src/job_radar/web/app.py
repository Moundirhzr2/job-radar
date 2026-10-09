"""FastAPI application: JSON API under /api, the page at /."""

import os
import threading
from collections import Counter, OrderedDict, defaultdict
from collections.abc import Callable, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Literal

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pgvector.psycopg import register_vector
from psycopg_pool import ConnectionPool
from pydantic import BaseModel

from .. import db
from ..models import Kind
from ..sources.geo import Geocoder, GeocodingUnavailable

STATIC = Path(__file__).parent / "static"
DEFAULT_DATABASE_URL = "postgresql://radar:radar@localhost:5433/radar"
Status = Literal["to_apply", "sent", "followed_up", "interview", "offer", "rejected", "dropped"]


def connection(request: Request) -> Iterator[psycopg.Connection]:
    with request.app.state.pool.connection() as conn:
        yield conn


Conn = Annotated[psycopg.Connection, Depends(connection)]
Lat = Annotated[float, Query(ge=-90, le=90)]
Lon = Annotated[float, Query(ge=-180, le=180)]
Radius = Annotated[float, Query(gt=0, le=200)]
Kinds = Annotated[list[Kind] | None, Query()]
Lang = Literal["fr", "en"]


def _lazy(factory: Callable[[], object]) -> Callable[[], object]:
    """Build a heavy object (a model) once, on first use, whichever request asks first."""
    lock, box = threading.Lock(), []

    def get():
        with lock:
            if not box:
                box.append(factory())
            return box[0]

    return get


def _embedder():
    from ..embed import Embedder

    return Embedder()


def _reranker():
    from ..rerank import Reranker

    return Reranker()


def _rewrite(request: str, profile: str):
    from ..rewrite import rewrite

    return rewrite(request, profile)


def _analyse(offer: str, profile: str, lang: str = "fr"):
    from ..fit import analyse

    return analyse(offer, profile, lang=lang)


def _draft(offer: str, contact: str, profile: str):
    from ..draft import write

    return write(offer, contact, profile)


def _allows_ai_input() -> Callable[[str], bool]:
    from ..sources.careers.fetch import PoliteFetcher

    fetcher = PoliteFetcher()  # keeps each site's robots.txt and Content-Signal once read
    return fetcher.allows_ai_input


@dataclass
class Services:
    """What the app needs from the outside world; tests replace each piece."""

    database_url: str = field(
        default_factory=lambda: os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    )
    profile_path: Path | None = field(
        default_factory=lambda: Path(os.environ.get("RADAR_PROFILE", "data/profile.md"))
    )
    geocoder: Geocoder = field(default_factory=Geocoder)
    embedder: Callable[[], object] = field(default_factory=lambda: _lazy(_embedder))
    reranker: Callable[[], object] = field(default_factory=lambda: _lazy(_reranker))
    rewrite: Callable[[str, str], object] | None = _rewrite
    analyse: Callable[..., object] = _analyse  # (offer, profile, lang="fr")
    draft: Callable[[str, str, str], object] = _draft
    allows_ai_input: Callable[[str], bool] = field(default_factory=_allows_ai_input)

    def profile(self) -> str:
        if self.profile_path and self.profile_path.exists():
            return self.profile_path.read_text(encoding="utf-8")
        return ""


def fail(status: int, code: str, message: str) -> HTTPException:
    """An error the page can name in its own language: a stable code, and a French sentence for
    whoever reads the API directly."""
    return HTTPException(status, {"code": code, "message": message})


# --- request bodies --------------------------------------------------------------------------


class ApplicationIn(BaseModel):
    offer_id: int | None = None
    siret: str | None = None
    company: str = ""
    title: str = ""
    url: str = ""
    status: Status = "to_apply"
    channel: str = ""
    contact: str = ""
    applied_on: date | None = None
    follow_up_on: date | None = None
    notes: str = ""


class ApplicationPatch(BaseModel):
    company: str | None = None
    title: str | None = None
    url: str | None = None
    status: Status | None = None
    channel: str | None = None
    contact: str | None = None
    applied_on: date | None = None
    follow_up_on: date | None = None
    notes: str | None = None


# --- JSON shapes -----------------------------------------------------------------------------


def _offer_json(o: db.RadarOffer) -> dict:
    return {
        "id": o.id,
        "source": o.source,
        "title": o.title,
        "company": o.company,
        "city": o.city,
        "kinds": o.kinds,
        "distance_km": round(o.distance_km, 1),
        "weekly_hours": o.weekly_hours,
        "precision": o.precision,
        "lat": o.latitude,
        "lon": o.longitude,
        "published_at": o.published_at.isoformat() if o.published_at else None,
        "url": o.url,
    }


def _places(offers: list[db.RadarOffer]) -> list[dict]:
    """Offers grouped by point: many are placed at the centre of their town."""
    groups: dict[tuple[float, float], list[db.RadarOffer]] = defaultdict(list)
    for o in offers:
        if o.latitude is not None and o.longitude is not None:
            groups[(round(o.latitude, 4), round(o.longitude, 4))].append(o)
    places = []
    for (lat, lon), items in groups.items():
        kinds = Counter(k for o in items for k in o.kinds)
        places.append(
            {
                "lat": lat,
                "lon": lon,
                "city": items[0].city,
                "count": len(items),
                "town_centre": all(o.precision == "town" for o in items),
                "kinds": dict(kinds),
                "ids": [o.id for o in items],
            }
        )
    return sorted(places, key=lambda p: -p["count"])


def _application_json(a: db.Application) -> dict:
    today = date.today()
    due = a.status in ("sent", "followed_up") and a.follow_up_on is not None
    return {
        "id": a.id,
        "offer_id": a.offer_id,
        "siret": a.siret,
        "company": a.company,
        "title": a.title,
        "url": a.url,
        "status": a.status,
        "channel": a.channel,
        "contact": a.contact,
        "applied_on": a.applied_on.isoformat() if a.applied_on else None,
        "follow_up_on": a.follow_up_on.isoformat() if a.follow_up_on else None,
        "follow_up_due": due and a.follow_up_on <= today,
        "notes": a.notes,
        "offer_open": a.offer_open,
        "updated_at": a.updated_at.isoformat(),
    }


def _stats(applications: list[db.Application]) -> dict:
    counts = Counter(a.status for a in applications)
    sent = sum(counts[s] for s in ("sent", "followed_up", "interview", "offer", "rejected"))
    answered = sum(counts[s] for s in ("interview", "offer", "rejected"))
    today = date.today()
    due = sum(
        1
        for a in applications
        if a.status in ("sent", "followed_up") and a.follow_up_on and a.follow_up_on <= today
    )
    return {
        "by_status": {s: counts[s] for s in db.STATUSES},
        "sent": sent,
        "answered": answered,
        "response_rate": round(answered / sent, 2) if sent else None,
        "follow_ups_due": due,
    }


def _requirement_json(r) -> dict:
    return r.model_dump() | {
        "evidence_found": r.evidence_found,
        "profile_evidence_found": r.profile_evidence_found,
        "note": r.note,
    }


# --- the app ---------------------------------------------------------------------------------


def create_app(services: Services | None = None) -> FastAPI:
    services = services or Services()
    with db.connect(services.database_url) as conn:
        db.init_schema(conn)
    pool = ConnectionPool(
        services.database_url,
        kwargs={"autocommit": True},
        configure=register_vector,
        min_size=1,
        max_size=4,
        open=True,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        pool.close()

    app = FastAPI(
        title="Job Radar",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.state.services = services
    app.state.pool = pool

    rewrites: OrderedDict[tuple[str, str], object] = OrderedDict()
    rewrites_lock = threading.Lock()

    def rewrite_once(request_text: str, profile: str):
        """Claude reads a given request once: changing the radius or the contract kinds later
        searches again without paying for the same rewrite (kept for the last 64 requests)."""
        key = (" ".join(request_text.casefold().split()), sha256(profile.encode()).hexdigest())
        with rewrites_lock:
            if key in rewrites:
                rewrites.move_to_end(key)
                return rewrites[key]
        result = services.rewrite(request_text, profile)  # failures are not kept
        with rewrites_lock:
            rewrites[key] = result
            while len(rewrites) > 64:
                rewrites.popitem(last=False)
        return result

    def stored_offer(conn: psycopg.Connection, offer_id: int) -> db.StoredOffer:
        offer = db.get_offer(conn, str(offer_id))
        if offer is None:
            raise fail(404, "offer_not_found", "Offre introuvable.")
        return offer

    def profile_for_claude(offer: db.StoredOffer) -> str:
        profile = services.profile()
        if not profile.strip():
            raise fail(
                409,
                "profile_missing",
                "Ton profil manque : mets ton CV en texte dans data/profile.md.",
            )
        if offer.source != "france_travail" and not services.allows_ai_input(offer.url):
            raise fail(
                403, "ai_input_refused", "Ce site refuse que ses pages servent d'entrée à une IA."
            )
        return profile

    @app.get("/api/towns")
    def towns(q: Annotated[str, Query(min_length=2, max_length=80)]):
        try:
            found = services.geocoder.search(q, limit=6)
        except GeocodingUnavailable:
            raise fail(
                503, "geocoding_unavailable", "La géolocalisation ne répond pas, réessaie."
            ) from None
        return [
            {
                "name": t.name,
                "insee": t.insee_code,
                "postal_codes": list(t.postal_codes),
                "lat": t.latitude,
                "lon": t.longitude,
            }
            for t in found
        ]

    @app.get("/api/coverage")
    def coverage(conn: Conn):
        """Towns with the most open offers, to start from when no town is chosen yet."""
        return [
            {"city": city, "count": count, "lat": lat, "lon": lon}
            for city, count, lat, lon in db.offer_towns(conn)
        ]

    @app.get("/api/radar")
    def radar(conn: Conn, lat: Lat, lon: Lon, radius_km: Radius = 20, kind: Kinds = None):
        offers = db.offers_within(conn, lat, lon, radius_km, kind, limit=5000)
        return {
            "count": len(offers),
            "offers": [_offer_json(o) for o in offers],
            "places": _places(offers),
        }

    @app.get("/api/employers")
    def employers(conn: Conn, lat: Lat, lon: Lon, radius_km: Radius = 20):
        hiring = db.likely_employers(conn, lat, lon, radius_km, limit=200)
        digital = db.companies_within(conn, lat, lon, min(radius_km, 50), ["62", "63"], limit=500)
        return {
            "hiring": [
                {
                    "siret": e.siret,
                    "name": e.name,
                    "naf_code": e.naf_code,
                    "city": e.city,
                    "distance_km": round(e.distance_km, 1),
                    "rome": e.rome,
                    "score": round(e.score, 1),
                    "high_potential": e.is_high_potential,
                    "accepts_email": e.accepts_email,
                    "lat": e.latitude,
                    "lon": e.longitude,
                    "fiche": f"https://annuaire-entreprises.data.gouv.fr/etablissement/{e.siret}",
                }
                for e in hiring
            ],
            "digital": [
                {
                    "siren": c.siren,
                    "name": c.name,
                    "naf_code": c.naf_code,
                    "city": c.city,
                    "distance_km": round(c.distance_km, 1),
                    "officers": c.officers,
                    "lat": c.latitude,
                    "lon": c.longitude,
                    "fiche": db.directory_url(c.siren),
                }
                for c in digital
            ],
        }

    @app.get("/api/search")
    def search(
        conn: Conn,
        q: Annotated[str, Query(min_length=2, max_length=300)],
        lat: Lat,
        lon: Lon,
        radius_km: Radius = 30,
        kind: Kinds = None,
        rewrite: bool = True,
        rerank: bool = True,
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
    ):
        from ..pipeline import CONFIDENCE, find

        profile = services.profile()
        notice = None
        rewriter = services.rewrite if rewrite and services.rewrite else None
        if rewriter:
            try:
                rewritten = rewrite_once(q, profile)
                rewriter = lambda request, prof: rewritten  # noqa: E731  (computed once)
            except Exception as exc:  # no key, no credit, no network: search without Claude
                rewriter = None
                notice = {"code": "rewrite_unavailable", "error": type(exc).__name__}
        results = find(
            conn,
            q,
            lat,
            lon,
            radius_km,
            services.embedder(),
            kinds=kind or (),
            profile=profile,
            rewriter=rewriter,
            reranker=services.reranker() if rerank else None,
            limit=limit,
        )
        return {
            "query": {
                "text": results.query.text,
                "keywords": list(results.query.keywords),
                "kinds": [k.value for k in results.query.kinds],
                "rewritten": results.rewritten is not None,
            },
            "confident": results.confident,
            "best_score": results.best_score,
            "threshold": CONFIDENCE,
            "notice": notice,
            "results": [
                {
                    "id": c.id,
                    "source": c.source,
                    "title": c.title,
                    "company": c.company,
                    "city": c.city,
                    "kinds": c.kinds,
                    "distance_km": round(c.distance_km, 1),
                    "weekly_hours": c.weekly_hours,
                    "lat": c.latitude,
                    "lon": c.longitude,
                    "url": c.url,
                    "score": c.extra.get("rerank"),
                    "weak": "rerank" in c.extra and c.extra["rerank"] < CONFIDENCE,
                }
                for c in results.candidates
            ],
        }

    @app.get("/api/offers/{offer_id}")
    def offer(conn: Conn, offer_id: int):
        o = stored_offer(conn, offer_id)
        tracked = conn.execute(
            "SELECT id FROM applications WHERE offer_id = %s ORDER BY id LIMIT 1", (offer_id,)
        ).fetchone()
        return {
            "id": o.id,
            "source": o.source,
            "title": o.title,
            "company": o.company,
            "city": o.city,
            "kinds": o.kinds,
            "weekly_hours": o.weekly_hours,
            "description": o.description,
            "url": o.url,
            "contact": o.contact,
            "closed": o.closed,
            "application_id": tracked[0] if tracked else None,
        }

    @app.post("/api/offers/{offer_id}/fit")
    def fit(conn: Conn, offer_id: int, again: bool = False, lang: Lang = "fr"):
        from ..fit import fit_offer

        o = stored_offer(conn, offer_id)
        profile = profile_for_claude(o)
        try:
            result, cached = fit_offer(conn, o, profile, services.analyse, again=again, lang=lang)
        except Exception as exc:
            raise fail(502, "fit_failed", f"Analyse impossible ({type(exc).__name__}).") from exc
        return {
            "summary": result.summary,
            "requirements": [_requirement_json(r) for r in result.requirements],
            "projects": [p.model_dump() for p in result.projects],
            "cached": cached,
        }

    @app.post("/api/offers/{offer_id}/draft")
    def draft(conn: Conn, offer_id: int):
        from ..fit import offer_text

        o = stored_offer(conn, offer_id)
        if o.closed:
            raise fail(410, "offer_closed", "Cette offre n'est plus en ligne.")
        profile = profile_for_claude(o)
        text = offer_text(o.title, o.company, o.city, o.kinds, o.weekly_hours, o.description)
        try:
            result = services.draft(text, o.contact, profile)
        except Exception as exc:
            raise fail(
                502, "draft_failed", f"Brouillon impossible ({type(exc).__name__})."
            ) from exc
        return {
            "subject": result.subject,
            "body": result.body,
            "facts": [f.model_dump() | {"found": f.found} for f in result.facts],
            "contact": o.contact,
            "url": o.url,
        }

    @app.get("/api/applications")
    def applications(conn: Conn):
        items = db.list_applications(conn)
        return {"applications": [_application_json(a) for a in items], "stats": _stats(items)}

    @app.post("/api/applications", status_code=201)
    def add_application(conn: Conn, body: ApplicationIn):
        values = body.model_dump(exclude_unset=True)
        if not values.get("offer_id") and not (values.get("company") or "").strip():
            raise fail(422, "company_required", "Une offre, ou au moins le nom de l'entreprise.")
        try:
            return _application_json(db.add_application(conn, **values))
        except ValueError as exc:
            raise fail(422, "invalid_application", str(exc)) from None

    @app.patch("/api/applications/{application_id}")
    def update_application(conn: Conn, application_id: int, body: ApplicationPatch):
        updated = db.update_application(conn, application_id, **body.model_dump(exclude_unset=True))
        if updated is None:
            raise fail(404, "application_not_found", "Candidature introuvable.")
        return _application_json(updated)

    @app.delete("/api/applications/{application_id}", status_code=204)
    def delete_application(conn: Conn, application_id: int):
        if not db.delete_application(conn, application_id):
            raise fail(404, "application_not_found", "Candidature introuvable.")
        return Response(status_code=204)

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
