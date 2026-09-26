import gzip

import httpx

from job_radar.sources.careers.fetch import PoliteFetcher, parse_content_signals
from job_radar.sources.careers.site import read_career_site


def job_page(title):
    return f"""<html><head><script type="application/ld+json">
    {{"@type": "JobPosting", "title": "{title}", "hiringOrganization": "Acme"}}
    </script></head></html>"""


def site(routes: dict[str, httpx.Response]):
    requested = []

    def handler(request):
        url = str(request.url)
        requested.append(url)
        return routes.get(url, httpx.Response(404))

    fetcher = PoliteFetcher(
        client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda s: None
    )
    return fetcher, requested


ROBOTS = (
    "User-agent: *\nDisallow: /jobs/interne/\n"
    "Content-Signal: search=yes, ai-train=no, ai-input=no\n"
    "Sitemap: https://acme.example/sitemap_index.xml\n"
)
INDEX = """<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://acme.example/sitemap-jobs.xml.gz</loc></sitemap></sitemapindex>"""
JOBS = """<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://acme.example/jobs/stage-data</loc></url>
  <url><loc>https://acme.example/jobs/alternance-bi</loc></url>
  <url><loc>https://acme.example/jobs/interne/secret</loc></url>
  <url><loc>https://acme.example/a-propos</loc></url>
  <url><loc>https://other.example/jobs/not-ours</loc></url>
</urlset>"""


def test_follows_sitemaps_to_job_pages_and_obeys_robots():
    fetcher, requested = site(
        {
            "https://acme.example/robots.txt": httpx.Response(200, text=ROBOTS),
            "https://acme.example/carrieres": httpx.Response(200, text="<html>Nos offres</html>"),
            "https://acme.example/sitemap_index.xml": httpx.Response(200, text=INDEX),
            "https://acme.example/sitemap-jobs.xml.gz": httpx.Response(
                200, content=gzip.compress(JOBS.encode())
            ),
            "https://acme.example/jobs/stage-data": httpx.Response(
                200, text=job_page("Stage Data")
            ),
            "https://acme.example/jobs/alternance-bi": httpx.Response(
                200, text=job_page("Alternance BI &amp; Reporting")
            ),
        }
    )
    report = read_career_site(fetcher, "https://acme.example/carrieres")
    assert [o.title for o in report.offers] == ["Stage Data", "Alternance BI & Reporting"]
    assert report.skipped == ["https://acme.example/jobs/interne/secret"]
    assert "https://acme.example/jobs/interne/secret" not in requested
    assert "https://acme.example/a-propos" not in requested  # not a job page
    assert not any(u.startswith("https://other.example") for u in requested)
    assert not fetcher.allows_ai_input("https://acme.example/carrieres")


def test_prefers_the_ats_feed():
    feed = '{"offers": [{"id": 1, "title": "Stage BI", "careers_url": "https://acme.recruitee.com/o/1"}]}'
    fetcher, requested = site(
        {
            "https://acme.example/robots.txt": httpx.Response(404),
            "https://acme.example/carrieres": httpx.Response(
                200, text='<iframe src="https://acme.recruitee.com/"></iframe>'
            ),
            "https://acme.recruitee.com/robots.txt": httpx.Response(200, text="User-agent: *\n"),
            "https://acme.recruitee.com/api/offers/": httpx.Response(200, text=feed),
        }
    )
    report = read_career_site(fetcher, "https://acme.example/carrieres")
    assert [o.title for o in report.offers] == ["Stage BI"]
    assert not any("sitemap" in u for u in requested)


def test_content_signals_prefer_our_own_group():
    lines = [
        "User-agent: *",
        "Content-Signal: ai-input=yes",
        "User-agent: JobRadar",
        "Content-Signal: ai-input=no",
    ]
    assert parse_content_signals(lines) == {"ai-input": "no"}
    assert parse_content_signals(["User-agent: *", "Disallow:"]) == {}
