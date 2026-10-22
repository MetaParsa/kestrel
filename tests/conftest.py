import json
from pathlib import Path

import httpx
import pytest

from wallex.client import WallexClient

FIX = Path(__file__).parent / "fixtures"
FAKE_KEY = "test-key-0000000000000000000000000000"


def fixture(name: str) -> dict:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self):
        return self.now

    def sleep(self, s):
        self.slept.append(s)
        self.now += s


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def make_client(clock):
    """Client whose HTTP calls are answered by `routes`: {(method, path): response or [responses]}."""
    def _make(routes: dict, *, key: str | None = FAKE_KEY, seen: list | None = None) -> WallexClient:
        queues = {k: (list(v) if isinstance(v, list) else [v]) for k, v in routes.items()}

        def handler(request: httpx.Request) -> httpx.Response:
            if seen is not None:
                seen.append(request)
            q = queues.get((request.method, request.url.path))
            if not q:
                return httpx.Response(404, json={"message": "Not Found", "success": False})
            item = q.pop(0) if len(q) > 1 else q[0]
            if isinstance(item, httpx.Response):
                return item
            return httpx.Response(200, json=item)

        return WallexClient(api_key=key, transport=httpx.MockTransport(handler),
                            sleep=clock.sleep, clock=clock, min_interval=0.25)
    return _make
