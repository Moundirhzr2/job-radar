"""Companies likely to hire, even without a published offer (France Travail - La Bonne Boîte v2).

La Bonne Boîte ranks establishments by their hiring potential for a job (ROME code) around a
point, from the hiring declarations of all French employers. It is the radar's "hidden job
market": where to send a spontaneous application.

It never exposes a recruiter's email: `email` only says whether the company accepts
spontaneous applications by email ("yes").
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

from ..http import ApiClient
from .france_travail import TokenProvider

API = "https://api.francetravail.io/partenaire/labonneboite/v2"
SCOPE = "api_labonneboitev2 search"

# A few ROME codes for students in computing and data. Any ROME code can be passed directly.
ROME_FIELDS = {
    "dev": ["M1805"],  # Études et développement informatique
    "data": ["M1805", "M1403"],  # + Études et prospectives socio-économiques (analyse de données)
    "systemes": ["M1801", "M1810"],  # Administration / production et exploitation des SI
    "support": ["M1802"],  # Expertise et support en systèmes d'information
    "conseil": ["M1806"],  # Conseil et maîtrise d'ouvrage en SI
}


@dataclass(frozen=True)
class HiringCompany:
    siret: str
    company_name: str  # legal name of the company
    office_name: str  # name of this establishment, when it has its own
    naf_code: str  # "62.02A", the format of the company directory
    naf_label: str
    headcount_min: int | None
    headcount_max: int | None
    latitude: float | None
    longitude: float | None
    city: str
    postal_code: str
    rome: str
    hiring_potential: float
    is_high_potential: bool
    accepts_email: bool  # spontaneous applications by email accepted; the address is not given

    @property
    def siren(self) -> str:
        return self.siret[:9]

    @property
    def name(self) -> str:
        return self.office_name or self.company_name


def _naf(code: str) -> str:
    """ "6202A" -> "62.02A"."""
    code = (code or "").replace(".", "")
    return f"{code[:2]}.{code[2:]}" if len(code) == 5 else code


def to_company(item: dict) -> HiringCompany:
    loc = item.get("location") or {}
    return HiringCompany(
        siret=str(item.get("siret") or ""),
        company_name=(item.get("company_name") or "").strip(),
        office_name=(item.get("office_name") or "").strip(),
        naf_code=_naf(item.get("naf") or ""),
        naf_label=item.get("naf_label") or "",
        headcount_min=item.get("headcount_min"),
        headcount_max=item.get("headcount_max"),
        latitude=loc.get("lat"),
        longitude=loc.get("lon"),
        city=item.get("city") or "",
        postal_code=item.get("postcode") or "",
        rome=item.get("rome") or "",
        hiring_potential=float(item.get("hiring_potential") or 0),
        is_high_potential=bool(item.get("is_high_potential")),
        accepts_email=item.get("email") == "yes",
    )


@dataclass
class LaBonneBoite:
    tokens: TokenProvider = field(default_factory=lambda: TokenProvider(scope=SCOPE))
    api: ApiClient = field(default_factory=lambda: ApiClient(API, min_interval=0.5))  # 2/s

    def search(
        self, rome: str, latitude: float, longitude: float, distance_km: float
    ) -> Iterator[HiringCompany]:
        data = self.api.get(
            "/recherche",
            {
                "rome": rome,
                "latitude": latitude,
                "longitude": longitude,
                "distance": round(distance_km),
            },
            headers={"Authorization": f"Bearer {self.tokens()}", "Accept": "application/json"},
        )
        for item in (data or {}).get("items") or []:
            if item.get("siret"):
                yield to_company(item)
