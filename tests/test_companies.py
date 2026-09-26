import json
from pathlib import Path

import httpx
import pytest

from job_radar import db
from job_radar.http import ApiClient, ApiError
from job_radar.sources.companies import CompanyDirectory
from job_radar.sources.geo import find_towns

NEAR = json.loads((Path(__file__).parent / "fixtures" / "near_point.json").read_text("utf-8"))


def api(base: str, responses: list):
    """An ApiClient whose HTTP answers are scripted; records the requests it makes."""
    requested = []

    def handler(request):
        requested.append(request.url)
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    client = ApiClient(
        base, client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda s: None
    )
    return client, requested


def directory(responses):
    client, requested = api("https://recherche-entreprises.api.gouv.fr", responses)
    return CompanyDirectory(api=client), requested


def test_near_keeps_employers_and_minimises_personal_data():
    d, requested = directory([httpx.Response(200, json=NEAR)])
    companies = list(d.near(47.75, 7.34, 80, naf_sections="J"))
    assert [c.name for c in companies] == ["DATA ACME (FICTIVE)", "GRAND GROUPE (FICTIF)"]
    params = requested[0].params
    assert (params["radius"], params["section_activite_principale"]) == ("50", "J")  # capped

    small, big = companies
    assert small.is_small and small.headcount_label == "10 à 19"
    # Name and role only: no birth date; auditors are not recruiters.
    assert [(o.name, o.role) for o in small.officers] == [
        ("Marie Claire Dupont", "Président de SAS"),
        ("HOLDING ACME", "Directeur général"),
    ]
    assert not hasattr(small.officers[0], "birth")
    assert big.officers == []  # large company: officers are not the people who hire

    (site,) = small.establishments
    assert (site.city, site.latitude, site.longitude) == ("MULHOUSE", 47.743, 7.342)
    assert site.opened_on.isoformat() == "2024-03-01"


def test_retries_dropped_connections_and_rate_limits():
    d, requested = directory(
        [
            httpx.ConnectError("reset by peer"),
            httpx.Response(429, headers={"Retry-After": "1"}),
            httpx.Response(503),
            httpx.Response(200, json=NEAR),
        ]
    )
    assert len(list(d.near(47.75, 7.34, 5))) == 2
    assert len(requested) == 4


def test_gives_up_on_client_errors_and_after_retries():
    d, _ = directory([httpx.Response(400, text="bad radius")])
    with pytest.raises(ApiError, match="400"):
        list(d.near(47.75, 7.34, 5))
    d, _ = directory([httpx.Response(503)] * 5)
    with pytest.raises(ApiError, match="failed after 5 attempts"):
        list(d.near(47.75, 7.34, 5))


def test_find_towns():
    client, _ = api(
        "https://geo.api.gouv.fr",
        [
            httpx.Response(
                200,
                json=[
                    {
                        "nom": "Mulhouse",
                        "code": "68224",
                        "codesPostaux": ["68100", "68200"],
                        "centre": {"type": "Point", "coordinates": [7.3255, 47.7526]},
                        "population": 104978,
                    }
                ],
            )
        ],
    )
    (town,) = find_towns("Mulhouse", api=client)
    assert (town.insee_code, town.latitude, town.longitude) == ("68224", 47.7526, 7.3255)


def test_companies_on_the_radar(conn):
    d, _ = directory([httpx.Response(200, json=NEAR)])
    assert db.upsert_companies(conn, d.near(47.75, 7.34, 10)) == 2
    again, _ = directory([httpx.Response(200, json=NEAR)])
    assert db.upsert_companies(conn, again.near(47.75, 7.34, 10)) == 2  # idempotent

    around = db.companies_within(conn, 47.7508, 7.3359, 5)
    assert [c.name for c in around] == ["DATA ACME (FICTIVE)", "GRAND GROUPE (FICTIF)"]
    assert around[0].officers == [
        "Marie Claire Dupont (Président de SAS)",
        "HOLDING ACME (Directeur général)",
    ]
    assert around[1].officers == []

    it_only = db.companies_within(conn, 47.7508, 7.3359, 5, naf_prefixes=["62"])
    assert [c.siren for c in it_only] == ["900000001"]
    assert db.companies_within(conn, 48.5734, 7.7521, 20) == []  # Strasbourg: too far

    (source,) = conn.execute(
        "SELECT DISTINCT source_url FROM company_contacts WHERE kind = 'registry_officer'"
    ).fetchone()
    assert source == "https://annuaire-entreprises.data.gouv.fr/entreprise/900000001"
