"""Thin HubSpot CRM API client: auth, batching, clear errors and retry-with-backoff on 429."""

import logging
import os
import random
import time
from collections.abc import Callable, Iterator, Sequence
from types import TracebackType
from typing import Any, Self

import httpx

BASE_URL = "https://api.hubapi.com"
BATCH_SIZE = 100  # max inputs per HubSpot batch request

# Backoff on 429: wait ~1s, ~2s, ~4s, ~8s, ~16s, then give up (6 attempts in total).
MAX_ATTEMPTS = 6
BASE_DELAY = 1.0
MAX_DELAY = 30.0

JSON = dict[str, Any]

logger = logging.getLogger(__name__)


class HubSpotError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"HubSpot returned {status}: {message}")
        self.status = status


class RateLimitedError(HubSpotError):
    """HTTP 429: too many requests. `retry_after` = HubSpot's suggested wait in seconds, if any."""

    def __init__(self, message: str, retry_after: float | None) -> None:
        super().__init__(429, message)
        self.retry_after = retry_after


def chunked(items: Sequence[JSON], size: int) -> Iterator[Sequence[JSON]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def parse_retry_after(value: str | None) -> float | None:
    """HubSpot sends Retry-After as seconds. Anything else (missing, a date) -> None."""
    try:
        return float(value) if value else None
    except ValueError:
        return None


def backoff_delay(retry: int, retry_after: float | None) -> float:
    """Seconds to wait before retry number `retry` (0 = first retry).

    Exponential: each wait doubles (1, 2, 4, 8, 16s), capped at MAX_DELAY.
    Jitter: each wait is randomly 50-100% of that, so 10 threads that all got a 429 at
    the same moment don't all retry at the same moment too (and get another 429 together).
    If HubSpot said how long to wait (Retry-After), trust it, plus a little jitter.
    """
    if retry_after is not None:
        return retry_after + random.uniform(0, 0.5)
    exponential = min(MAX_DELAY, BASE_DELAY * 2.0**retry)
    return exponential * random.uniform(0.5, 1.0)


class HubSpotClient:
    def __init__(
        self,
        token: str,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,  # tests pass a fake to avoid real waits
    ) -> None:
        # The token lives only in this header. Never print it, log it or put it in an error.
        self._http = httpx.Client(
            base_url=BASE_URL,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
            transport=transport,
        )
        self._sleep = sleep

    @classmethod
    def from_env(cls) -> Self:
        token = os.environ.get("HUBSPOT_SERVICE_KEY", "")
        if not token or token == "replace-me":
            raise SystemExit("HUBSPOT_SERVICE_KEY is not set. Put your service key in .env.")
        return cls(token)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._http.close()

    def request(self, method: str, path: str, json: JSON | None = None) -> JSON:
        # Retrying is safe here, even for POSTs that create records: a 429 means HubSpot
        # rejected the request without processing it, so nothing gets created twice.
        for retry in range(MAX_ATTEMPTS):
            resp = self._http.request(method, path, json=json)
            if resp.status_code != 429:
                break
            retry_after = parse_retry_after(resp.headers.get("Retry-After"))
            if retry == MAX_ATTEMPTS - 1:
                raise RateLimitedError(resp.text[:300], retry_after)
            delay = backoff_delay(retry, retry_after)
            logger.warning("Rate limited on %s %s, retrying in %.1fs", method, path, delay)
            self._sleep(delay)

        if resp.is_error:
            raise HubSpotError(resp.status_code, f"{method} {path}: {resp.text[:500]}")

        body: JSON = resp.json() if resp.content else {}
        # Batch endpoints answer 207 (multi-status) when SOME inputs failed. Treat as an error:
        # a half-created dataset is worse than none.
        if body.get("errors"):
            first = body["errors"][0].get("message", body["errors"][0])
            raise HubSpotError(resp.status_code, f"{method} {path}: {first}")
        return body

    def get(self, path: str) -> JSON:
        return self.request("GET", path)

    def post(self, path: str, json: JSON) -> JSON:
        return self.request("POST", path, json)

    def check_can_write(self, object_type: str, properties: JSON) -> None:
        """Create one throwaway record and delete it again. Raises HubSpotError (403) if the
        key lacks write permission, before the real upload creates anything."""
        record = self.post(f"/crm/v3/objects/{object_type}", {"properties": properties})
        self.request("DELETE", f"/crm/v3/objects/{object_type}/{record['id']}")

    def batch_create(self, object_type: str, inputs: Sequence[JSON]) -> list[JSON]:
        """Create records 100 at a time. Returns every created record (order NOT guaranteed)."""
        results: list[JSON] = []
        for chunk in chunked(inputs, BATCH_SIZE):
            body = self.post(f"/crm/v3/objects/{object_type}/batch/create", {"inputs": list(chunk)})
            results.extend(body["results"])
        return results

    def create_property(self, object_type: str, definition: JSON) -> bool:
        """Create a custom property. Returns False if it already existed (409 Conflict)."""
        try:
            self.post(f"/crm/v3/properties/{object_type}", definition)
        except HubSpotError as exc:
            if exc.status == 409:
                return False
            raise
        return True

    def count(self, object_type: str, property_name: str, value: str) -> int:
        """How many records have property_name == value."""
        body = self.post(
            f"/crm/v3/objects/{object_type}/search",
            {
                "filterGroups": [
                    {"filters": [{"propertyName": property_name, "operator": "EQ", "value": value}]}
                ],
                "limit": 1,
            },
        )
        return int(body["total"])
