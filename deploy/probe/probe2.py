import json
from datetime import datetime, timezone
from spashta.fetch import Fetcher
from spashta.ingest.sitemap import parse_sitemap
f = Fetcher(delay_s=0.5)
lo, hi = datetime(2026,9,26,tzinfo=timezone.utc), datetime(2026,11,26,tzinfo=timezone.utc)
kids = ["https://nagariknews.nagariknetwork.com/storage/sitemap_1.xml", "https://nagariknews.nagariknetwork.com/storage/sitemap_10.xml",
        "https://myrepublica.nagariknetwork.com/storage/sitemap_1.xml",
        "https://annapurnapost.com/sitemap-news.xml", "https://annapurnapost.com/sitemap-news.xml?p=2", "https://annapurnapost.com/sitemap-news.xml?p=1299",
        "https://english.onlinekhabar.com/post-sitemap1.xml", "https://english.onlinekhabar.com/post-sitemap34.xml",
        "https://nepallivetoday.com/wp-sitemap-posts-post-1.xml"]
for u in kids:
    try:
        sm = parse_sitemap(f.get(u).text)
    except Exception as e:
        print("KID", u, "ERROR", str(e)[:100]); continue
    ds = [e.published for e in sm.entries if e.published]
    inwin = sum(1 for d in ds if lo <= d <= hi)
    print("KID", u, f"entries={len(sm.entries)} children={len(sm.children)} dated={len(ds)} in_window={inwin}",
          "min", min(ds) if ds else None, "max", max(ds) if ds else None)
    for e in sm.entries[:2] + sm.entries[-2:]:
        print("KID    ", e.url, e.published)
for site in ["https://www.onlinekhabar.com", "https://english.onlinekhabar.com", "https://www.setopati.com", "https://www.ratopati.com",
             "https://nepallivetoday.com", "https://risingnepaldaily.com", "https://gorkhapatraonline.com", "https://ekantipur.com",
             "https://kathmandupost.com", "https://annapurnapost.com", "https://nagariknews.nagariknetwork.com"]:
    u = site + "/wp-json/wp/v2/posts?per_page=100&after=2026-09-26T00:00:00&before=2026-09-29T00:00:00&_fields=link,date"
    try:
        r = f.get(u)
        try:
            data = json.loads(r.text)
            print("WP", site, "posts 26-28 Sep:", len(data) if isinstance(data, list) else str(data)[:100], (data[:1] if isinstance(data, list) else ""))
        except ValueError:
            print("WP", site, "not json", r.url, r.text[:60].replace(chr(10), " "))
    except Exception as e:
        print("WP", site, "ERROR", str(e)[:100])
