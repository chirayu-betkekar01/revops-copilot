"""Step 2 break-it exercise: fire requests too fast and watch HubSpot push back with 429.

    just rate-limit-demo

Read-only: it only GETs one deal, over and over, from 10 threads at once. Nothing is created.
HubSpot allows about 100 requests per 10 seconds on a free/test account, so 200 fast requests
should trip the limit. Once you've added backoff to HubSpotClient.request, all 200 succeed.
"""

import time
from concurrent.futures import ThreadPoolExecutor

from seed.hubspot_client import HubSpotClient, RateLimitedError

N_REQUESTS = 200
THREADS = 10


def main() -> None:
    with HubSpotClient.from_env() as hs:

        def one_request(i: int) -> str:
            try:
                hs.get("/crm/v3/objects/deals?limit=1")
                return "ok"
            except RateLimitedError as exc:
                return f"429 (Retry-After: {exc.retry_after})"

        start = time.perf_counter()
        with ThreadPoolExecutor(max_workers=THREADS) as pool:
            results = list(pool.map(one_request, range(N_REQUESTS)))
        elapsed = time.perf_counter() - start

    ok = results.count("ok")
    print(f"{N_REQUESTS} requests in {elapsed:.1f}s: {ok} ok, {N_REQUESTS - ok} rate-limited")
    first_429 = next((i for i, r in enumerate(results) if r != "ok"), None)
    if first_429 is not None:
        print(f"First 429 at request #{first_429 + 1}: {results[first_429]}")


if __name__ == "__main__":
    main()
