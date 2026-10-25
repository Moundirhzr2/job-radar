"""La bonne alternance export: offers kept, France Travail offers set apart, and the companies
that take apprentices without an offer."""

import json
from dataclasses import replace

import pytest

from job_radar import db
from job_radar.models import Kind
from job_radar.sources.la_bonne_alternance import (
    SCHOOL,
    TokenMissing,
    entries,
    read_token,
    to_offer,
    to_recruiter,
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
RECRUITER = {
    "identifier": {
        "id": "y",
        "partner_label": "recruteurs_lba",
        "partner_job_id": "12345678900011",
    },
    "workplace": {
        "siret": "12345678900011",
        "brand": "ACME DATA",
        "legal_name": "ACME (FICTIVE)",
        "name": "ACME (FICTIVE)",
        "size": "20-49",
        "location": {
            "address": "5 rue de la Gare 68200 Mulhouse",
            "geopoint": {"type": "Point", "coordinates": [7.34, 47.74]},
        },
        "domain": {"naf": {"code": "62.02A", "label": "Conseil en systèmes informatiques"}},
    },
    "apply": {"url": "https://labonnealternance.example/recruteurs_lba/12345678900011"},
    "contract": {"type": []},
    "offer": {"title": "recruteurs_lba", "rome_codes": ["D1106", "M1805", "M1810"]},
}
IT = {"M1805", "M1810"}


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


def test_france_travail_offers_and_companies_without_offers_are_not_offers():
    assert to_offer(FRANCE_TRAVAIL) is None  # already read from France Travail itself
    assert to_offer(RECRUITER) is None


def test_an_offer_managed_by_a_school_says_so():
    assert SCHOOL in to_offer(OFFER | {"is_delegated": True}).employment_types


def test_a_company_that_takes_apprentices_in_the_field_once_under_its_first_trade():
    company, url = to_recruiter(RECRUITER, IT)
    assert (company.siret, company.siren, company.rome) == ("12345678900011", "123456789", "M1805")
    assert (company.name, company.naf_code) == ("ACME DATA", "62.02A")
    assert (company.city, company.postal_code, company.headcount_min) == ("Mulhouse", "68200", 20)
    assert url == "https://labonnealternance.example/recruteurs_lba/12345678900011"
    assert to_recruiter(RECRUITER, {"A1101"}) is None  # not in the field
    assert to_recruiter(OFFER, IT) is None  # an offer is not a recruiter


def test_the_export_is_read_one_entry_at_a_time(tmp_path):
    path = tmp_path / "export.json"
    broken = {"identifier": None, "offer": None}
    path.write_text(json.dumps([OFFER, FRANCE_TRAVAIL, broken, RECRUITER, OFFER]), "utf-8")
    assert len(list(entries(path, chunk=64))) == 5  # objects cut across small blocks


def test_recruiters_replace_yesterdays_and_keep_la_bonne_boite_scores(conn):
    company, url = to_recruiter(RECRUITER, IT)
    scored = replace(company, siret="98765432100022", hiring_potential=4.2)
    db.upsert_hiring(conn, [scored])  # La Bonne Boîte already knows this one
    assert (
        db.replace_apprentice_recruiters(
            conn, [(company, url), (replace(scored, hiring_potential=0), url)]
        )
        == 2
    )
    rows = db.likely_employers(conn, 47.7508, 7.3359, 30)
    assert [(r.siret, r.score, r.source) for r in rows] == [
        ("98765432100022", 4.2, "la_bonne_boite"),  # scored first, score kept
        ("12345678900011", 0.0, "la_bonne_alternance"),
    ]
    assert all(r.apply_url == url for r in rows)

    db.replace_apprentice_recruiters(conn, [])  # gone from today's export
    rows = db.likely_employers(conn, 47.7508, 7.3359, 30)
    assert [(r.siret, r.apply_url) for r in rows] == [("98765432100022", None)]


def test_missing_token(monkeypatch):
    monkeypatch.delenv("LBA_API_TOKEN", raising=False)
    with pytest.raises(TokenMissing):
        read_token()
