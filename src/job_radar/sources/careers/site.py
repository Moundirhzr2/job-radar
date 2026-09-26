"""Read all the offers a company publishes on its own careers site.

In order of preference:
1. The careers page is built on an ATS with a public feed (Greenhouse, Lever, Recruitee):
   read the feed, which has every offer, complete and structured.
2. The careers page itself carries JobPosting data (some list pages embed all their offers).
3. The site's sitemap, which it publishes for search engines: follow it to the job pages and
   read the JobPosting data on each one.
Every request goes through the PoliteFetcher, so robots.txt and crawl delays always apply.
"""

from __future__ import annotations

import gzip
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

from ...models import Offer
from .ats import Board, detect_boards, fetch_board
from .fetch import Disallowed, PoliteFetcher
from .jsonld import extract_job_postings

# Paths that job pages use on careers sites (French and English).
_JOB_PATH = re.compile(
    r"/(jobs?|o|offres?|offre-d-emploi|emplois?|careers?|carrieres?|postes?|"
    r"recrutement|join-us|rejoignez-nous|positions?|vacancies)/[^/?#]+",
    re.I,
)
_MAX_SITEMAPS = 5


@dataclass
class SiteReport:
    offers: list[Offer] = field(default_factory=list)
    boards: list[Board] = field(default_factory=list)
    pages_read: int = 0
    skipped: list[str] = field(default_factory=list)  # URLs robots.txt kept us away from


def _xml(resp: httpx.Response) -> ET.Element | None:
    body = resp.content
    if body[:2] == b"\x1f\x8b":
        body = gzip.decompress(body)
    try:
        return ET.fromstring(body)
    except ET.ParseError:
        return None


def _locs(root: ET.Element) -> list[str]:
    return [el.text.strip() for el in root.iter() if el.tag.endswith("loc") and el.text]


def sitemap_job_urls(fetcher: PoliteFetcher, site_url: str, report: SiteReport) -> list[str]:
    """Job page URLs listed in the sitemaps the site declares (sitemap indexes followed)."""
    host = urlsplit(site_url).netloc
    queue = fetcher.sitemaps(site_url) or [f"{urlsplit(site_url).scheme}://{host}/sitemap.xml"]
    seen, urls = set(), []
    while queue and len(seen) < _MAX_SITEMAPS:
        sitemap = queue.pop(0)
        if sitemap in seen:
            continue
        seen.add(sitemap)
        try:
            resp = fetcher.get(sitemap)
        except Disallowed:
            report.skipped.append(sitemap)
            continue
        root = _xml(resp) if resp.status_code == 200 else None
        if root is None:
            continue
        if root.tag.endswith("sitemapindex"):
            queue.extend(_locs(root))
            continue
        for loc in _locs(root):
            if urlsplit(loc).netloc == host and _JOB_PATH.search(urlsplit(loc).path):
                urls.append(loc)
    return list(dict.fromkeys(urls))


def read_career_site(
    fetcher: PoliteFetcher, careers_url: str, max_job_pages: int = 50
) -> SiteReport:
    report = SiteReport()
    try:
        page = fetcher.get(careers_url)
    except Disallowed:
        report.skipped.append(careers_url)
        return report
    report.pages_read += 1

    report.boards = detect_boards(page.text)
    for board in report.boards:
        try:
            report.offers.extend(fetch_board(board, lambda url: fetcher.get(url).json()))
        except Disallowed:
            report.skipped.append(board.feed_url)
    if report.offers:
        return report

    report.offers = extract_job_postings(page.text, str(page.url))
    if report.offers:
        return report

    seen: set[str] = set()
    for url in sitemap_job_urls(fetcher, str(page.url), report)[:max_job_pages]:
        try:
            resp = fetcher.get(url)
        except Disallowed:
            report.skipped.append(url)
            continue
        report.pages_read += 1
        if resp.status_code != 200:
            continue
        for offer in extract_job_postings(resp.text, url):
            if offer.source_id not in seen:
                seen.add(offer.source_id)
                report.offers.append(offer)
    return report
