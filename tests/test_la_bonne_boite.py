import httpx

from job_radar import db
from job_radar.http import ApiClient
from job_radar.sources.france_travail import TokenProvider
from job_radar.sources.la_bonne_boite import SCOPE, LaBonneBoite, to_company

ITEMS = {
    "hits": 2,
    "items": [
        {"rome": "M1805", "siret": "90000000100017", "company_name": "ACME DEV (FICTIVE)",
         "office_name": "", "headcount_min": 3, "headcount_max": 5, "naf": "6202A",
         "naf_label": "Conseil en systèmes et logiciels informatiques",
         "location": {"lat": 47.841, "lon": 7.308}, "city": "Pulversheim", "postcode": "68840",
         "hiring_potential": 22.2, "is_high_potential": False, "email": "yes"},
        {"rome": "M1805", "siret": "90000000200011", "company_name": "BETA SOFT (FICTIVE)",
         "office_name": "BETA SOFT MULHOUSE", "naf": "5829C",
         "location": {"lat": 47.75, "lon": 7.34},
         "city": "Mulhouse", "postcode": "68100", "hiring_potential": 41.0,
         "is_high_potential": True},
    ],
}  # fmt: skip


def test_to_company():
    c = to_company(ITEMS["items"][0])
    assert (c.siret, c.siren, c.naf_code) == ("90000000100017", "900000001", "62.02A")
    assert (c.latitude, c.longitude, c.city) == (47.841, 7.308, "Pulversheim")
    assert c.accepts_email is True and c.is_high_potential is False
    office = to_company(ITEMS["items"][1])
    assert office.name == "BETA SOFT MULHOUSE"  # the establishment's own name when it has one
    assert office.accepts_email is False


def lbb(monkeypatch):
    monkeypatch.setenv("FRANCE_TRAVAIL_CLIENT_ID", "PAR_test_0123")
    monkeypatch.setenv("FRANCE_TRAVAIL_CLIENT_SECRET", "a" * 64)
    seen = {}

    def handler(request):
        if request.url.host == "entreprise.francetravail.fr":
            seen["scope"] = dict(x.split("=") for x in request.content.decode().split("&"))["scope"]
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 1499})
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json=ITEMS)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    api = ApiClient("https://api.francetravail.io/partenaire/labonneboite/v2", client=client)
    return LaBonneBoite(tokens=TokenProvider(scope=SCOPE, client=client), api=api), seen


def test_search_asks_for_the_search_scope(monkeypatch):
    client, seen = lbb(monkeypatch)
    found = list(client.search("M1805", 47.75, 7.33, 30.4))
    assert len(found) == 2
    assert seen["scope"] == "api_labonneboitev2+search"  # form-encoded space
    assert seen["params"] == {
        "rome": "M1805",
        "latitude": "47.75",
        "longitude": "7.33",
        "distance": "30",
    }


def test_likely_employers_ranked_by_potential(conn, monkeypatch):
    client, _ = lbb(monkeypatch)
    assert db.upsert_hiring(conn, client.search("M1805", 47.75, 7.33, 30)) == 2
    assert db.upsert_hiring(conn, client.search("M1805", 47.75, 7.33, 30)) == 2  # idempotent

    rows = db.likely_employers(conn, 47.7508, 7.3359, 30, romes=["M1805"])
    assert [r.name for r in rows] == ["BETA SOFT (FICTIVE)", "ACME DEV (FICTIVE)"]
    assert rows[0].is_high_potential and not rows[0].accepts_email
    assert rows[1].accepts_email
    assert db.likely_employers(conn, 47.7508, 7.3359, 5) == rows[:1]  # Pulversheim is ~10 km
    assert db.likely_employers(conn, 47.7508, 7.3359, 30, romes=["M1403"]) == []
    # Only the fact that email applications are accepted is stored, never an address.
    columns = [r[0] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'hiring_potential'"
    )]  # fmt: skip
    assert "email" not in columns
