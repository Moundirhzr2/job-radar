"""Apprenticeship offers from La bonne alternance (API Apprentissage), for all of France.

Access: a free account on api.apprentissage.beta.gouv.fr; the key is read from LBA_API_TOKEN,
never stored in the repository. Reading offers needs no habilitation. Terms of use: no resale
of the data and no sharing with third parties; this project uses it for one student, locally.

The export route lists every opportunity once a day (3:00, Paris time) in a single JSON file
of about 600 MB: published offers from La bonne alternance and its partners (Hellowork, Direct
Emploi, APEC…), the France Travail offers the radar already reads from France Travail itself,
and some 290,000 companies likely to hire an apprentice without an offer ("recruteurs_lba").
The file is read one entry at a time, so it never has to fit in memory as a whole.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

from ..http import ApiClient, default_client
from ..models import Kind, Location, Offer, weekly_hours

API = "https://api.apprentissage.beta.gouv.fr/api"
SOURCE = "la_bonne_alternance"
# Read elsewhere, or not an offer: France Travail offers come from France Travail directly.
SKIPPED_PARTNERS = {"France Travail", "recruteurs_lba"}
SCHOOL = "géré par une école"  # is_delegated: the offer is managed by a partner school


class TokenMissing(Exception):
    pass


def read_token() -> str:
    token = os.environ.get("LBA_API_TOKEN", "").strip()
    if not token:
        raise TokenMissing(
            "Clé La bonne alternance absente : définir LBA_API_TOKEN dans les variables "
            "d'environnement (compte gratuit sur api.apprentissage.beta.gouv.fr)."
        )
    return token


def download_export(target: Path, client: httpx.Client | None = None) -> datetime | None:
    """Save today's export to `target`; returns the date La bonne alternance built it."""
    client = client or default_client()
    api = ApiClient(API, min_interval=30.0, client=client)  # 2 calls a minute at most
    meta = api.get("/job/v1/export", headers={"Authorization": f"Bearer {read_token()}"})
    target.parent.mkdir(parents=True, exist_ok=True)
    with client.stream("GET", meta["url"], timeout=600) as resp:  # a signed link, 2 minutes
        resp.raise_for_status()
        with target.open("wb") as out:
            for chunk in resp.iter_bytes(1 << 20):
                out.write(chunk)
    return _date(meta.get("lastUpdate"))


def entries(path: Path, chunk: int = 1 << 22) -> Iterator[dict]:
    """The objects of a JSON array file, one at a time."""
    decoder = json.JSONDecoder()
    with path.open(encoding="utf-8") as f:
        buffer, started = "", False
        for block in iter(lambda: f.read(chunk), ""):
            buffer += block
            pos = 0
            while True:
                while pos < len(buffer) and buffer[pos] in " \t\r\n,[]":
                    started = started or buffer[pos] == "["
                    pos += 1
                if pos >= len(buffer) or not started:
                    break
                try:
                    item, end = decoder.raw_decode(buffer, pos)
                except json.JSONDecodeError:
                    break  # an object cut by the end of the block: read more
                yield item
                pos = end
            buffer = buffer[pos:]


def _date(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None
    except ValueError:
        return None


def _text(html: str) -> str:
    if "<" not in html:
        return html.strip()
    text = BeautifulSoup(html, "html.parser").get_text("\n")
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def _place(location: dict | None) -> Location:
    location = location or {}
    address = (location.get("address") or "").strip()
    town = re.search(r"(\d{5})\s+([^,\d][^,]*)$", address)
    lon, lat = ((location.get("geopoint") or {}).get("coordinates") or [None, None])[:2]
    return Location(
        city=town.group(2).strip() if town else "",
        postal_code=town.group(1) if town else "",
        country="FR",
        latitude=lat,
        longitude=lon,
        precision="exact" if lat is not None and lon is not None else "",
    )


def to_offer(raw: dict) -> Offer | None:
    """A published apprenticeship offer, or None for what is not one to add here."""
    ident = raw.get("identifier") or {}
    if ident.get("partner_label") in SKIPPED_PARTNERS:
        return None
    offer, workplace = raw.get("offer") or {}, raw.get("workplace") or {}
    contract, apply = raw.get("contract") or {}, raw.get("apply") or {}
    url = apply.get("url") or ""
    if not url or (offer.get("status") or "Active") != "Active":
        return None
    description = _text(offer.get("description") or "")
    types = list(contract.get("type") or [])
    if ident.get("partner_label"):
        types.append(f"via {ident['partner_label']}")
    if raw.get("is_delegated"):
        types.append(SCHOOL)
    publication = offer.get("publication") or {}
    return Offer(
        source=SOURCE,
        source_id=ident.get("id") or f"{ident.get('partner_label')}:{ident.get('partner_job_id')}",
        url=url,
        title=(offer.get("title") or "").strip(),
        company=(workplace.get("name") or workplace.get("brand") or "").strip(),
        description=description,
        kinds=frozenset({Kind.APPRENTICESHIP}),
        employment_types=types,
        location=_place(workplace.get("location")),
        remote=None if not contract.get("remote") else contract["remote"] != "onsite",
        published_at=_date(publication.get("creation")),
        valid_through=_date(publication.get("expiration")),
        weekly_hours=weekly_hours(description),
    )


def offers(path: Path) -> Iterator[Offer]:
    for raw in entries(path):
        try:
            found = to_offer(raw)
        except (KeyError, TypeError, AttributeError):  # one malformed entry must not stop the rest
            continue
        if found is not None:
            yield found


__all__ = ["SOURCE", "TokenMissing", "download_export", "entries", "offers", "to_offer"]
