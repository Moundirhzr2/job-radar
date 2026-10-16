"""La bonne alternance export: offers kept, France Travail offers and recruiters set apart."""

import json

import pytest

from job_radar.models import Kind
from job_radar.sources.la_bonne_alternance import (
    SCHOOL,
    TokenMissing,
    entries,
    offers,
    read_token,
    to_offer,
)

# The shape of the export, as published (values made up).
OFFER = {
    "identifier": {"id": "6ac0", "partner_label": "Hellowork", "partner_job_id": "H42"},
    "workplace": {
        "name": "Data Conseil",
        "brand": None,
        "location": {
            "address": "12 rue du Sauvage 68100 Mulhouse",
            "geopoint": {"type": "Point", "coordinates": [7.3359, 47.7508]},
        },
    },
    "apply": {"url": "https://example.org/offre/H42", "phone": None, "recipient_id": None},
    "contract": {"type": ["Apprentissage", "Professionnalisation"], "remote": "hybrid"},
    "offer": {
        "title": "Alternance Data Analyst (H/F)",
        "description": "<p>SQL et Power BI.</p><p>35 heures par semaine.</p>",
        "publication": {"creation": "2026-10-01T13:52:15.000Z", "expiration": None},
        "status": "Active",
    },
    "is_delegated": False,
}
FRANCE_TRAVAIL = OFFER | {"identifier": {"id": "x", "partner_label": "France Travail"}}
RECRUITER = OFFER | {"identifier": {"id": "y", "partner_label": "recruteurs_lba"}}


def test_an_offer_as_the_radar_stores_it():
    offer = to_offer(OFFER)
    assert (offer.source, offer.source_id) == ("la_bonne_alternance", "6ac0")
    assert offer.url == "https://example.org/offre/H42"
    assert offer.title == "Alternance Data Analyst (H/F)" and offer.company == "Data Conseil"
    assert offer.kinds == frozenset({Kind.APPRENTICESHIP})
    assert offer.description == "SQL et Power BI.\n35 heures par semaine."  # no HTML left
    assert offer.weekly_hours == 35
    loc = offer.location
    assert (loc.city, loc.postal_code, loc.precision) == ("Mulhouse", "68100", "exact")
    assert (loc.latitude, loc.longitude) == (47.7508, 7.3359)
    assert offer.remote is True
    assert "via Hellowork" in offer.employment_types and SCHOOL not in offer.employment_types


def test_france_travail_offers_and_companies_without_offers_are_not_added():
    assert to_offer(FRANCE_TRAVAIL) is None  # already read from France Travail itself
    assert to_offer(RECRUITER) is None


def test_an_offer_managed_by_a_school_says_so():
    assert SCHOOL in to_offer(OFFER | {"is_delegated": True}).employment_types


def test_the_export_is_read_one_entry_at_a_time(tmp_path):
    path = tmp_path / "export.json"
    broken = {"identifier": None, "offer": None}
    path.write_text(json.dumps([OFFER, FRANCE_TRAVAIL, broken, RECRUITER, OFFER]), "utf-8")
    assert len(list(entries(path, chunk=64))) == 5  # objects cut across small blocks
    assert [o.source_id for o in offers(path)] == ["6ac0", "6ac0"]


def test_missing_token(monkeypatch):
    monkeypatch.delenv("LBA_API_TOKEN", raising=False)
    with pytest.raises(TokenMissing):
        read_token()
