# Collecting without a computer left on

`.github/workflows/collect.yml` runs ingest every 3 hours on GitHub's
machines (free for public repositories). Each run:

1. downloads the store from the last run,
2. backfills from sitemaps, three days at a time, until everything from
   2026-09-26 (`BACKFILL_FROM` in the workflow) to today is covered; a
   backfill too long for one run carries on in the next,
3. polls every outlet's feed, and once a day re-reads the last two days
   of sitemaps to catch what the feeds missed,
4. checks the store (SQLite integrity, no lost articles) and saves it.

The store holds publishers' full text and the repository is public, so it
is saved only encrypted (AES-256, key in a repository secret) as an asset
of the `collector-data` release. The four newest saves are kept, so a bad
run can be rolled back by deleting its asset. The run page shows a
per-outlet table (articles, newest date, last error) and nothing else.

If a run fails, GitHub emails you. A run where every outlet failed still
saves the store and is marked failed, so outlet blocking shows up quickly.

## One-time setup

1. Make a key and keep a copy somewhere safe (a password manager). In
   PowerShell:

   ```powershell
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

2. Add it on GitHub: **Settings → Secrets and variables → Actions → New
   repository secret**, name `SPASHTA_STORE_KEY`, value the key.
3. **Actions → Collect corpus → Run workflow**. The first run creates the
   release and starts the backfill. It takes a few hours; later runs take
   a few minutes. Check the table on the run page: every outlet should
   have articles and a recent "Newest" date.

Without the secret the workflow does a dry run: two days into a
throwaway store, nothing saved. Use it to see which outlets answer.

## Seeding with an older local store (optional)

A `live.db` you collected at home can be merged in at any time; its rows
are added and nothing already collected is replaced. Stop any local
ingest first, then in Git Bash (comes with Git for Windows) from the
repository folder:

```bash
python -c "import sqlite3; sqlite3.connect('data/live.db').execute(\"VACUUM INTO 'data/seed.db'\")"
export SPASHTA_STORE_KEY='the key from step 1'
gzip -c data/seed.db | openssl enc -e -aes-256-cbc -pbkdf2 -iter 200000 -md sha256 -salt -pass env:SPASHTA_STORE_KEY -out seed.db.gz.enc
```

On GitHub open **Releases → Collector store (encrypted) → Edit**, attach
`seed.db.gz.enc` (the name must be exactly that), and update the release.
The next run merges it and deletes it. If the release does not exist yet,
run the workflow once first.

## Getting the store back (to freeze the snapshot)

From **Releases → Collector store (encrypted)**, download the newest
`live-….db.gz.enc`, then in Git Bash:

```bash
export SPASHTA_STORE_KEY='the key'
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -md sha256 -pass env:SPASHTA_STORE_KEY -in live-XXXX.db.gz.enc | gunzip > data/live.db
spashta --db data/live.db sources list
```

Then freeze as in [EVIDENCE.md](EVIDENCE.md) step 2. Once frozen, stop
collection with **Actions → Collect corpus → ⋯ → Disable workflow**.

## Limits worth knowing

- GitHub's runners are in US data centres. Most outlets serve them, but a
  site behind strict bot protection may refuse; the per-outlet table shows
  that as a last error and no new articles. If several outlets are
  blocked, use the VPS route in [DEPLOY.md](DEPLOY.md) instead.
- Scheduled runs can start up to an hour late when GitHub is busy. Polling
  every 3 hours with a daily sitemap catch-up leaves room for that.
- GitHub pauses schedules in a repository with no activity for 60 days.
- A run is capped at about 5½ hours; the backfill stops after 4 and
  resumes next time, so nothing is lost to the cap.
