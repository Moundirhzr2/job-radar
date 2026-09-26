"""Town name -> coordinates, from the official API Géo (communes, INSEE codes)."""

from __future__ import annotations

from dataclasses import dataclass

from ..http import ApiClient

API = "https://geo.api.gouv.fr"


@dataclass(frozen=True)
class Town:
    name: str
    insee_code: str
    postal_codes: tuple[str, ...]
    latitude: float
    longitude: float
    population: int


def find_towns(name: str, api: ApiClient | None = None, limit: int = 5) -> list[Town]:
    """Towns matching `name`, most populated first (so "Mulhouse" is the city, not a hamlet)."""
    api = api or ApiClient(API, min_interval=0.1)
    rows = api.get(
        "/communes",
        {
            "nom": name,
            "fields": "nom,code,codesPostaux,centre,population",
            "boost": "population",
            "limit": limit,
        },
    )
    towns = []
    for r in rows:
        centre = (r.get("centre") or {}).get("coordinates")
        if not centre:
            continue
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
