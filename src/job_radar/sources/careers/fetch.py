"""HTTP access to company websites that follows the rules each site publishes.

- robots.txt is read once per host and obeyed: a disallowed page is never requested.
  No robots.txt (404) means everything is allowed. If robots.txt is refused (401/403) we keep
  out of the whole site. If it cannot be read (5xx, network error) we don't request anything
  and try reading it again next time.
- Requests to the same host are spaced out (the site's Crawl-delay if it sets one).
- The User-Agent names the project and links to it, so a site owner knows who is reading.
"""

from __future__ import annotations

import time
import urllib.robotparser
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

USER_AGENT = "JobRadar/0.1 (+https://github.com/Moundirhzr2/job-radar)"
ROBOTS_AGENT = "JobRadar"
DEFAULT_DELAY = 2.0  # seconds between two requests to the same host
MAX_BYTES = 3_000_000


class Disallowed(Exception):
    """The site's robots.txt does not allow this URL."""


@dataclass
class _Host:
    robots: urllib.robotparser.RobotFileParser | None  # None: robots.txt unreachable -> blocked
    delay: float
    last_request: float = 0.0


@dataclass
class PoliteFetcher:
    client: httpx.Client = field(
        default_factory=lambda: httpx.Client(
            headers={"User-Agent": USER_AGENT}, timeout=20.0, follow_redirects=True
        )
    )
    default_delay: float = DEFAULT_DELAY
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    _hosts: dict[str, _Host] = field(default_factory=dict)

    def _host(self, url: str) -> _Host:
        parts = urlsplit(url)
        key = f"{parts.scheme}://{parts.netloc}"
        host = self._hosts.get(key)
        if host is None:
            host = self._load_robots(key)
            if host.robots is not None:  # an unreadable robots.txt is retried next time
                self._hosts[key] = host
        return host

    def _load_robots(self, base: str) -> _Host:
        parser = urllib.robotparser.RobotFileParser()
        try:
            resp = self.client.get(f"{base}/robots.txt")
        except httpx.HTTPError:
            return _Host(robots=None, delay=self.default_delay)
        if resp.status_code >= 500:
            return _Host(robots=None, delay=self.default_delay)
        if resp.status_code in (401, 403):
            parser.disallow_all = True  # access to robots.txt itself refused: keep out
        # Any other 4xx (usually 404): there is no robots.txt, everything is allowed.
        parser.parse(resp.text.splitlines() if resp.status_code < 400 else [])
        crawl_delay = parser.crawl_delay(ROBOTS_AGENT)
        delay = max(self.default_delay, float(crawl_delay or 0))
        return _Host(robots=parser, delay=delay)

    def allowed(self, url: str) -> bool:
        host = self._host(url)
        return host.robots is not None and host.robots.can_fetch(ROBOTS_AGENT, url)

    def get(self, url: str) -> httpx.Response:
        host = self._host(url)
        if host.robots is None or not host.robots.can_fetch(ROBOTS_AGENT, url):
            raise Disallowed(url)
        wait = host.last_request + host.delay - self.clock()
        if wait > 0:
            self.sleep(wait)
        host.last_request = self.clock()
        resp = self.client.get(url)
        if len(resp.content) > MAX_BYTES:
            raise ValueError(f"response too large: {url}")
        return resp
