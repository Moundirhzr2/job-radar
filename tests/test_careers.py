import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from job_radar.models import Kind, classify
from job_radar.sources.careers.ats import Board, detect_boards, fetch_board
from job_radar.sources.careers.fetch import Disallowed, PoliteFetcher
from job_radar.sources.careers.jsonld import extract_job_postings, html_to_text

FIXTURES = Path(__file__).parent / "fixtures"
PAGE = (FIXTURES / "careers_page.html").read_text(encoding="utf-8")
PAGE_URL = "https://acme.example/carrieres"


# --- classification -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "types", "part_time", "expected"),
    [
        ("Stage Data Analyst", [], False, {Kind.INTERNSHIP}),
        ("Data Analyst", ["INTERN"], False, {Kind.INTERNSHIP}),
        ("Stage ou alternance - BI", [], False, {Kind.INTERNSHIP, Kind.APPRENTICESHIP}),
        ("Développeur (H/F)", ["Contrat de professionnalisation"], False, {Kind.APPRENTICESHIP}),
        ("Vendeur", ["PART_TIME"], True, {Kind.JOB, Kind.STUDENT_JOB}),
        ("Job étudiant - caisse", [], False, {Kind.JOB, Kind.STUDENT_JOB}),
        ("Data Engineer", ["FULL_TIME"], False, {Kind.JOB}),
        ("Internal Auditor", [], False, {Kind.JOB}),  # "internal" is not "intern"
    ],
)
def test_classify(title, types, part_time, expected):
    assert classify(title, types, part_time) == frozenset(expected)


# --- schema.org JobPosting ----------------------------------------------------------------


def test_extracts_job_postings_from_graph_and_skips_broken_scripts():
    offers = extract_job_postings(PAGE, PAGE_URL)
    assert [o.title for o in offers] == [
        "Stage Data Analyst (6 mois)",
        "Alternance - Développeur Python",
    ]

    stage, alternance = offers
    assert stage.source_id == "ACME-42"
    assert stage.url == "https://acme.example/carrieres/stage-data-analyst"
    assert stage.company == "Acme"
    assert stage.kinds == {Kind.INTERNSHIP}
    assert stage.location.city == "Mulhouse"
    assert stage.location.postal_code == "68100"
    assert (stage.location.latitude, stage.location.longitude) == (47.7508, 7.3359)
    assert stage.published_at == datetime(2026, 9, 1)
    assert stage.valid_through == datetime(2026, 11, 30, 23, 59, 59, tzinfo=UTC)
    assert stage.description == "Vous rejoindrez l'équipe data.\n- SQL\n- Power BI"

    assert alternance.url == PAGE_URL  # no url in the data: the page itself
    assert alternance.kinds == {Kind.APPRENTICESHIP}
    assert alternance.remote is True
    assert alternance.company == "Acme"
    assert alternance.employment_types == ["FULL_TIME", "OTHER"]


def test_page_without_job_postings():
    assert extract_job_postings("<html><body>Rien ici</body></html>", PAGE_URL) == []


def test_html_to_text_keeps_structure():
    assert html_to_text("<h2>Missions</h2><p>Analyser<br>Restituer</p><ul><li>A</li></ul>") == (
        "Missions\nAnalyser\nRestituer\n- A"
    )


# --- ATS feeds ----------------------------------------------------------------------------


def test_detect_boards():
    html = """
      <a href="https://jobs.lever.co/acme/a1b2">x</a>
      <a href="https://jobs.eu.lever.co/beta">x</a>
      <iframe src="https://boards.greenhouse.io/embed/job_board?for=gamma"></iframe>
      <a href="https://job-boards.greenhouse.io/delta/jobs/1">x</a>
      <a href="https://jobs.smartrecruiters.com/Epsilon/123">x</a>
      <a href="https://zeta.recruitee.com/o/stage">x</a>
      <script src="https://cdn.recruitee.com/widget.js"></script>
    """
    assert set(detect_boards(html)) == {
        Board("lever", "acme"),
        Board("lever", "beta", "eu"),
        Board("greenhouse", "gamma"),
        Board("greenhouse", "delta"),
        Board("smartrecruiters", "epsilon"),
        Board("recruitee", "zeta"),
    }
    assert detect_boards(PAGE) == [Board("lever", "acme")]


def test_feed_urls():
    assert (
        Board("lever", "beta", "eu").feed_url
        == "https://api.eu.lever.co/v0/postings/beta?mode=json"
    )
    assert Board("recruitee", "zeta").feed_url == "https://zeta.recruitee.com/api/offers/"


def _fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_greenhouse():
    board = Board("greenhouse", "acme")
    offers = fetch_board(board, lambda url: _fixture("greenhouse.json"))
    assert len(offers) == 1  # the job without a title is dropped
    (o,) = offers
    assert (o.source, o.source_id) == ("ats:greenhouse", "acme:101")
    assert o.kinds == {Kind.INTERNSHIP}
    assert o.location.city == "Strasbourg, France"
    assert o.description == "Build pipelines."
    assert o.published_at.date().isoformat() == "2026-09-01"


def test_lever():
    offers = fetch_board(Board("lever", "acme"), lambda url: _fixture("lever.json"))
    cdi, week_end = offers
    assert cdi.url == "https://jobs.lever.co/acme/a1b2"
    assert cdi.kinds == {Kind.JOB}
    assert cdi.description == "Rejoignez-nous.\n\nProfil\n- SQL\n- Excel\n\nTélétravail partiel."
    assert cdi.remote is False  # hybrid is not full remote
    assert cdi.published_at == datetime(2025, 9, 1, 8, 0, tzinfo=UTC)
    assert week_end.kinds == {Kind.JOB, Kind.STUDENT_JOB}


def test_smartrecruiters():
    (o,) = fetch_board(
        Board("smartrecruiters", "acme"), lambda url: _fixture("smartrecruiters.json")
    )
    assert o.url == "https://jobs.smartrecruiters.com/acme/7440001"
    assert o.company == "Acme SAS"
    assert o.kinds == {Kind.INTERNSHIP}
    assert o.location.country == "FR"
    assert o.location.has_point


def test_recruitee():
    (o,) = fetch_board(Board("recruitee", "acme"), lambda url: _fixture("recruitee.json"))
    assert o.kinds == {Kind.JOB, Kind.STUDENT_JOB}
    assert o.description == "12h par semaine.\n\nSouriant(e)."
    assert o.published_at == datetime(2026, 9, 12, 8, 30, tzinfo=UTC)


# --- polite fetcher -----------------------------------------------------------------------


def make_fetcher(robots: dict[str, tuple[int, str]]):
    requested: list[str] = []
    slept: list[float] = []
    now = [100.0]

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if request.url.path == "/robots.txt":
            status, body = robots[request.url.host]
            return httpx.Response(status, text=body)
        return httpx.Response(200, text="<html>ok</html>")

    fetcher = PoliteFetcher(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda s: (slept.append(s), now.__setitem__(0, now[0] + s)),
        clock=lambda: now[0],
    )
    return fetcher, requested, slept


def test_robots_rules_are_obeyed():
    robots = (
        "User-agent: *\nDisallow: /private/\n\n"
        "User-agent: JobRadar\nDisallow: /admin/\nCrawl-delay: 5"
    )
    fetcher, requested, slept = make_fetcher({"a.example": (200, robots)})
    assert fetcher.get("https://a.example/jobs").status_code == 200
    with pytest.raises(Disallowed):
        fetcher.get("https://a.example/admin/jobs")
    fetcher.get("https://a.example/jobs/2")
    assert slept == [5.0]  # the site's Crawl-delay, not our 2 s default
    assert requested.count("https://a.example/robots.txt") == 1  # read once per host
    assert "https://a.example/admin/jobs" not in requested


def test_missing_robots_allows_and_server_error_blocks():
    fetcher, requested, _ = make_fetcher({"a.example": (404, ""), "b.example": (503, "")})
    assert fetcher.allowed("https://a.example/carrieres")
    assert not fetcher.allowed("https://b.example/carrieres")
    with pytest.raises(Disallowed):
        fetcher.get("https://b.example/carrieres")
    assert "https://b.example/carrieres" not in requested
