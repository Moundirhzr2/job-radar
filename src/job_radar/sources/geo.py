"""Town name or postal code -> coordinates, from two official services.

1. API Géo (geo.api.gouv.fr): the list of French communes with their INSEE code and centre.
2. If it cannot be reached, the IGN Géoplateforme geocoder (data.geopf.fr), which covers
   the same communes.
A lookup that fails on both is not an error for the caller: the offer is kept without a
position and placed on a later run.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from ..http import ApiClient, ApiError

GEO_API = "https://geo.api.gouv.fr"
IGN_API = "https://data.geopf.fr/geocodage"
_FIELDS = "nom,code,codesPostaux,centre,population"


class GeocodingUnavailable(Exception):
    """Neither geocoding service answered."""


@dataclass(frozen=True)
class Town:
    name: str
    insee_code: str
    postal_codes: tuple[str, ...]
    latitude: float
    longitude: float
    population: int


def _normalise(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


# "Paris 75002", "Strasbourg, France", "Remote, Lyon", "Mulhouse (68)" -> the town part.
_NOISE = re.compile(r"\b(remote|hybrid|télétravail|teletravail|france|cedex)\b|\(.*?\)|\d+", re.I)


def clean_city(raw: str) -> str:
    parts = [p for p in re.split(r"[,;/|]", raw) if _NOISE.sub("", p).strip(" -")]
    return _NOISE.sub("", parts[0]).strip(" -") if parts else ""


def _geo_api_towns(rows: list[dict]) -> list[Town]:
    towns = []
    for r in rows or []:
        centre = (r.get("centre") or {}).get("coordinates")
        if centre:
            lon, lat = centre
            towns.append(
                Town(
                    name=r.get("nom", ""),
                    insee_code=r.get("code", ""),
                    postal_codes=tuple(r.get("codesPostaux") or ()),
                    latitude=lat,
                    longitude=lon,
                    population=r.get("population") or 0,
                )
            )
    return towns


def _ign_towns(data: dict) -> list[Town]:
    towns = []
    for f in (data or {}).get("features") or []:
        p = f.get("properties") or {}
        coords = (f.get("geometry") or {}).get("coordinates")
        if coords and p.get("type") == "municipality":
            lon, lat = coords
            towns.append(
                Town(
                    name=p.get("city") or p.get("name") or "",
                    insee_code=p.get("citycode") or "",
                    postal_codes=(p["postcode"],) if p.get("postcode") else (),
                    latitude=lat,
                    longitude=lon,
                    population=p.get("population") or 0,
                )
            )
    return towns


@dataclass
class Geocoder:
    """Finds French towns; caches answers so a feed of 50 offers in "Paris" costs one call."""

    geo_api: ApiClient = field(default_factory=lambda: ApiClient(GEO_API, min_interval=0.1))
    ign_api: ApiClient = field(
        default_factory=lambda: ApiClient(IGN_API, min_interval=0.1, retries=2)
    )
    _cache: dict[tuple[str, str], Town | None] = field(default_factory=dict)

    def search(self, name: str, limit: int = 5) -> list[Town]:
        """Towns called `name`, most populated first (so "Mulhouse" is the city)."""
        params = {"nom": name, "fields": _FIELDS, "boost": "population", "limit": limit}
        try:
            return _geo_api_towns(self.geo_api.get("/communes", params))
        except ApiError:
            pass
        try:
            data = self.ign_api.get("/search", {"q": name, "type": "municipality", "limit": limit})
        except ApiError as exc:
            raise GeocodingUnavailable(name) from exc
        return sorted(_ign_towns(data), key=lambda t: -t.population)

    def locate(self, city: str, postal_code: str = "", country: str = "") -> Town | None:
        """The town an offer is in, or None if it is not a French town we can identify."""
        if country and country.upper() not in ("FR", "FRA", "FRANCE"):
            return None
        name = clean_city(city)
        postal_code = postal_code.strip() if re.fullmatch(r"\d{5}", postal_code.strip()) else ""
        if not name and not postal_code:
            return None
        key = (postal_code, _normalise(name))
        if key not in self._cache:  # failures raise and are not cached: retried next time
            self._cache[key] = self._lookup(name, postal_code)
        return self._cache[key]

    def _lookup(self, name: str, postal_code: str) -> Town | None:
        if postal_code:
            try:
                towns = _geo_api_towns(
                    self.geo_api.get("/communes", {"codePostal": postal_code, "fields": _FIELDS})
                )
            except ApiError:
                towns = self._ign(name or postal_code, postal_code)
            if len(towns) > 1 and name:  # several towns share a postal code: match the name
                named = [t for t in towns if _normalise(t.name) == _normalise(name)]
                towns = named or sorted(towns, key=lambda t: -t.population)
            if towns:
                return towns[0]
        if not name:
            return None
        towns = self.search(name, limit=1)
        return towns[0] if towns and _normalise(towns[0].name) == _normalise(name) else None

    def _ign(self, query: str, postal_code: str) -> list[Town]:
        params = {"q": query, "type": "municipality", "limit": 5}
        if postal_code:
            params["postcode"] = postal_code
        try:
            return _ign_towns(self.ign_api.get("/search", params))
        except ApiError as exc:
            raise GeocodingUnavailable(query) from exc


def find_towns(name: str, limit: int = 5) -> list[Town]:
    return Geocoder().search(name, limit)


@dataclass
class PlaceReport:
    placed: int = 0  # given the centre of their town
    unplaced: int = 0  # not a French town, or not found
    unavailable: int = 0  # geocoding services down: will be placed on a later run


def place(offers: list, geocoder: Geocoder) -> PlaceReport:
    """Give a town-centre position to offers that have none."""
    report = PlaceReport()
    for offer in offers:
        loc = offer.location
        if loc.has_point:
            loc.precision = loc.precision or "exact"
            continue
        try:
            town = geocoder.locate(loc.city, loc.postal_code, loc.country)
        except GeocodingUnavailable:
            report.unavailable += 1
            continue
        if town is None:
            report.unplaced += 1
            continue
        loc.latitude, loc.longitude, loc.precision = town.latitude, town.longitude, "town"
        loc.postal_code = loc.postal_code or (town.postal_codes[0] if town.postal_codes else "")
        report.placed += 1
    return report
