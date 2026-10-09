"""The web API, on a real PostgreSQL, with fake models and a fake Claude (no network, no key)."""

import json

import pytest
from fastapi.testclient import TestClient

from job_radar import db
from job_radar.draft import Draft
from job_radar.draft import check as check_draft
from job_radar.fit import Fit, check
from job_radar.sources.geo import GeocodingUnavailable, Town
from job_radar.web import Services, create_app
from tests.conftest import TEST_DATABASE_URL
from tests.test_draft import ANSWER as DRAFT
from tests.test_fit import ANSWER as FIT
from tests.test_fit import PROFILE
from tests.test_pipeline import FakeEmbedder, FakeReranker
from tests.test_search import MULHOUSE, indexed  # noqa: F401  (indexed is a fixture)


class FakeGeocoder:
    def search(self, name, limit=5):
        if name == "panne":
            raise GeocodingUnavailable("down")
        return [Town("Mulhouse", "68224", ("68100", "68200"), *MULHOUSE, 105000)][:limit]


def fake_fit(text, profile):
    return check(Fit(**json.loads(json.dumps(FIT))), text, profile)


@pytest.fixture
def web(indexed, tmp_path):  # noqa: F811
    profile = tmp_path / "profile.md"
    profile.write_text(PROFILE, encoding="utf-8")
    calls = []
    services = Services(
        database_url=TEST_DATABASE_URL,
        profile_path=profile,
        geocoder=FakeGeocoder(),
        embedder=FakeEmbedder,
        reranker=FakeReranker,
        rewrite=None,
        analyse=lambda text, prof: calls.append("fit") or fake_fit(text, prof),
        draft=lambda text, contact, prof: check_draft(Draft(**DRAFT), prof),
        allows_ai_input=lambda url: True,
    )
    with TestClient(create_app(services)) as client:
        client.calls, client.services = calls, services
        yield client


def ids_by_title(client, titles):
    radar = client.get(
        "/api/radar", params={"lat": MULHOUSE[0], "lon": MULHOUSE[1], "radius_km": 150}
    )
    return {o["title"]: o["id"] for o in radar.json()["offers"] if o["title"] in titles}


def test_page_and_towns(web):
    assert "Job Radar" in web.get("/").text
    towns = web.get("/api/towns", params={"q": "Mulhouse"}).json()
    assert towns[0] | {} == {
        "name": "Mulhouse", "insee": "68224", "postal_codes": ["68100", "68200"],
        "lat": MULHOUSE[0], "lon": MULHOUSE[1],
    }  # fmt: skip
    assert web.get("/api/towns", params={"q": "panne"}).status_code == 503
    assert web.get("/api/towns", params={"q": "x"}).status_code == 422  # too short


def test_radar_offers_and_places(web):
    params = {"lat": MULHOUSE[0], "lon": MULHOUSE[1], "radius_km": 30}
    radar = web.get("/api/radar", params=params).json()
    assert radar["count"] == len(radar["offers"]) == 3  # Strasbourg is outside 30 km
    distances = [o["distance_km"] for o in radar["offers"]]
    assert distances == sorted(distances)
    assert all(o["lat"] and o["lon"] for o in radar["offers"])
    assert sum(p["count"] for p in radar["places"]) == 3
    only = web.get("/api/radar", params=params | {"kind": "apprenticeship"}).json()
    assert [o["title"] for o in only["offers"]] == ["Alternance Développeur Python"]
    assert web.get("/api/radar", params=params | {"radius_km": 500}).status_code == 422


def test_search_without_rewriting_and_with_a_failing_rewrite(web):
    params = {"q": "data sql", "lat": MULHOUSE[0], "lon": MULHOUSE[1], "radius_km": 30}
    found = web.get("/api/search", params=params | {"rewrite": "false"}).json()
    assert found["results"][0]["title"] == "Data analyst (H/F)"
    assert found["results"][0]["score"] == 1.0 and found["confident"]
    assert found["query"]["rewritten"] is False and found["notice"] is None

    def broken(request, profile):
        raise RuntimeError("no key")

    web.services.rewrite = broken
    found = web.get("/api/search", params=params).json()
    assert "Réécriture indisponible" in found["notice"] and found["results"]


def test_offer_detail_fit_cache_and_draft(web):
    offer_id = ids_by_title(web, {"Data analyst (H/F)"})["Data analyst (H/F)"]
    detail = web.get(f"/api/offers/{offer_id}").json()
    assert detail["title"] == "Data analyst (H/F)" and detail["application_id"] is None
    assert web.get("/api/offers/999999").status_code == 404

    first = web.post(f"/api/offers/{offer_id}/fit").json()
    second = web.post(f"/api/offers/{offer_id}/fit").json()
    assert (first["cached"], second["cached"], web.calls) == (False, True, ["fit"])
    by_skill = {r["skill"]: r for r in second["requirements"]}
    assert by_skill["dbt"]["status"] == "to_confirm" and by_skill["dbt"]["note"]

    draft = web.post(f"/api/offers/{offer_id}/draft").json()
    assert draft["subject"] == DRAFT["subject"]
    assert [f["found"] for f in draft["facts"]] == [True, False]


def test_claude_is_never_asked_without_a_profile_or_against_a_site_refusal(web):
    offer_id = next(iter(ids_by_title(web, {"Cuisinier"}).values()))
    web.services.allows_ai_input = lambda url: False
    assert web.post(f"/api/offers/{offer_id}/fit").status_code == 403
    web.services.profile_path = None
    assert web.post(f"/api/offers/{offer_id}/draft").status_code == 409
    assert web.calls == []


def test_application_tracker(web):
    offer_id = next(iter(ids_by_title(web, {"Cuisinier"}).values()))
    tracked = web.post("/api/applications", json={"offer_id": offer_id}).json()
    assert tracked["title"] == "Cuisinier" and tracked["status"] == "to_apply"
    assert web.get(f"/api/offers/{offer_id}").json()["application_id"] == tracked["id"]

    spontaneous = web.post(
        "/api/applications", json={"company": "Boulangerie Martin", "channel": "sur place"}
    )
    assert spontaneous.status_code == 201
    assert web.post("/api/applications", json={"notes": "?"}).status_code == 422
    assert web.post("/api/applications", json={"company": "X", "status": "lost"}).status_code == 422

    sent = web.patch(f"/api/applications/{tracked['id']}", json={"status": "sent"}).json()
    assert sent["applied_on"] and sent["follow_up_on"] and not sent["follow_up_due"]
    web.patch(f"/api/applications/{tracked['id']}", json={"follow_up_on": "2026-01-01"})
    listing = web.get("/api/applications").json()
    assert listing["applications"][0]["follow_up_due"] is True  # past date: follow up now
    stats = listing["stats"]
    assert (stats["sent"], stats["answered"], stats["follow_ups_due"]) == (1, 0, 1)
    assert stats["by_status"]["to_apply"] == 1

    assert web.delete(f"/api/applications/{tracked['id']}").status_code == 204
    assert web.delete(f"/api/applications/{tracked['id']}").status_code == 404
    assert web.patch("/api/applications/999999", json={"notes": "x"}).status_code == 404


def test_employers_layers(web, conn):
    found = web.get(
        "/api/employers", params={"lat": MULHOUSE[0], "lon": MULHOUSE[1], "radius_km": 20}
    ).json()
    assert found == {"hiring": [], "digital": []}  # none loaded in this database
    assert db.list_applications(conn) == []
