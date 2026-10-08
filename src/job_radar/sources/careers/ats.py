"""Public job feeds of the recruiting software (ATS) many companies use for their careers pages.

These feeds exist so that a company's offers can be displayed elsewhere (its own site, job
sites, aggregators). We detect which tool a careers page is built on, then read that tool's
public feed instead of the page itself: the data is complete and structured, and nothing is
scraped. Not used: feeds that need a key (Teamtailor, Workday...) and SmartRecruiters, whose
API robots.txt only admits LinkedIn's crawler. For those companies, only the JobPosting data
on their own careers pages is read.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ...models import Location, Offer, classify, weekly_hours
from .jsonld import html_to_text

GetJson = Callable[[str], Any]


@dataclass(frozen=True)
class Board:
    provider: str  # greenhouse | lever | recruitee
    identifier: str  # the company's name on that provider
    region: str = ""  # "eu" for the EU instances of Greenhouse and Lever

    @property
    def feed_url(self) -> str:
        if self.provider == "greenhouse":
            return f"https://boards-api.greenhouse.io/v1/boards/{self.identifier}/jobs?content=true"
        if self.provider == "lever":
            host = "api.eu.lever.co" if self.region == "eu" else "api.lever.co"
            return f"https://{host}/v0/postings/{self.identifier}?mode=json"
        if self.provider == "recruitee":
            return f"https://{self.identifier}.recruitee.com/api/offers/"
        raise ValueError(self.provider)


_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("greenhouse", re.compile(r"greenhouse\.io/embed/job_board(?:/js)?\?for=([\w-]+)", re.I)),
    (
        "greenhouse",
        re.compile(r"(?:job-)?boards(\.eu)?\.greenhouse\.io/(?!embed\b)([\w-]+)", re.I),
    ),
    ("lever", re.compile(r"jobs(\.eu)?\.lever\.co/([\w.-]+)", re.I)),
    ("recruitee", re.compile(r"\b([a-z0-9][a-z0-9-]*)\.recruitee\.com", re.I)),
]
_NOT_COMPANIES = {"www", "app", "api", "cdn", "help", "blog", "docs", "support", "status"}


def detect_boards(page_html: str) -> list[Board]:
    """ATS boards referenced by a careers page (links, iframes, embed scripts)."""
    found: dict[tuple[str, str], Board] = {}
    for provider, pattern in _PATTERNS:
        for m in pattern.finditer(page_html):
            groups = [g for g in m.groups()]
            region = "eu" if groups[0] and groups[0].lower() == ".eu" else ""
            identifier = groups[-1].strip(".").lower()
            if not identifier or identifier in _NOT_COMPANIES:
                continue
            found.setdefault((provider, identifier), Board(provider, identifier, region))
    return list(found.values())


def _ms_date(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=UTC)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _iso_date(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    value = value.strip().replace(" UTC", "+00:00").replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_greenhouse(data: dict, board: Board) -> list[Offer]:
    offers = []
    for job in data.get("jobs") or []:
        title = (job.get("title") or "").strip()
        if not title:
            continue
        description = html_to_text(job.get("content") or "")
        hours = weekly_hours(description)
        offers.append(
            Offer(
                source="ats:greenhouse",
                source_id=f"{board.identifier}:{job.get('id')}",
                url=job.get("absolute_url") or "",
                title=title,
                company=job.get("company_name") or "",
                description=description,
                kinds=classify(title, [], weekly_hours=hours),
                weekly_hours=hours,
                location=Location(city=((job.get("location") or {}).get("name") or "")),
                published_at=_iso_date(job.get("first_published") or job.get("updated_at")),
            )
        )
    return offers


def parse_lever(data: list | dict, board: Board) -> list[Offer]:
    if not isinstance(data, list):  # {"ok": false, "error": "Document not found"}
        return []
    offers = []
    for post in data:
        title = (post.get("text") or "").strip()
        if not title:
            continue
        categories = post.get("categories") or {}
        commitment = categories.get("commitment") or ""
        types = [commitment] if commitment else []
        sections = [post.get("descriptionPlain") or ""]
        for block in post.get("lists") or []:
            sections.append(f"{block.get('text', '')}\n{html_to_text(block.get('content', ''))}")
        sections.append(post.get("additionalPlain") or "")
        workplace = (post.get("workplaceType") or "").lower()
        description = "\n\n".join(s.strip() for s in sections if s and s.strip())
        hours = weekly_hours(description)
        offers.append(
            Offer(
                source="ats:lever",
                source_id=f"{board.identifier}:{post.get('id')}",
                url=post.get("hostedUrl") or post.get("applyUrl") or "",
                title=title,
                description=description,
                kinds=classify(
                    title, types, part_time="part" in commitment.lower(), weekly_hours=hours
                ),
                weekly_hours=hours,
                employment_types=types,
                location=Location(city=categories.get("location") or ""),
                remote=True if workplace == "remote" else (False if workplace else None),
                published_at=_ms_date(post.get("createdAt")),
            )
        )
    return offers


def parse_recruitee(data: dict, board: Board) -> list[Offer]:
    offers = []
    for post in data.get("offers") or []:
        title = (post.get("title") or "").strip()
        if not title:
            continue
        code = post.get("employment_type_code") or ""
        types = [code] if code else []
        description = "\n\n".join(
            html_to_text(post.get(k) or "") for k in ("description", "requirements")
        ).strip()
        # Recruitee publishes the contract's hours when the employer fills them in
        published = post.get("min_hours_per_week") or post.get("max_hours_per_week")
        hours = float(published) if published else weekly_hours(description)
        offers.append(
            Offer(
                source="ats:recruitee",
                source_id=f"{board.identifier}:{post.get('id')}",
                url=post.get("careers_url") or "",
                title=title,
                company=post.get("company_name") or "",
                description=description,
                kinds=classify(title, types, part_time="part" in code.lower(), weekly_hours=hours),
                weekly_hours=hours,
                employment_types=types,
                location=Location(
                    city=post.get("city") or "",
                    postal_code=post.get("postal_code") or "",
                    country=(post.get("country_code") or "").upper(),
                ),
                remote=post.get("remote"),
                published_at=_iso_date(post.get("published_at")),
            )
        )
    return offers


_PARSERS = {
    "greenhouse": parse_greenhouse,
    "lever": parse_lever,
    "recruitee": parse_recruitee,
}


def fetch_board(board: Board, get_json: GetJson) -> list[Offer]:
    """Read one board's feed. `get_json(url)` does the HTTP call (polite fetcher in production)."""
    return _PARSERS[board.provider](get_json(board.feed_url), board)
