"""Offers from France Travail (API Offres d'emploi v2), around a commune.

Access: a free francetravail.io application, OAuth2 client credentials. The keys are read from
FRANCE_TRAVAIL_CLIENT_ID and FRANCE_TRAVAIL_CLIENT_SECRET, never stored in the repository.

The search takes an INSEE commune code and a distance in km, and returns offers in pages of at
most 150 (HTTP 206 with a Content-Range header), up to 3,150 results per query.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime

import httpx

from ..http import ApiClient, ApiError, default_client
from ..models import Kind, Location, Offer, classify

TOKEN_URL = "https://entreprise.francetravail.fr/connexion/oauth2/access_token"
API = "https://api.francetravail.io/partenaire/offresdemploi/v2"
OFFERS_SCOPE = "api_offresdemploiv2 o2dsoffre"
PAGE = 150
MAX_RESULTS = 3150  # the API refuses ranges beyond index 3149

# Query filters per kind of offer. Internships are almost never published on France Travail
# (an internship agreement is not an employment contract), so there is no filter for them.
KIND_FILTERS: dict[Kind, dict[str, str]] = {
    Kind.APPRENTICESHIP: {"natureContrat": "E2,FS"},  # apprentissage, professionnalisation
    Kind.STUDENT_JOB: {"tempsPlein": "false"},  # part-time
    Kind.JOB: {"typeContrat": "CDI,CDD,MIS,SAI"},
}


class CredentialsError(Exception):
    pass


def read_credentials() -> tuple[str, str]:
    client_id = os.environ.get("FRANCE_TRAVAIL_CLIENT_ID", "").strip()
    secret = os.environ.get("FRANCE_TRAVAIL_CLIENT_SECRET", "").strip()
    if not client_id or not secret:
        raise CredentialsError(
            "Clés France Travail absentes : définir FRANCE_TRAVAIL_CLIENT_ID et "
            "FRANCE_TRAVAIL_CLIENT_SECRET dans les variables d'environnement."
        )
    for name, value in (
        ("FRANCE_TRAVAIL_CLIENT_ID", client_id),
        ("FRANCE_TRAVAIL_CLIENT_SECRET", secret),
    ):
        if re.search(r"[\s\"'«»]", value):
            raise CredentialsError(
                f"{name} contient des espaces ou des guillemets : il faut coller uniquement la "
                "valeur affichée sur francetravail.io, sans libellé ni guillemets."
            )
    return client_id, secret


@dataclass
class TokenProvider:
    """Client-credentials access token, renewed a minute before it expires."""

    scope: str = OFFERS_SCOPE
    client: httpx.Client = field(default_factory=default_client)
    clock: Callable[[], float] = time.monotonic
    _token: str = ""
    _expires: float = 0.0

    def __call__(self) -> str:
        if not self._token or self.clock() > self._expires - 60:
            client_id, secret = read_credentials()
            resp = self.client.post(
                TOKEN_URL,
                params={"realm": "/partenaire"},
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": secret,
                    "scope": self.scope,
                },
            )
            if resp.status_code != 200:
                raise CredentialsError(
                    f"France Travail refuse les clés (HTTP {resp.status_code}) : "
                    f"{resp.json().get('error_description', resp.text[:100])}"
                )
            body = resp.json()
            self._token = body["access_token"]
            self._expires = self.clock() + float(body.get("expires_in", 1499))
        return self._token


def _date(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None
    except ValueError:
        return None


def _city(libelle: str) -> str:
    """ "68 - ILLZACH" -> "ILLZACH"."""
    return re.sub(r"^\s*\d{2,3}[AB]?\s*-\s*", "", libelle or "").strip()


def _contact(raw: dict | None) -> str:
    """The contact the employer published in the offer, as published (often a link)."""
    if not raw:
        return ""
    parts = [
        (raw.get(k) or "").strip() for k in ("nom", "coordonnees1", "courriel", "urlPostulation")
    ]
    return " | ".join(dict.fromkeys(p for p in parts if p))


def to_offer(o: dict) -> Offer:
    title = (o.get("intitule") or "").strip()
    types = [t for t in (o.get("typeContratLibelle"), o.get("natureContrat")) if t]
    part_time = (o.get("dureeTravailLibelleConverti") or "").lower() == "temps partiel"
    kinds = set(classify(title, types, part_time=part_time))
    nature = (o.get("natureContrat") or "").lower()
    if o.get("alternance") or "apprentissage" in nature or "professionnalisation" in nature:
        kinds -= {Kind.JOB, Kind.STUDENT_JOB}
        kinds.add(Kind.APPRENTICESHIP)
    place = o.get("lieuTravail") or {}
    lat, lon = place.get("latitude"), place.get("longitude")
    return Offer(
        source="france_travail",
        source_id=o["id"],
        url=(o.get("origineOffre") or {}).get("urlOrigine")
        or f"https://candidat.francetravail.fr/offres/recherche/detail/{o['id']}",
        title=title,
        company=((o.get("entreprise") or {}).get("nom") or "").strip(),
        description=(o.get("description") or "").strip(),
        kinds=frozenset(kinds),
        employment_types=types,
        location=Location(
            city=_city(place.get("libelle", "")),
            postal_code=place.get("codePostal") or "",
            country="FR",
            latitude=lat,
            longitude=lon,
            precision="exact" if lat is not None and lon is not None else "",
        ),
        published_at=_date(o.get("dateCreation")),
        contact=_contact(o.get("contact")),
    )


@dataclass
class FranceTravail:
    tokens: TokenProvider = field(default_factory=TokenProvider)
    api: ApiClient = field(default_factory=lambda: ApiClient(API, min_interval=0.1))

    def search(
        self,
        insee_code: str,
        distance_km: float,
        kind: Kind | None = None,
        keywords: str = "",
        max_results: int = 1000,
    ) -> Iterator[Offer]:
        params: dict[str, str | int] = {"commune": insee_code, "distance": round(distance_km)}
        if kind is not None:
            if kind not in KIND_FILTERS:
                return  # no internships on France Travail
            params.update(KIND_FILTERS[kind])
        if keywords:
            params["motsCles"] = keywords
        limit = min(max_results, MAX_RESULTS)
        for start in range(0, limit, PAGE):
            end = min(start + PAGE, limit) - 1
            data = self.api.get(
                "/offres/search",
                {**params, "range": f"{start}-{end}"},
                headers={"Authorization": f"Bearer {self.tokens()}", "Accept": "application/json"},
            )
            results = (data or {}).get("resultats") or []
            for raw in results:
                try:
                    yield to_offer(raw)
                except (KeyError, TypeError):  # a malformed offer must not stop the search
                    continue
            if len(results) < end - start + 1:
                return


__all__ = ["ApiError", "CredentialsError", "FranceTravail", "KIND_FILTERS", "to_offer"]
