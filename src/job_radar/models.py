"""The common shape every source is normalised into."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class Kind(StrEnum):
    """What the student is looking for. An offer can match several ("Stage ou alternance")."""

    INTERNSHIP = "internship"  # stage
    APPRENTICESHIP = "apprenticeship"  # alternance : apprentissage ou professionnalisation
    STUDENT_JOB = "student_job"  # job étudiant, temps partiel compatible avec les cours
    JOB = "job"  # CDI, CDD, intérim


@dataclass
class Location:
    city: str = ""
    postal_code: str = ""
    country: str = ""
    latitude: float | None = None
    longitude: float | None = None
    precision: str = ""  # "exact": coordinates from the source; "town": centre of the town

    @property
    def has_point(self) -> bool:
        return self.latitude is not None and self.longitude is not None


@dataclass
class Offer:
    source: str  # "careers:jsonld", "ats:greenhouse", "france_travail", ...
    source_id: str  # identifier inside that source
    url: str  # the original page, where the student applies
    title: str
    company: str = ""
    description: str = ""
    kinds: frozenset[Kind] = frozenset({Kind.JOB})
    employment_types: list[str] = field(default_factory=list)  # as published, e.g. ["INTERN"]
    location: Location = field(default_factory=Location)
    remote: bool | None = None
    published_at: datetime | None = None
    valid_through: datetime | None = None


_APPRENTICESHIP = re.compile(
    r"\b(alternance|alternant|alternante|apprenti|apprentie|apprentissage"
    r"|contrat de pro(fessionnalisation)?|work[- ]study|apprentice(ship)?)\b",
    re.I,
)
_INTERNSHIP = re.compile(r"\b(stage|stagiaire|internship|intern|praktikum)\b", re.I)
_STUDENT = re.compile(r"\b(job (é|e)tudiant|student job|(é|e)tudiant(e)?s?)\b", re.I)


def classify(title: str, employment_types: list[str], part_time: bool = False) -> frozenset[Kind]:
    """Infer what an offer is from its title and published contract types.

    A part-time job is kept as a possible student job: most student jobs are ordinary
    part-time contracts that never say "étudiant".
    """
    text = f"{title} {' '.join(employment_types)}"
    kinds = set()
    if _APPRENTICESHIP.search(text):
        kinds.add(Kind.APPRENTICESHIP)
    if _INTERNSHIP.search(text):
        kinds.add(Kind.INTERNSHIP)
    if not kinds:
        kinds.add(Kind.JOB)
        if part_time or _STUDENT.search(title):
            kinds.add(Kind.STUDENT_JOB)
    return frozenset(kinds)
