from datetime import datetime, timezone
from spashta.fetch import Fetcher
from spashta.models import Source
from spashta.ingest.discover import backfill_items
f = Fetcher(delay_s=0.5)
for a, b in [((2026, 9, 26), (2026, 9, 28)), ((2026, 10, 20), (2026, 10, 22))]:
    since = datetime(*a, tzinfo=timezone.utc); until = datetime(*b, 23, 59, tzinfo=timezone.utc)
    for url in ["https://www.onlinekhabar.com", "https://english.onlinekhabar.com", "https://annapurnapost.com",
                "https://nepalnews.com", "https://myrepublica.nagariknetwork.com"]:
        items = backfill_items(Source(url=url), f, since, until)
        ds = [i.published for i in items if i.published]
        print("BF", a, url, len(items), min(ds) if ds else None, max(ds) if ds else None, items[0].url if items else "")
