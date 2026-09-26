"""A small JSON API client with pacing and retries, shared by the public APIs we call."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

USER_AGENT = "JobRadar/0.1 (+https://github.com/Moundirhzr2/job-radar)"
RETRY_STATUS = {429, 500, 502, 503, 504}


class ApiError(Exception):
    pass


def default_client() -> httpx.Client:
    return httpx.Client(timeout=30.0, headers={"User-Agent": USER_AGENT})


@dataclass
class ApiClient:
    base_url: str
    min_interval: float = 0.2  # seconds between two calls (the API's published rate limit)
    retries: int = 4
    client: httpx.Client = field(default_factory=default_client)
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    _last: float = 0.0

    def get(self, path: str, params: dict | None = None) -> Any:
        """GET a JSON resource; retries dropped connections, 429 and 5xx with backoff."""
        error: Exception | None = None
        for attempt in range(self.retries + 1):
            wait = self._last + self.min_interval - self.clock()
            if wait > 0:
                self.sleep(wait)
            self._last = self.clock()
            try:
                resp = self.client.get(f"{self.base_url}{path}", params=params)
            except httpx.TransportError as exc:
                error = exc
            else:
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code not in RETRY_STATUS:
                    raise ApiError(f"{path}: HTTP {resp.status_code}: {resp.text[:200]}")
                error = ApiError(f"{path}: HTTP {resp.status_code}")
                retry_after = resp.headers.get("Retry-After", "")
                if resp.status_code == 429 and retry_after.isdigit():
                    self.sleep(float(retry_after))
                    continue
            if attempt < self.retries:
                self.sleep(2**attempt)
        raise ApiError(f"{path}: failed after {self.retries + 1} attempts") from error
