"""Read job offers from the schema.org `JobPosting` data companies embed in their pages.

Companies add this JSON-LD so that search engines can list their offers: it is published to be
read by machines. Pages are messy in practice (several scripts, `@graph` wrappers, lists,
HTML inside strings, the odd trailing comma), so parsing is deliberately forgiving.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from collections.abc import Iterator
from datetime import datetime
from typing import Any

from bs4 import BeautifulSoup

from ...models import Location, Offer, classify

SOURCE = "careers:jsonld"

_TRAILING_COMMA = re.compile(r",\s*([}\]])")


def html_to_text(value: str) -> str:
    """Offer descriptions are HTML (sometimes escaped twice). Keep paragraphs and list items."""
    if not value:
        return ""
    value = html.unescape(value) if "&lt;" in value else value
    soup = BeautifulSoup(value, "html.parser")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for li in soup.find_all("li"):
        li.insert_before("\n- ")
    for block in soup.find_all(["p", "div", "h1", "h2", "h3", "h4", "ul", "ol"]):
        block.insert_after("\n")
    text = soup.get_text()
    lines = (re.sub(r"[ \t\xa0]+", " ", line).strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def _load(raw: str) -> Any:
    raw = raw.strip().removeprefix("<!--").removesuffix("-->").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return json.loads(_TRAILING_COMMA.sub(r"\1", raw))


def _walk(node: Any) -> Iterator[dict]:
    """Every JSON object in the document, depth first (covers @graph, lists, nesting)."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _is_job_posting(node: dict) -> bool:
    types = node.get("@type")
    types = types if isinstance(types, list) else [types]
    return "JobPosting" in types


def _first(value: Any) -> Any:
    return value[0] if isinstance(value, list) and value else value


def _text(value: Any) -> str:
    """A plain string from a JSON-LD value. Sites often leave HTML entities in titles."""
    if isinstance(value, dict):
        value = value.get("name") or value.get("@value") or ""
    return html.unescape(str(value)).strip() if value is not None else ""


def _date(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _location(node: dict) -> Location:
    place = _first(node.get("jobLocation")) or {}
    if not isinstance(place, dict):
        return Location()
    address = place.get("address") or {}
    if isinstance(address, str):
        return Location(city=address.strip())
    geo = place.get("geo") or {}
    return Location(
        city=_text(address.get("addressLocality")),
        postal_code=_text(address.get("postalCode")),
        country=_text(address.get("addressCountry")),
        latitude=_float(geo.get("latitude")),
        longitude=_float(geo.get("longitude")),
        precision="exact" if geo.get("latitude") and geo.get("longitude") else "",
    )


def _employment_types(node: dict) -> list[str]:
    value = node.get("employmentType") or []
    values = value if isinstance(value, list) else re.split(r"\s*,\s*", str(value))
    return [v.strip() for v in values if str(v).strip()]


def to_offer(node: dict, page_url: str) -> Offer | None:
    title = _text(node.get("title"))
    if not title:
        return None
    url = _text(node.get("url")) or page_url
    identifier = node.get("identifier")
    if isinstance(identifier, dict):
        identifier = identifier.get("value")
    source_id = _text(identifier) or hashlib.sha256(f"{url}|{title}".encode()).hexdigest()[:16]
    types = _employment_types(node)
    remote = None
    if node.get("jobLocationType"):
        remote = "TELECOMMUTE" in str(node.get("jobLocationType")).upper()
    return Offer(
        source=SOURCE,
        source_id=source_id,
        url=url,
        title=title,
        company=_text(_first(node.get("hiringOrganization"))),
        description=html_to_text(_text(node.get("description"))),
        kinds=classify(title, types, part_time="PART_TIME" in (t.upper() for t in types)),
        employment_types=types,
        location=_location(node),
        remote=remote,
        published_at=_date(node.get("datePosted")),
        valid_through=_date(node.get("validThrough")),
    )


def extract_job_postings(page_html: str, page_url: str) -> list[Offer]:
    """All JobPosting offers found in a page's JSON-LD scripts. Unreadable scripts are skipped."""
    soup = BeautifulSoup(page_html, "html.parser")
    offers: list[Offer] = []
    seen: set[str] = set()
    for script in soup.find_all("script", type=re.compile(r"application/ld\+json", re.I)):
        try:
            data = _load(script.string or script.get_text())
        except (json.JSONDecodeError, TypeError):
            continue
        for node in _walk(data):
            if not _is_job_posting(node):
                continue
            offer = to_offer(node, page_url)
            if offer and offer.source_id not in seen:
                seen.add(offer.source_id)
                offers.append(offer)
    return offers
