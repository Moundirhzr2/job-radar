"""Companies around a point, from the official company directory (API Recherche d'entreprises).

This is the radar's second layer: businesses near the student, including those that never
publish offers. Data comes from the public Sirene and company registers (INSEE, RNE).

Data minimisation: the directory also lists each company's officers with their month of
birth. We keep only name and role, and only for small companies (where the manager is the
person who hires); birth dates are never stored.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ..http import ApiClient

API = "https://recherche-entreprises.api.gouv.fr"
MAX_RADIUS_KM = 50  # limit of the API's /near_point
PER_PAGE = 25

# INSEE headcount brackets ("tranche d'effectif salarié"), as lower bounds.
HEADCOUNT_MIN = {
    "00": 0, "01": 1, "02": 3, "03": 6, "11": 10, "12": 20, "21": 50, "22": 100,
    "31": 200, "32": 250, "41": 500, "42": 1000, "51": 2000, "52": 5000, "53": 10000,
}  # fmt: skip
HEADCOUNT_LABEL = {
    "00": "0 salarié", "01": "1 à 2", "02": "3 à 5", "03": "6 à 9", "11": "10 à 19",
    "12": "20 à 49", "21": "50 à 99", "22": "100 à 199", "31": "200 à 249",
    "32": "250 à 499", "41": "500 à 999", "42": "1 000 à 1 999", "51": "2 000 à 4 999",
    "52": "5 000 à 9 999", "53": "10 000 et plus",
}  # fmt: skip
SMALL_COMPANY_MAX = 49  # officers are shown for companies under 50 employees


@dataclass
class Officer:
    name: str
    role: str


@dataclass
class Establishment:
    siret: str
    address: str
    postal_code: str
    city: str
    latitude: float | None
    longitude: float | None
    opened_on: date | None
    is_head_office: bool
    headcount_range: str  # INSEE code, "NN" when unknown


@dataclass
class Company:
    siren: str
    name: str
    naf_code: str
    naf_section: str
    headcount_range: str
    category: str  # PME, ETI, GE
    establishments: list[Establishment] = field(default_factory=list)
    officers: list[Officer] = field(default_factory=list)

    @property
    def headcount_label(self) -> str:
        return HEADCOUNT_LABEL.get(self.headcount_range, "effectif inconnu")

    @property
    def is_small(self) -> bool:
        low = HEADCOUNT_MIN.get(self.headcount_range)
        return low is not None and low <= SMALL_COMPANY_MAX


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _establishment(e: dict) -> Establishment:
    return Establishment(
        siret=e.get("siret") or "",
        address=e.get("adresse") or "",
        postal_code=e.get("code_postal") or "",
        city=e.get("libelle_commune") or "",
        latitude=_float(e.get("latitude")),
        longitude=_float(e.get("longitude")),
        opened_on=_date(e.get("date_creation")),
        is_head_office=bool(e.get("est_siege")),
        headcount_range=e.get("tranche_effectif_salarie") or "NN",
    )


def _officers(raw: list[dict]) -> list[Officer]:
    officers = []
    for d in raw or []:
        if d.get("type_dirigeant") == "personne physique":
            name = " ".join(p for p in (d.get("prenoms"), d.get("nom")) if p).title()
        else:
            name = d.get("denomination") or ""
        role = d.get("qualite") or ""
        # Auditors are not people who hire.
        if name and "commissaire aux comptes" not in role.lower():
            officers.append(Officer(name=name, role=role))
    return officers


def parse_company(r: dict) -> Company:
    company = Company(
        siren=r.get("siren") or "",
        name=r.get("nom_complet") or r.get("nom_raison_sociale") or "",
        naf_code=r.get("activite_principale") or "",
        naf_section=r.get("section_activite_principale") or "",
        headcount_range=r.get("tranche_effectif_salarie") or "NN",
        category=r.get("categorie_entreprise") or "",
        establishments=[_establishment(e) for e in r.get("matching_etablissements") or []],
    )
    if company.is_small:
        company.officers = _officers(r.get("dirigeants") or [])
    return company


@dataclass
class CompanyDirectory:
    """Client for the directory's /near_point search."""

    api: ApiClient = field(default_factory=lambda: ApiClient(API, min_interval=0.2))

    def near(
        self,
        latitude: float,
        longitude: float,
        radius_km: float,
        naf_sections: str | None = None,
        max_results: int = 500,
    ) -> Iterator[Company]:
        """Companies with an establishment within `radius_km`, page by page.

        Only employers (companies that declare staff) are kept: they are the ones that can
        take an intern, an apprentice or a student.
        """
        params = {
            "lat": latitude,
            "long": longitude,
            "radius": min(radius_km, MAX_RADIUS_KM),
            "per_page": PER_PAGE,
        }
        if naf_sections:
            params["section_activite_principale"] = naf_sections
        seen = 0
        page = 1
        while seen < max_results:
            data = self.api.get("/near_point", {**params, "page": page})
            results = data.get("results") or []
            for r in results:
                if r.get("caractere_employeur") == "N" or r.get("etat_administratif") == "C":
                    continue
                seen += 1
                yield parse_company(r)
                if seen >= max_results:
                    return
            if page >= (data.get("total_pages") or 0) or not results:
                return
            page += 1
