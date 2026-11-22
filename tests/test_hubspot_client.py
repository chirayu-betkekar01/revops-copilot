"""HubSpot client tests against a fake HubSpot (httpx.MockTransport). No network, no real key."""

import json
from collections.abc import Callable

import httpx
import pytest

from seed.hubspot_client import (
    MAX_ATTEMPTS,
    HubSpotClient,
    HubSpotError,
    RateLimitedError,
    backoff_delay,
)

Handler = Callable[[httpx.Request], httpx.Response]


def make_client(handler: Handler, sleeps: list[float] | None = None) -> HubSpotClient:
    """Client wired to a fake HubSpot. Waits are recorded in `sleeps` instead of slept."""
    record = sleeps if sleeps is not None else []
    return HubSpotClient("test-token", transport=httpx.MockTransport(handler), sleep=record.append)


def test_sends_bearer_token() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["Authorization"])
        return httpx.Response(200, json={})

    make_client(handler).get("/anything")

    assert seen == ["Bearer test-token"]


def test_batch_create_splits_into_chunks_of_100() -> None:
    batch_sizes: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        inputs = json.loads(request.content)["inputs"]
        batch_sizes.append(len(inputs))
        return httpx.Response(201, json={"results": [{"id": "1"} for _ in inputs]})

    results = make_client(handler).batch_create("deals", [{"properties": {}}] * 250)

    assert batch_sizes == [100, 100, 50]
    assert len(results) == 250


def test_429_then_success_retries_and_returns_result() -> None:
    responses = iter([429, 429, 200])
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        status = next(responses)
        return httpx.Response(status, json={"ok": True} if status == 200 else {})

    assert make_client(handler, sleeps).get("/anything") == {"ok": True}
    assert len(sleeps) == 2  # waited before each of the 2 retries


def test_backoff_waits_grow_exponentially() -> None:
    # With jitter each wait is 50-100% of 1, 2, 4, 8s: always inside these ranges.
    for retry, (low, high) in enumerate([(0.5, 1), (1, 2), (2, 4), (4, 8)]):
        assert low <= backoff_delay(retry, retry_after=None) <= high


def test_backoff_trusts_retry_after_header() -> None:
    sleeps: list[float] = []
    responses = iter([429, 200])

    def handler(request: httpx.Request) -> httpx.Response:
        if next(responses) == 429:
            return httpx.Response(429, headers={"Retry-After": "3"})
        return httpx.Response(200, json={})

    make_client(handler, sleeps).get("/anything")

    assert 3 <= sleeps[0] <= 3.5


def test_gives_up_after_max_attempts() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(429, headers={"Retry-After": "2"}, json={"message": "slow down"})

    with pytest.raises(RateLimitedError) as exc_info:
        make_client(handler).get("/anything")

    assert len(calls) == MAX_ATTEMPTS
    assert exc_info.value.retry_after == 2.0


def test_partial_batch_failure_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(207, json={"results": [], "errors": [{"message": "bad amount"}]})

    with pytest.raises(HubSpotError, match="bad amount"):
        make_client(handler).batch_create("deals", [{"properties": {}}])


def test_check_can_write_creates_then_deletes() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.method == "POST":
            return httpx.Response(201, json={"id": "123"})
        return httpx.Response(204)

    make_client(handler).check_can_write("notes", {"hs_note_body": "x"})

    assert calls == ["POST /crm/v3/objects/notes", "DELETE /crm/v3/objects/notes/123"]


def test_check_can_write_raises_on_missing_scope() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "missing scopes"})

    with pytest.raises(HubSpotError) as exc_info:
        make_client(handler).check_can_write("notes", {"hs_note_body": "x"})

    assert exc_info.value.status == 403


def test_search_ids_follows_the_cursor() -> None:
    pages = iter(
        [
            {"results": [{"id": "1"}, {"id": "2"}], "paging": {"next": {"after": "2"}}},
            {"results": [{"id": "3"}]},
        ]
    )
    afters: list[object] = []

    def handler(request: httpx.Request) -> httpx.Response:
        afters.append(json.loads(request.content).get("after"))
        return httpx.Response(200, json=next(pages))

    assert make_client(handler).search_ids("deals", "seed_batch", "seed-v1") == ["1", "2", "3"]
    assert afters == [None, "2"]


def test_associated_ids_collects_linked_records() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/crm/v4/associations/deals/notes/batch/read"
        return httpx.Response(
            200,
            json={
                "results": [
                    {"from": {"id": "d1"}, "to": [{"toObjectId": 11}, {"toObjectId": 12}]},
                    {"from": {"id": "d2"}, "to": [{"toObjectId": 12}]},
                ]
            },
        )

    assert make_client(handler).associated_ids("deals", "notes", ["d1", "d2"]) == ["11", "12"]


def test_batch_archive_sends_ids_in_chunks() -> None:
    sizes: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sizes.append(len(json.loads(request.content)["inputs"]))
        return httpx.Response(204)

    make_client(handler).batch_archive("deals", [str(i) for i in range(150)])

    assert sizes == [100, 50]


def test_create_property_returns_false_when_it_already_exists() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"message": "Property already exists"})

    assert make_client(handler).create_property("deals", {"name": "rep_id"}) is False
