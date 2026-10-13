"""The same search on the big job sites, as links the student opens in their own browser.

The radar never reads these sites (their terms forbid automated access): it only prepares the
search address, with the words, the town, the radius and the contract types already filled
in. What the student sees there stays in their browser.

A site filters on contract types only when it has a filter for every kind asked for; a kind
it cannot filter (LinkedIn has no "alternance") would otherwise be hidden by the others. A
single kind without a filter goes into the words instead ("data" + "alternance").
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import urlencode

from .models import Kind

WORDS = {
    Kind.INTERNSHIP: "stage",
    Kind.APPRENTICESHIP: "alternance",
    Kind.STUDENT_JOB: "job étudiant",
    Kind.JOB: "",
}
# Contract filters of each site, for the kinds it can filter.
LINKEDIN_TYPES = {Kind.INTERNSHIP: "I", Kind.STUDENT_JOB: "P", Kind.JOB: "F,C,T"}
WTTJ_TYPES = {
    Kind.INTERNSHIP: ["internship"],
    Kind.APPRENTICESHIP: ["apprenticeship"],
    Kind.STUDENT_JOB: ["part_time"],
    Kind.JOB: ["full_time", "temporary"],
}
LINKEDIN_MILES = (5, 10, 25, 50, 100)
INDEED_KM = (5, 10, 15, 25, 35, 50, 100)
KM_PER_MILE = 1.609


@dataclass(frozen=True)
class Link:
    site: str
    url: str


def _covering(radius: float, choices: Sequence[int]) -> int:
    """The smallest radius a site offers that still covers the radar's circle."""
    return next((c for c in choices if c >= radius), choices[-1])


def _words(q: str, kinds: Sequence[Kind], filtered: bool) -> str:
    """The request, plus the contract word when the site cannot filter that single kind."""
    if filtered or len(kinds) != 1:
        return q
    word = WORDS[kinds[0]]
    return f"{q} {word}" if word and word not in q.lower() else q


def _filter(kinds: Sequence[Kind], types: dict) -> list:
    """The site's contract filters, or none when one of the kinds has no filter there."""
    if not kinds or any(k not in types for k in kinds):
        return []
    return [types[k] for k in kinds]


def links(q: str, town: str, radius_km: float, kinds: Sequence[Kind] = ()) -> list[Link]:
    kinds = list(dict.fromkeys(kinds))  # keep the order, drop repeats
    q = q.strip()

    linkedin_types = _filter(kinds, LINKEDIN_TYPES)
    linkedin = {
        "keywords": _words(q, kinds, bool(linkedin_types)),
        "location": f"{town}, France",
        "distance": _covering(radius_km / KM_PER_MILE, LINKEDIN_MILES),
    }
    if linkedin_types:
        linkedin["f_JT"] = ",".join(linkedin_types)

    wttj_types = [t for group in _filter(kinds, WTTJ_TYPES) for t in group]
    wttj = [("query", _words(q, kinds, bool(wttj_types))), ("aroundQuery", town)]
    wttj += [("refinementList[contract_type][]", t) for t in wttj_types]

    plain = _words(q, kinds, False)
    return [
        Link("LinkedIn", "https://www.linkedin.com/jobs/search/?" + urlencode(linkedin)),
        Link(
            "Welcome to the Jungle", "https://www.welcometothejungle.com/fr/jobs?" + urlencode(wttj)
        ),
        Link(
            "HelloWork",
            "https://www.hellowork.com/fr-fr/emploi/recherche.html?"
            + urlencode({"k": plain, "l": town, "ray": round(radius_km)}),
        ),
        Link(
            "Indeed",
            "https://fr.indeed.com/jobs?"
            + urlencode({"q": plain, "l": town, "radius": _covering(radius_km, INDEED_KM)}),
        ),
    ]
