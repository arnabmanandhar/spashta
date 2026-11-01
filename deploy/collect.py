"""
One collection run for the scheduled GitHub Actions workflow
(.github/workflows/collect.yml). The workflow restores the store before
this runs and saves it afterwards; this script only touches the file.

  1. Merge a seed store (an older live.db) if one was handed over.
  2. Backfill from sitemaps, a few days at a time, until the window from
     BACKFILL_FROM to today is covered. Progress is kept in the store, so
     a backfill that does not fit in one run carries on in the next.
  3. Poll every outlet's feed, as the 3-hourly local task did.
  4. Once a day, re-read the last two days of sitemaps to catch what the
     feeds missed.
  5. Check the store and refuse to save one that lost articles.

Usage: python deploy/collect.py DB [--seed SEED_DB] [--budget-min 240]
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess  # noqa: S404 - runs the spashta CLI only
import sys
import time
from datetime import date, datetime, timedelta, timezone

NEPAL = timezone(timedelta(hours=5, minutes=45))
STATE = "CREATE TABLE IF NOT EXISTS collector_state (key TEXT PRIMARY KEY, value TEXT)"
WINDOW_DAYS = 3
# Tables merged from a seed, keyed so existing rows win.
MERGE = {
    "sources": "url, name, lang, kind, endpoint, enabled, last_polled, last_error",
    "articles": "id, url, source, title, body, lang, published, author, summary, fetched_at",
    "chunks": "id, article_id, idx, text, lang, embedder, vector",
    "seen": "url, article_id, seen_at",
}


def spashta(db: str, *args: str) -> int:
    cmd = [sys.executable, "-m", "spashta", "--db", db, *args]
    print(f"::group::spashta {' '.join(args)}", flush=True)
    code = subprocess.call(cmd)  # noqa: S603 - fixed argv, no shell
    print("::endgroup::", flush=True)
    print(f"  exit {code}", flush=True)
    return code


def counts(db: str) -> dict[str, int]:
    con = sqlite3.connect(db)
    try:
        out = {}
        for t in ("sources", "articles", "chunks"):
            try:
                out[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # noqa: S608 - fixed names
            except sqlite3.OperationalError:
                out[t] = 0
        return out
    finally:
        con.close()


def state(db: str, key: str, value: str | None = None) -> str | None:
    con = sqlite3.connect(db)
    try:
        with con:
            con.execute(STATE)
            if value is not None:
                con.execute("INSERT OR REPLACE INTO collector_state VALUES (?, ?)", (key, value))
                return value
        row = con.execute("SELECT value FROM collector_state WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None
    finally:
        con.close()


def merge_seed(db: str, seed: str) -> None:
    """Copy rows from an older store that this one lacks. Rows already
    here win, so merging the same seed twice changes nothing."""
    before = counts(db)
    con = sqlite3.connect(db)
    try:
        con.execute("ATTACH DATABASE ? AS seed", (seed,))
        have = {r[0] for r in con.execute("SELECT name FROM seed.sqlite_master WHERE type = 'table'")}
        with con:
            for table, cols in MERGE.items():
                if table in have:
                    con.execute(f"INSERT OR IGNORE INTO main.{table} ({cols}) SELECT {cols} FROM seed.{table}")  # noqa: S608
        con.execute("DETACH DATABASE seed")
    finally:
        con.close()
    after = counts(db)
    print(f"seed merged: articles {before['articles']} -> {after['articles']}", flush=True)


def backfill(db: str, start: date, deadline: float) -> None:
    done = state(db, "backfilled_until")
    cursor = date.fromisoformat(done) + timedelta(days=1) if done else start
    today = datetime.now(NEPAL).date()
    while cursor <= today:
        if time.monotonic() > deadline:
            print(f"backfill paused at {cursor}; the next run carries on", flush=True)
            return
        end = min(cursor + timedelta(days=WINDOW_DAYS - 1), today)
        if spashta(db, "ingest", "--since", cursor.isoformat(), "--until", end.isoformat(), "--workers", "6") != 0:
            print(f"::warning::every outlet failed backfilling {cursor}; trying this window again next run", flush=True)
            return
        # A window counts as done even if one outlet failed: the daily
        # catch-up and the feeds keep covering recent days, and the summary
        # shows any outlet that is falling behind.
        if end < today:
            state(db, "backfilled_until", end.isoformat())
        cursor = end + timedelta(days=1)
    state(db, "backfilled_until", (today - timedelta(days=1)).isoformat())


def summary(db: str, before: dict, after: dict) -> str:
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT s.name, s.url, s.kind, s.last_polled, s.last_error, "
            "(SELECT COUNT(*) FROM articles a WHERE a.source = s.url) AS n, "
            "(SELECT MAX(published) FROM articles a WHERE a.source = s.url) AS newest "
            "FROM sources s ORDER BY n DESC").fetchall()
    finally:
        con.close()
    lines = [
        "### Spashta collection",
        "",
        f"Articles: **{after['articles']}** ({after['articles'] - before['articles']:+d} this run) · "
        f"sources: {after['sources']} · backfilled until: {state(db, 'backfilled_until') or '-'}",
        "",
        "| Outlet | Articles | Newest | Kind | Last error |",
        "|---|---:|---|---|---|",
    ]
    for r in rows:
        err = (r["last_error"] or "").replace("|", "/")[:80]
        lines.append(f"| {r['name'] or r['url']} | {r['n']} | {(r['newest'] or '')[:10]} | {r['kind'] or ''} | {err} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("db")
    p.add_argument("--seed")
    p.add_argument("--budget-min", type=float, default=240)
    args = p.parse_args()
    deadline = time.monotonic() + args.budget_min * 60
    start = date.fromisoformat(os.environ.get("BACKFILL_FROM", "2026-09-26"))

    from spashta.store import Store
    Store(args.db).close()  # creates the tables in a new store
    before = counts(args.db)
    print(f"store before: {json.dumps(before)}", flush=True)
    if args.seed:
        merge_seed(args.db, args.seed)
    if counts(args.db)["sources"] == 0:
        spashta(args.db, "sources", "seed")

    backfill(args.db, start, deadline)
    poll = spashta(args.db, "ingest", "--workers", "6")

    today = datetime.now(NEPAL).date().isoformat()
    if state(args.db, "catchup_on") != today and time.monotonic() < deadline:
        spashta(args.db, "ingest", "--since", (datetime.now(NEPAL).date() - timedelta(days=2)).isoformat(),
                "--workers", "6")
        state(args.db, "catchup_on", today)

    con = sqlite3.connect(args.db)
    try:
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        ok = con.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        con.close()
    after = counts(args.db)
    print(f"store after: {json.dumps(after)}", flush=True)
    text = summary(args.db, before, after)
    print(text, flush=True)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(text)
    if ok != "ok":
        print(f"::error::integrity check failed: {ok}", flush=True)
        return 3
    if after["articles"] < before["articles"]:
        print(f"::error::store lost articles ({before['articles']} -> {after['articles']}); not saving it", flush=True)
        return 3
    if poll != 0:
        print("::warning::every outlet failed this poll; the store is saved, but check the outlets", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
