import httpx
import pytest

from job_radar.http import ApiClient
from job_radar.models import Location, Offer
from job_radar.sources.geo import Geocoder, GeocodingUnavailable, clean_city, place

MULHOUSE = {
    "nom": "Mulhouse",
    "code": "68224",
    "codesPostaux": ["68100", "68200"],
    "centre": {"type": "Point", "coordinates": [7.3255, 47.7526]},
    "population": 104978,
}
IGN_MULHOUSE = {
    "features": [
        {
            "geometry": {"type": "Point", "coordinates": [7.3265, 47.7517]},
            "properties": {
                "type": "municipality",
                "city": "Mulhouse",
                "citycode": "68224",
                "postcode": "68100",
                "population": 104978,
            },
        }
    ]
}


def scripted(base, answers, calls):
    def handler(request):
        calls.append(str(request.url))
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    return ApiClient(
        base,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda s: None,
        retries=1,
    )


def geocoder(geo_answers, ign_answers):
    calls = []
    return (
        Geocoder(
            geo_api=scripted("https://geo.api.gouv.fr", geo_answers, calls),
            ign_api=scripted("https://data.geopf.fr/geocodage", ign_answers, calls),
        ),
        calls,
    )


@pytest.mark.parametrize(
    ("raw", "town"),
    [
        ("Paris 75002", "Paris"),
        ("Strasbourg, France", "Strasbourg"),
        ("Remote, Lyon", "Lyon"),
        ("Mulhouse (68)", "Mulhouse"),
        ("", ""),
    ],
)
def test_clean_city(raw, town):
    assert clean_city(raw) == town


def test_postal_code_lookup_is_cached():
    g, calls = geocoder([httpx.Response(200, json=[MULHOUSE])], [])
    assert g.locate("MULHOUSE", "68100").insee_code == "68224"
    assert g.locate("Mulhouse", "68100").insee_code == "68224"
    assert len(calls) == 1


def test_falls_back_to_ign_when_geo_api_is_down():
    down = [httpx.ConnectError("reset")] * 2
    g, calls = geocoder(down, [httpx.Response(200, json=IGN_MULHOUSE)])
    town = g.locate("Mulhouse", "68100")
    assert (town.name, town.latitude) == ("Mulhouse", 47.7517)
    assert calls[-1].startswith("https://data.geopf.fr/geocodage/search")


def test_unavailable_is_raised_and_not_cached():
    down = [httpx.ConnectError("reset")] * 2
    g, calls = geocoder(down + [httpx.Response(200, json=[MULHOUSE])], list(down))
    with pytest.raises(GeocodingUnavailable):
        g.locate("Mulhouse", "68100")
    assert g.locate("Mulhouse", "68100").name == "Mulhouse"  # retried, not a cached failure


def test_place_keeps_going_when_services_are_down():
    down = [httpx.ConnectError("reset")] * 2
    g, _ = geocoder(list(down), list(down))
    offers = [
        Offer("t", "1", "u", "Stage", location=Location(city="Mulhouse", postal_code="68100")),
        Offer("t", "2", "u", "Job", location=Location(city="Toronto", country="CA")),
        Offer("t", "3", "u", "Job", location=Location(latitude=47.7, longitude=7.3)),
    ]
    report = place(offers, g)
    assert (report.placed, report.unplaced, report.unavailable) == (0, 1, 1)
    assert offers[0].location.has_point is False
    assert offers[2].location.precision == "exact"


def test_foreign_offers_are_not_geocoded():
    g, calls = geocoder([], [])
    assert g.locate("Toronto", "M6A 2T9", "CA") is None
    assert calls == []
