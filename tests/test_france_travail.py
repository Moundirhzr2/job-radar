import json
from pathlib import Path

import httpx
import pytest

from job_radar.http import ApiClient
from job_radar.models import Kind
from job_radar.sources.france_travail import (
    CredentialsError,
    FranceTravail,
    TokenProvider,
    read_credentials,
    to_offer,
)

DATA = json.loads((Path(__file__).parent / "fixtures" / "france_travail.json").read_text("utf-8"))


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setenv("FRANCE_TRAVAIL_CLIENT_ID", "PAR_test_0123")
    monkeypatch.setenv("FRANCE_TRAVAIL_CLIENT_SECRET", "a" * 64)


def test_to_offer_apprenticeship():
    o = to_offer(DATA["resultats"][0])
    assert (o.source, o.source_id) == ("france_travail", "900AAAA")
    assert o.kinds == {Kind.APPRENTICESHIP}
    assert (o.location.city, o.location.postal_code) == ("MULHOUSE", "68100")
    assert (o.location.latitude, o.location.precision) == (47.75, "exact")
    assert o.company == "ACME DATA (FICTIVE)"
    assert o.contact.startswith("ACME DATA - Mme Exemple | Pour postuler")
    assert o.url.endswith("/detail/900AAAA")


def test_to_offer_part_time_without_position():
    o = to_offer(DATA["resultats"][1])
    assert o.kinds == {Kind.JOB, Kind.STUDENT_JOB}
    assert o.location.city == "ILLZACH"
    assert not o.location.has_point and o.location.precision == ""
    assert o.url == "https://candidat.francetravail.fr/offres/recherche/detail/900BBBB"
    assert o.company == "" and o.contact == ""


@pytest.mark.parametrize("secret", ['Clé secrète : "' + "a" * 64 + '"', "a" * 32 + " " + "a" * 32])
def test_badly_pasted_secret_is_explained(monkeypatch, secret):
    monkeypatch.setenv("FRANCE_TRAVAIL_CLIENT_ID", "PAR_test_0123")
    monkeypatch.setenv("FRANCE_TRAVAIL_CLIENT_SECRET", secret)
    with pytest.raises(CredentialsError, match="sans libellé ni guillemets"):
        read_credentials()


def test_missing_keys(monkeypatch):
    monkeypatch.delenv("FRANCE_TRAVAIL_CLIENT_ID", raising=False)
    with pytest.raises(CredentialsError, match="absentes"):
        read_credentials()


def fake_france_travail(pages):
    calls = {"token": 0, "search": []}

    def handler(request):
        if request.url.host == "entreprise.francetravail.fr":
            calls["token"] += 1
            form = dict(x.split("=") for x in request.content.decode().split("&"))
            assert form["grant_type"] == "client_credentials"
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 1499})
        assert request.headers["Authorization"] == "Bearer tok"
        calls["search"].append(dict(request.url.params))
        return pages.pop(0)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    ft = FranceTravail(
        tokens=TokenProvider(client=client),
        api=ApiClient(
            "https://api.francetravail.io/partenaire/offresdemploi/v2",
            client=client,
            sleep=lambda s: None,
        ),
    )
    return ft, calls


def test_search_pages_filters_and_reuses_the_token(keys):
    full_page = {"resultats": [DATA["resultats"][0] | {"id": f"P{i}"} for i in range(150)]}
    ft, calls = fake_france_travail(
        [httpx.Response(206, json=full_page), httpx.Response(206, json=DATA)]
    )
    offers = list(ft.search("68224", 20, Kind.APPRENTICESHIP))
    assert len(offers) == 152
    assert calls["token"] == 1
    first, second = calls["search"]
    assert (first["commune"], first["distance"], first["natureContrat"]) == ("68224", "20", "E2,FS")
    assert (first["range"], second["range"]) == ("0-149", "150-299")


def test_search_nothing_found_and_no_internships(keys):
    ft, calls = fake_france_travail([httpx.Response(204)])
    assert list(ft.search("68224", 20, Kind.JOB, keywords="introuvable")) == []
    assert list(ft.search("68224", 20, Kind.INTERNSHIP)) == []  # not published on France Travail
    assert len(calls["search"]) == 1


def test_refused_keys_are_reported(keys):
    def handler(request):
        return httpx.Response(
            400,
            json={"error": "invalid_client", "error_description": "Client authentication failed"},
        )

    tokens = TokenProvider(client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(CredentialsError, match="Client authentication failed"):
        tokens()
