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
    contact: str = ""  # contact published by the employer in the offer, as published
    weekly_hours: float | None = None  # hours a week; the lowest one when the offer gives a range


_APPRENTICESHIP = re.compile(
    r"\b(alternance|alternant|alternante|apprenti|apprentie|apprentissage"
    r"|contrat de pro(fessionnalisation)?|work[- ]study|apprentice(ship)?)\b",
    re.I,
)
_INTERNSHIP = re.compile(r"\b(stage|stagiaire|internship|intern|praktikum)\b", re.I)
_STUDENT = re.compile(r"\b(job (é|e)tudiant|student job|(é|e)tudiant(e)?s?)\b", re.I)

# A job a student can hold alongside classes: 26 hours a week at most. When the offer gives a
# range ("de 24 à 30 heures par semaine") or says the hours are negotiable, the lowest value
# counts: the student can ask for it. "Temps partiel" alone means nothing (France Travail
# calls 34 h 12 a part-time job), so it decides only when no hours are published.
STUDENT_MAX_HOURS = 26.0

_N = r"(?<![\d,.])(\d{1,2})"  # a number of hours, not the end of "151,67"
_UNIT = r"(?:\s*h(\d{2})?|\s*heures?)"  # "24h", "17h30", "24 heures"
_WEEK = r"\s*(?:par\s+|/\s*|de\s+|en\s+)?(?:semaine|hebdo)"
_HOURS = [
    # "de 20 à 25 heures par semaine", "CDI de 12 à 30 heures semaine"
    re.compile(_N + _UNIT + r"?\s*(?:à|a|-|–)\s*" + _N + _UNIT + _WEEK, re.I),
    # "volume horaire hebdomadaire, de 4 à 24 heures"
    re.compile(
        r"(?:hebdomadaire|par\s+semaine)\W{0,3}(?:de\s+)?"
        + _N
        + _UNIT
        + r"?\s*(?:à|a|-|–)\s*"
        + _N
        + _UNIT,
        re.I,
    ),
    # "24H/semaine", "17H30 par semaine", "24h Hebdo", "24 heures hebdomadaires"
    re.compile(_N + _UNIT + _WEEK, re.I),
]
_MONTHLY = re.compile(r"(?<![\d,.])(\d{2,3})" + _UNIT + r"\s*(?:par\s+|/\s*)?mois", re.I)


def weekly_hours(*texts: str | None) -> float | None:
    """The lowest number of hours a week an offer states, or None if it states none.

    Only figures tied to a week (or a month, converted) count: "de 17h30 à 0h30" is an opening
    time, not a contract.
    """
    found: list[float] = []
    for text in filter(None, texts):
        for pattern in _HOURS:
            for m in pattern.finditer(text):
                numbers = [g for g in m.groups()]
                # groups come in (hours, minutes) pairs
                for h, mins in zip(numbers[::2], numbers[1::2], strict=True):
                    if h is not None:
                        found.append(int(h) + (int(mins) / 60 if mins else 0))
        for m in _MONTHLY.finditer(text):
            found.append((int(m.group(1)) + (int(m.group(2)) / 60 if m.group(2) else 0)) * 12 / 52)
    found = [h for h in found if 4 <= h <= 48]  # "2 heures par semaine de formation" is no contract
    return round(min(found), 2) if found else None


def classify(
    title: str,
    employment_types: list[str],
    part_time: bool = False,
    weekly_hours: float | None = None,
) -> frozenset[Kind]:
    """Infer what an offer is from its title, published contract types and hours.

    A job is also a student job when its hours fit alongside classes (STUDENT_MAX_HOURS). Most
    student jobs are ordinary part-time contracts that never say "étudiant"; when no hours are
    published, "temps partiel" or a title for students decides.
    """
    text = f"{title} {' '.join(employment_types)}"
    kinds = set()
    if _APPRENTICESHIP.search(text):
        kinds.add(Kind.APPRENTICESHIP)
    if _INTERNSHIP.search(text):
        kinds.add(Kind.INTERNSHIP)
    if not kinds:
        kinds.add(Kind.JOB)
        if weekly_hours is not None:
            student = weekly_hours <= STUDENT_MAX_HOURS
        else:
            student = part_time or bool(_STUDENT.search(title))
        if student:
            kinds.add(Kind.STUDENT_JOB)
    return frozenset(kinds)
