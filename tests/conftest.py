from pathlib import Path

import pytest

from spashta.embed import HashingEmbedder
from spashta.fetch import Fetcher, FetchError, Response
from spashta.retrieve import Index
from spashta.samples import sample_articles
from spashta.store import Store

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeWeb:
    """A transport serving canned pages; anything else is a 404."""

    def __init__(self, pages: dict[str, tuple[str, str]]):
        self.pages = pages  # url -> (content_type, body)
        self.requests: list[str] = []

    def __call__(self, url, headers):
        self.requests.append(url)
        assert headers["User-Agent"].startswith("SpashtaBot")
        if url not in self.pages:
            return Response(url, 404, {}, b"")
        ctype, body = self.pages[url]
        return Response(url, 200, {"content-type": ctype}, body.encode("utf-8"))


@pytest.fixture
def fake_web():
    def make(pages):
        web = FakeWeb(pages)
        return web, Fetcher(transport=web, delay_s=0, allow_private=True)
    return make


@pytest.fixture
def sample_index():
    index = Index(Store(":memory:"), HashingEmbedder())
    index.add_many(sample_articles())
    return index


__all__ = ["FetchError", "fixture"]
